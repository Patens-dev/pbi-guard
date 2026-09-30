"""Local real-time Web Dashboard and SSE server for PBI Guard."""

from datetime import datetime
from difflib import unified_diff
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional
import yaml

from .logger import get_recent_logs, log
from .models import SemanticModel
from .parser import (
    CREATE_NO_WINDOW,
    PROJECT_CACHE_FILE,
    detect_active_powerbi_model,
    find_local_model,
    get_model_project_dir,
    parse_tmdl_directory,
    resolve_tmdl_directory,
)
from .runner import run_suite
from .snapshot import freeze_model_baseline
from .utils import clean_measure_name

PORT = 8765
HOST = "127.0.0.1"
HTML_FILE_PATH = Path(__file__).resolve().parent / "index.html"


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _scan_monitored_timestamps(tmdl_dir: Optional[Path], lock_file: Optional[Path], tests_file: Optional[Path]) -> dict:
    timestamps = {}
    if tmdl_dir and tmdl_dir.is_dir():
        try:
            for p in tmdl_dir.glob("**/*.tmdl"):
                try:
                    timestamps[str(p.resolve())] = p.stat().st_mtime_ns
                except OSError:
                    pass
        except OSError:
            pass

    for extra_file in (lock_file, tests_file):
        if extra_file and extra_file.is_file():
            try:
                timestamps[str(extra_file.resolve())] = extra_file.stat().st_mtime_ns
            except OSError:
                pass

    return timestamps


