"""Fast model detection, process inspection, and TMDL directory resolution."""

import ctypes
import os
from pathlib import Path
import re
import shutil
import sys
from typing import List, Optional, Set

from .models import Column, Measure, SemanticModel
from .utils import clean_measure_name, silent_subprocess_run

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Persistent cache in LocalAppData
_local_app = os.environ.get("LOCALAPPDATA")
if _local_app:
    PROJECT_CACHE_FILE = Path(_local_app) / "pbi-guard" / ".last_model"
else:
    PROJECT_CACHE_FILE = Path.home() / ".pbi_guard_last_model"


def get_model_project_dir(target_path: Path) -> Path:
    target = target_path.resolve()
    if target.is_file():
        return target.parent
    if target.name.lower() == "definition":
        return target.parent
    if target.name.endswith(".SemanticModel"):
        return target.parent
    return target


def resolve_tmdl_directory(target_path: Optional[Path]) -> Path:
    if not target_path:
        return Path("")
    target = Path(target_path).resolve()

    if target.is_file() and target.suffix.lower() == ".pbip":
        candidates = list(target.parent.glob("*.SemanticModel/definition")) or list(
            target.parent.glob("*.SemanticModel")
        )
        if candidates:
            return candidates[0]

    if target.is_dir():
        if (target / "definition").is_dir():
            return target / "definition"
        sm_candidates = list(target.glob("*.SemanticModel/definition")) or list(target.glob("*.SemanticModel"))
        if sm_candidates:
            return sm_candidates[0]
        for sub in target.iterdir():
            if sub.is_dir() and sub.name.endswith(".SemanticModel"):
                return sub / "definition" if (sub / "definition").is_dir() else sub

    return target


def find_local_model(start_dir: Optional[Path] = None) -> Optional[Path]:
    cwd = (start_dir or Path.cwd()).resolve()
    if (cwd / "definition").is_dir():
        return cwd / "definition"
    pbip_files = list(cwd.glob("*.pbip"))
    if pbip_files:
        return pbip_files[0]
    sm_dirs = list(cwd.glob("*.SemanticModel"))
    if sm_dirs:
        return sm_dirs[0]
    candidates = list(cwd.glob("*/*.SemanticModel")) + list(cwd.glob("*.pbip"))
    return candidates[0] if candidates else None


def _get_active_pbi_window_titles() -> List[str]:
    """Retrieves titles of running Power BI Desktop windows using pure Windows API in <5ms."""
    titles = []
    if sys.platform != "win32":
        return titles

    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        def foreach_window(hwnd, lParam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    t = buff.value
                    if "Power BI Desktop" in t or "(Power BI Project)" in t:
                        titles.append(t)
            return True

        EnumWindows(EnumWindowsProc(foreach_window), 0)
    except Exception:
        pass
    return titles


def _extract_project_names_from_windows() -> List[str]:
    names = []
    for title in _get_active_pbi_window_titles():
        clean = title.strip()
        for sep in [" • ", " - ", " (Power BI Project)"]:
            if sep in clean:
                clean = clean.split(sep)[0].strip()
        if clean and clean.lower() not in ("power bi desktop", "untitled"):
            names.append(clean)
    return names


def _get_pbi_process_command_lines() -> List[str]:
    """Inspects running PBIDesktop.exe processes for open .pbip arguments."""
    if sys.platform != "win32":
        return []
    res = silent_subprocess_run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"name = 'PBIDesktop.exe'\" | Select-Object -ExpandProperty CommandLine",
        ],
        timeout=2,
    )
    if res and res.returncode == 0 and res.stdout:
        return res.stdout.splitlines()
    return []


def suggest_nearby_models() -> List[Path]:
    """Discovers likely .pbip models without performing recursive full-drive scans."""
    suggested: Set[Path] = set()

    # 1. Check last cached model
    if PROJECT_CACHE_FILE.is_file():
        try:
            cached = Path(PROJECT_CACHE_FILE.read_text(encoding="utf-8").strip())
            if cached.exists():
                suggested.add(cached)
        except Exception:
            pass

    # 2. Check Windows Recent items
    app_data = os.environ.get("APPDATA")
    if app_data:
        recent_dir = Path(app_data) / "Microsoft" / "Windows" / "Recent"
        if recent_dir.is_dir():
            pbip_regex = re.compile(rb"([a-zA-Z]:\\[^\x00\"\'\r\n\t<>|*?]+?\.pbip)", re.IGNORECASE)
            for lnk in list(recent_dir.glob("*.lnk"))[:40]:
                try:
                    content = lnk.read_bytes()
                    for m in pbip_regex.findall(content):
                        p = Path(m.decode("utf-8", errors="ignore"))
                        if p.is_file():
                            suggested.add(p)
                except Exception:
                    continue

    # 3. Check shallow developer paths (depth <= 2)
    search_roots = [
        Path.cwd(),
        Path(os.environ.get("USERPROFILE", "")),
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("USERPROFILE", "")) / "Documents",
        Path("D:\\dev") if Path("D:\\dev").is_dir() else None,
        Path("C:\\dev") if Path("C:\\dev").is_dir() else None,
    ]

    for root in filter(None, search_roots):
        if not root.is_dir():
            continue
        try:
            for p in list(root.glob("*.pbip")) + list(root.glob("*/*.pbip")):
                suggested.add(p)
        except (PermissionError, OSError):
            continue

    return sorted(list(suggested), key=lambda x: str(x))