class AppState:
    def __init__(self, initial_model: Path | None = None):
        self.lock = threading.Lock()
        self.subscriber_queues: list[queue.Queue] = []
        self.model_path: Path | None = None
        self.resolved_tmdl: Path | None = None
        self.project_root: Path | None = None
        self.lock_file: Path | None = None
        self.tests_file: Path | None = None
        self.last_timestamps: dict = {}
        self.previous_model: SemanticModel | None = None

        self.latest_payload: dict = {
            "status": "idle",
            "model_name": "Scanning...",
            "model_path": "",
            "measures_count": 0,
            "model_measures": [],
            "passed": 0,
            "failed": 0,
            "total_assertions": 0,
            "failed_results": [],
            "passed_results": [],
            "uncertified_measures": [],
            "last_change": None,
            "last_check": "",
            "active_tmdl_snippet": "",
            "active_lock_yaml": "",
            "active_tests_yaml": "",
            "governance_rules": [],
        }

        target = initial_model or detect_active_powerbi_model() or find_local_model()
        if target:
            self.set_model(target)

    def set_model(self, target_path: Path):
        with self.lock:
            try:
                self.model_path = target_path
                self.resolved_tmdl = resolve_tmdl_directory(target_path)
                self.project_root = get_model_project_dir(target_path)
                self.lock_file = self.project_root / "pbi-guard.lock.yml"

                possible_test_files = [
                    self.project_root / "pbi_tests.yml",
                    self.project_root / "pbi_tests.yaml",
                    Path.cwd() / "pbi_tests.yml",
                    Path(__file__).resolve().parent.parent / "pbi_tests.yml",
                ]
                self.tests_file = next((f for f in possible_test_files if f.is_file()),
                                       self.project_root / "pbi_tests.yml")

                self.last_timestamps = _scan_monitored_timestamps(self.resolved_tmdl, self.lock_file, self.tests_file)
                PROJECT_CACHE_FILE.write_text(str(target_path.resolve()), encoding="utf-8")
                log.info(f"Active model bound to: {self.project_root.name} ({self.resolved_tmdl})")

                if not self.lock_file.is_file():
                    log.info("Generating initial lockfile baseline...")
                    freeze_model_baseline(self.resolved_tmdl, self.lock_file)
            except Exception as e:
                log.exception(f"Failed to set model: {e}")
                self.latest_payload["status"] = "error"
                self.latest_payload["error_msg"] = str(e)

        self.run_verification(save_summary=None)

    def compute_model_diff(self, new_model: SemanticModel) -> dict:
        if self.previous_model is None:
            return {"has_changes": False, "summary": "Baseline loaded", "details": []}

        old_measures = self.previous_model.measures
        new_measures = new_model.measures

        added = [m for m in new_measures if m not in old_measures]
        removed = [m for m in old_measures if m not in new_measures]
        modified = []

        for m_name in new_measures:
            if m_name in old_measures:
                old_exp = (old_measures[m_name].expression or "").strip()
                new_exp = (new_measures[m_name].expression or "").strip()
                old_fmt = old_measures[m_name].properties.get("formatString", "")
                new_fmt = new_measures[m_name].properties.get("formatString", "")

                if old_exp != new_exp or old_fmt != new_fmt:
                    diff_lines = list(
                        unified_diff(
                            old_exp.splitlines(),
                            new_exp.splitlines(),
                            fromfile="Previous",
                            tofile="Saved",
                            lineterm="",
                        )
                    )
                    modified.append({
                        "name": m_name,
                        "old_expression": old_exp,
                        "new_expression": new_exp,
                        "diff": "\n".join(diff_lines),
                    })

        has_changes = bool(added or removed or modified)
        summary_parts = []
        if modified:
            summary_parts.append(
                f"Modified {len(modified)} measure(s): " + ", ".join(f"[{m['name']}]" for m in modified[:3]))
        if added:
            summary_parts.append(f"Added {len(added)} measure(s): " + ", ".join(f"[{m}]" for m in added[:3]))
        if removed:
            summary_parts.append(f"Removed {len(removed)} measure(s): " + ", ".join(f"[{m}]" for m in removed[:3]))

        return {
            "has_changes": has_changes,
            "summary": "; ".join(summary_parts) if summary_parts else "No logic drift detected",
            "modified": modified,
            "added": added,
            "removed": removed,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }

    def run_verification(self, save_summary: dict | None = None):
        with self.lock:
            if not self.resolved_tmdl or not self.lock_file:
                return

            try:
                raw_lock_yaml = ""
                if self.lock_file.is_file():
                    raw_lock_yaml = self.lock_file.read_text(encoding="utf-8")
                lock_config = yaml.safe_load(raw_lock_yaml) if raw_lock_yaml else {}
                combined_assertions = list(lock_config.get("assertions", []))

                raw_tests_yaml = ""
                test_rules = []
                if self.tests_file and self.tests_file.is_file():
                    raw_tests_yaml = self.tests_file.read_text(encoding="utf-8")
                    test_config = yaml.safe_load(raw_tests_yaml) if raw_tests_yaml else {}
                    test_rules = test_config.get("assertions", [])
                    combined_assertions.extend(test_rules)

                combined_config = {"version": 1, "assertions": combined_assertions}
                new_model = parse_tmdl_directory(self.resolved_tmdl)

                if save_summary is None:
                    save_diff = self.compute_model_diff(new_model)
                else:
                    save_diff = save_summary

                self.previous_model = new_model

                t_start = time.perf_counter()
                success, results = run_suite(new_model, combined_config, verbose=False)
                t_elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)

                failed_list = []
                passed_list = []

                for r in results:
                    target_clean = clean_measure_name(str(r.target or r.name or "")).strip()

                    if r.rule_type == "dax_exact":
                        category = "drift"
                    elif r.rule_type in ("dax_rule", "dax_dependency_chain", "column_usage_rule"):
                        category = "policy"
                    elif r.rule_type in ("measure_format", "column_format", "measure_naming", "column_naming"):
                        category = "governance"
                    else:
                        category = "architecture"

                    item = {
                        "name": r.name,
                        "target": target_clean,
                        "rule_type": r.rule_type or "contract",
                        "category": category,
                        "passed": bool(r.passed and not r.is_error),
                        "expected": r.expected or "",
                        "actual": r.actual or "",
                        "reason": r.reason or "",
                    }
                    if item["passed"]:
                        passed_list.append(item)
                    else:
                        failed_list.append(item)

                baseline_measures = {
                    clean_measure_name(a.get("measure")) for a in combined_assertions if a.get("measure")
                }
                uncertified = [
                    {"name": clean_measure_name(m.name), "expression": m.expression}
                    for m in new_model.measures.values()
                    if clean_measure_name(m.name) not in baseline_measures
                ]

                # Collect TMDL files and pick the most relevant file to preview
                tmdl_files_map = {}
                primary_tmdl_name = ""
                primary_tmdl_content = ""

                try:
                    for p in sorted(self.resolved_tmdl.glob("**/*.tmdl")):
                        rel_path = p.relative_to(self.resolved_tmdl).as_posix()
                        try:
                            content = p.read_text(encoding="utf-8-sig")
                            tmdl_files_map[rel_path] = content
                        except Exception:
                            continue

                    failing_target = failed_list[0]["target"] if failed_list else None
                    if failing_target and failing_target in new_model.measures:
                        m_file = new_model.measures[failing_target].file_path
                        if m_file:
                            rel_m = m_file.relative_to(self.resolved_tmdl).as_posix()
                            if rel_m in tmdl_files_map:
                                primary_tmdl_name = rel_m

                    if not primary_tmdl_name:
                        for rel, txt in tmdl_files_map.items():
                            if "measure " in txt or rel.startswith("tables/"):
                                primary_tmdl_name = rel
                                break

                    if not primary_tmdl_name and tmdl_files_map:
                        primary_tmdl_name = next(iter(tmdl_files_map))

                    primary_tmdl_content = tmdl_files_map.get(primary_tmdl_name, "")
                except Exception as e:
                    log.warning(f"Error reading TMDL snippets: {e}")

                all_measures_clean = sorted([clean_measure_name(name) for name in new_model.measures.keys()])

                self.latest_payload = {
                    "status": "pass" if success else "fail",
                    "model_name": self.project_root.name,
                    "model_path": str(self.model_path.resolve()) if self.model_path else "",
                    "measures_count": len(new_model.measures),
                    "model_measures": all_measures_clean,
                    "passed": len(passed_list),
                    "failed": len(failed_list),
                    "total_assertions": len(results),
                    "elapsed_ms": t_elapsed_ms,
                    "failed_results": failed_list,
                    "passed_results": passed_list,
                    "uncertified_measures": uncertified,
                    "last_change": save_diff,
                    "last_check": datetime.now().strftime("%H:%M:%S"),
                    "active_tmdl_snippet": primary_tmdl_content,
                    "active_tmdl_file": primary_tmdl_name,
                    "tmdl_files": tmdl_files_map,
                    "active_lock_yaml": raw_lock_yaml[:2500],
                    "active_tests_yaml": raw_tests_yaml,
                    "governance_rules": test_rules,
                }
                log.info(
                    f"Verification complete: {len(passed_list)} passed, {len(failed_list)} failed ({t_elapsed_ms}ms)")
            except Exception as exc:
                log.exception(f"Verification failed with unhandled exception: {exc}")
                self.latest_payload["status"] = "error"
                self.latest_payload["error_msg"] = str(exc)

        self.broadcast()

    def freeze_baseline(self):
        with self.lock:
            if self.resolved_tmdl and self.lock_file:
                log.info("Updating baseline contracts lockfile...")
                freeze_model_baseline(self.resolved_tmdl, self.lock_file)
        self.run_verification(
            save_summary={
                "has_changes": True,
                "summary": "Baseline contracts updated.",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
        )

    def save_tests_yaml(self, raw_yaml_text: str):
        with self.lock:
            if not self.tests_file:
                if self.project_root:
                    self.tests_file = self.project_root / "pbi_tests.yml"
                else:
                    self.tests_file = Path.cwd() / "pbi_tests.yml"

            self.tests_file.write_text(raw_yaml_text, encoding="utf-8")
            log.info(f"Saved updated rules to {self.tests_file.name}")
        self.run_verification(
            save_summary={
                "has_changes": True,
                "summary": "Updated pbi_tests.yml governance rules.",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
        )

    def broadcast(self):
        msg = f"data: {json.dumps(self.latest_payload)}\n\n".encode("utf-8")
        with self.lock:
            for q in list(self.subscriber_queues):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


STATE: AppState = None
SERVER_INSTANCE: ThreadingHTTPServer = None


def _stop_server_worker():
    log.info("Shutdown requested. Stopping server...")
    time.sleep(0.2)
    os._exit(0)


def _restart_server_worker():
    """Shuts down socket listener cleanly and hands off to child process with clean argv."""
    global SERVER_INSTANCE
    log.info("Restart initiated. Closing server socket listener...")
    time.sleep(0.1)

    try:
        if SERVER_INSTANCE:
            SERVER_INSTANCE.server_close()
    except Exception as e:
        log.warning(f"Error closing socket during restart: {e}")

    time.sleep(0.3)

    python_exe = sys.executable
    clean_argv = [a for a in sys.argv if a != "--restarting"]

    child_env = os.environ.copy()
    child_env["PBI_GUARD_RESTART"] = "1"

    if sys.platform == "win32":
        import subprocess
        pythonw = Path(python_exe).with_name("pythonw.exe")
        exe = str(pythonw.resolve()) if pythonw.is_file() else python_exe
        log.info(f"Spawning child worker: {exe} {clean_argv}")
        subprocess.Popen(
            [exe] + clean_argv,
            env=child_env,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        import subprocess
        subprocess.Popen([python_exe] + clean_argv, env=child_env, close_fds=True)

    log.info("Parent process exiting cleanly.")
    os._exit(0)


class PBIGuardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug(f"{self.address_string()} - {format % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if HTML_FILE_PATH.is_file():
                self.wfile.write(HTML_FILE_PATH.read_bytes())
            else:
                self.wfile.write(b"<h1>index.html not found next to server.py</h1>")

        elif parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_q = queue.Queue(maxsize=32)
            with STATE.lock:
                STATE.subscriber_queues.append(client_q)
                initial_msg = f"data: {json.dumps(STATE.latest_payload)}\n\n".encode("utf-8")

            try:
                self.wfile.write(initial_msg)
                self.wfile.flush()

                while True:
                    try:
                        msg = client_q.get(timeout=15)
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with STATE.lock:
                    if client_q in STATE.subscriber_queues:
                        STATE.subscriber_queues.remove(client_q)

        elif parsed.path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"logs": get_recent_logs()}).encode("utf-8"))

        elif parsed.path == "/api/rules":
            rules_yaml = STATE.latest_payload.get("active_tests_yaml", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"yaml": rules_yaml}).encode("utf-8"))

        elif parsed.path == "/report":
            if STATE.project_root:
                rep = STATE.project_root / "pbi-guard-report.html"
                if rep.is_file():
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(rep.read_bytes())
                    return
            self.send_error(404, "Report not yet generated.")

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/freeze":
            STATE.freeze_baseline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        elif parsed.path == "/api/verify":
            STATE.run_verification()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        elif parsed.path == "/api/rules/parse":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req = json.loads(body.decode("utf-8"))
                parsed_data = yaml.safe_load(req.get("yaml", "")) or {}
                if not isinstance(parsed_data, dict):
                    parsed_data = {"version": 1, "assertions": []}
                if "assertions" not in parsed_data:
                    parsed_data["assertions"] = []
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "data": parsed_data}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

        elif parsed.path == "/api/rules/format":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req = json.loads(body.decode("utf-8"))
                data = req.get("data", {"version": 1, "assertions": []})
                formatted_yaml = yaml.dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "yaml": formatted_yaml}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

        elif parsed.path == "/api/rules/save":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
                new_yaml = payload.get("yaml", "")

                parsed_yaml = yaml.safe_load(new_yaml)
                if not isinstance(parsed_yaml, dict) or "assertions" not in parsed_yaml:
                    raise ValueError("YAML root must be a mapping containing an 'assertions:' list.")

                STATE.save_tests_yaml(new_yaml)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                log.warning(f"Rejected invalid rule YAML upload: {e}")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

        elif parsed.path == "/api/stop":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"stopping"}')
            threading.Thread(target=_stop_server_worker, daemon=True).start()

        elif parsed.path == "/api/restart":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"restarting"}')
            threading.Thread(target=_restart_server_worker, daemon=True).start()

        else:
            self.send_error(404)