def detect_active_powerbi_model() -> Optional[Path]:
    """Fast auto-detection of the active model using processes, window titles, and logs."""
    pbip_pattern = re.compile(r"([a-zA-Z]:\\[^\x00\"\'\r\n\t<>|*?]+?\.pbip)", re.IGNORECASE)

    # 1. Check command line of running PBIDesktop.exe processes
    for line in _get_pbi_process_command_lines():
        matches = pbip_pattern.findall(line)
        for m in matches:
            p = Path(m)
            if p.is_file():
                return p

    # 2. Extract active window title name
    active_names = _extract_project_names_from_windows()

    # 3. Check cached path if it matches the current window title
    if PROJECT_CACHE_FILE.is_file():
        try:
            cached = Path(PROJECT_CACHE_FILE.read_text(encoding="utf-8").strip())
            if cached.exists():
                if not active_names or any(name.lower() in cached.name.lower() for name in active_names):
                    return cached
        except Exception:
            pass

    # 4. Search recent items for the window name
    if active_names:
        for p in suggest_nearby_models():
            if any(name.lower() in p.stem.lower() for name in active_names):
                return p

    # 5. Check trace logs (latest 5 logs)
    local_app_data = os.environ.get("LOCALAPPDATA")
    trace_dirs = []
    if local_app_data:
        lad = Path(local_app_data)
        trace_dirs.extend([
            lad / "Microsoft" / "Power BI Desktop" / "Traces",
            lad / "Microsoft" / "Power BI Desktop Store App" / "Traces",
        ])

    for td in trace_dirs:
        if td.is_dir():
            try:
                logs = sorted(td.glob("*.log"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
                for log_file in logs[:5]:
                    raw = log_file.read_bytes()
                    clean_text = raw.replace(b"\x00", b"").decode("utf-8", errors="ignore")
                    matches = pbip_pattern.findall(clean_text)
                    for m in reversed(matches):
                        p = Path(m)
                        if p.is_file():
                            return p
            except Exception:
                continue

    return find_local_model()


def parse_tmdl_directory(model_dir: Path) -> SemanticModel:
    resolved_dir = resolve_tmdl_directory(model_dir)
    tmdl_files = list(resolved_dir.glob("**/*.tmdl"))

    if not tmdl_files:
        raise FileNotFoundError(f"No .tmdl files found in '{resolved_dir}'.")

    model = SemanticModel()
    table_pattern = re.compile(r"^\s*table\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s\r\n]+))")
    col_pattern = re.compile(r"^\s*column\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))(?:\s*=\s*(.*))?")
    measure_pattern = re.compile(r"^\s*measure\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))\s*=\s*(.*)")
    partition_pattern = re.compile(r"^\s*partition\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))")

    for file_path in tmdl_files:
        content = file_path.read_text(encoding="utf-8-sig")
        lines = content.splitlines()

        current_table: Optional[str] = None
        current_column: Optional[Column] = None
        current_measure: Optional[Measure] = None

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("///"):
                i += 1
                continue

            t_match = table_pattern.match(line)
            if t_match:
                g = t_match.groups()
                current_table = (g[0] or g[1] or g[2]).strip()
                if current_table not in model.tables:
                    model.tables.append(current_table)
                    model.columns[current_table] = {}
                    model.calculated_columns[current_table] = []
                current_column = None
                current_measure = None
                i += 1
                continue

            if current_table:
                if partition_pattern.match(line):
                    current_column = None
                    current_measure = None
                    i += 1
                    continue

                c_match = col_pattern.match(line)
                if c_match:
                    g = c_match.groups()
                    c_name = (g[0] or g[1] or g[2]).strip()
                    is_calc = c_match.group(4) is not None or "=" in line
                    current_column = Column(name=c_name, table=current_table, is_calculated=is_calc)
                    model.columns[current_table][c_name] = current_column
                    if is_calc:
                        model.calculated_columns[current_table].append(c_name)
                    current_measure = None
                    i += 1
                    continue

                m_match = measure_pattern.match(line)
                if m_match:
                    g = m_match.groups()
                    m_name = (g[0] or g[1] or g[2]).strip()
                    remainder = g[3].strip()

                    expr_lines = []
                    if remainder.startswith("```"):
                        expr_lines.append(remainder.lstrip("`"))
                        i += 1
                        while i < len(lines):
                            if "```" in lines[i]:
                                expr_lines.append(lines[i].split("```")[0])
                                break
                            expr_lines.append(lines[i])
                            i += 1
                    else:
                        if remainder:
                            expr_lines.append(remainder)
                        i += 1
                        while i < len(lines):
                            nxt = lines[i]
                            if nxt.strip() and not nxt.startswith((" ", "\t")):
                                i -= 1
                                break
                            if re.match(r"^\s*(measure|column|table|partition)\b", nxt) or \
                                    re.match(r"^\s*(formatString|displayFolder|description|lineageTag)\s*[:=]", nxt):
                                i -= 1
                                break
                            expr_lines.append(nxt.strip())
                            i += 1

                    full_expr = "\n".join(expr_lines).strip()
                    canonical_name = clean_measure_name(m_name)
                    current_measure = Measure(
                        name=canonical_name, table=current_table, expression=full_expr, file_path=file_path
                    )
                    model.measures[canonical_name] = current_measure
                    current_column = None
                    i += 1
                    continue

                separator = ":" if ":" in stripped else ("=" if "=" in stripped else None)
                if separator:
                    k, v = stripped.split(separator, 1)
                    prop_key = k.strip()
                    prop_val = v.strip().strip('"\'')
                    if current_column:
                        current_column.properties[prop_key] = prop_val
                    elif current_measure:
                        current_measure.properties[prop_key] = prop_val

            i += 1

    return model