def _background_file_watcher():
    POLL_INTERVAL = 0.5
    QUIET_SETTLE_SECONDS = 0.5

    while True:
        time.sleep(POLL_INTERVAL)
        if not STATE:
            continue

        if not STATE.resolved_tmdl:
            target = detect_active_powerbi_model() or find_local_model()
            if target:
                STATE.set_model(target)
            continue

        current_timestamps = _scan_monitored_timestamps(STATE.resolved_tmdl, STATE.lock_file, STATE.tests_file)

        has_delta = (
                set(current_timestamps.keys()) != set(STATE.last_timestamps.keys())
                or any(STATE.last_timestamps.get(p) != mtime for p, mtime in current_timestamps.items())
        )

        if has_delta:
            log.info("Filesystem change detected. Debouncing writes...")
            last_seen = current_timestamps
            while True:
                time.sleep(QUIET_SETTLE_SECONDS)
                new_scan = _scan_monitored_timestamps(STATE.resolved_tmdl, STATE.lock_file, STATE.tests_file)
                if new_scan == last_seen:
                    current_timestamps = new_scan
                    break
                last_seen = new_scan

            # Power BI file release grace period (prevents sharing violation)
            time.sleep(0.1)

            log.info("Writes settled. Executing background verification suite.")
            STATE.last_timestamps = current_timestamps

            # Safe verification with retry if Power BI is still unlocking files
            for attempt in range(3):
                try:
                    STATE.run_verification()
                    break
                except PermissionError:
                    time.sleep(0.2)
                except Exception as e:
                    log.warning(f"Verification attempt {attempt + 1} postponed: {e}")
                    time.sleep(0.2)


def launch_web_server(initial_model: Path | None = None, auto_open: bool = True):
    global STATE, SERVER_INSTANCE

    is_restarting = os.environ.get("PBI_GUARD_RESTART") == "1" or "--restarting" in sys.argv
    if is_restarting:
        log.info("Restart detected. Waiting for prior server instance to release port 8765...")
        for _ in range(40):
            if not is_port_in_use(PORT, HOST):
                break
            time.sleep(0.1)

    if is_port_in_use(PORT, HOST):
        log.warning(f"Port {PORT} already in use. Opening browser tab and terminating duplicate process.")
        if auto_open:
            webbrowser.open(f"http://{HOST}:{PORT}")
        sys.exit(0)

    ThreadingHTTPServer.allow_reuse_address = True
    STATE = AppState(initial_model=initial_model)
    SERVER_INSTANCE = ThreadingHTTPServer((HOST, PORT), PBIGuardRequestHandler)

    log.info(f"PBI Guard server listening on http://{HOST}:{PORT}")

    watcher_thread = threading.Thread(target=_background_file_watcher, daemon=True, name="WatcherThread")
    watcher_thread.start()

    if auto_open and not is_restarting:
        threading.Timer(0.3, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()

    try:
        SERVER_INSTANCE.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        log.info("Server loop terminated.")
        SERVER_INSTANCE.server_close()
