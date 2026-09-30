import ctypes
import os
from pathlib import Path
import re
import sys
from typing import List, Optional
from .utils import clean_measure_name  # Ensure it is exported from parser too
from .models import Column, Measure, SemanticModel

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
PROJECT_CACHE_FILE = Path(__file__).resolve().parent.parent / ".last_model"


def get_model_project_dir(target_path: Path) -> Path:
    target = target_path.resolve()
    if target.is_file():
        return target.parent
    if target.name.lower() == "definition":
        target = target.parent
    if target.name.endswith(".SemanticModel"):
        return target.parent
    return target


def resolve_tmdl_directory(target_path: Path) -> Path:
    target = target_path.resolve()
    if target.is_file() and target.suffix.lower() == ".pbip":
        candidates = list(target.parent.glob("*.SemanticModel/definition")) or list(
            target.parent.glob("*.SemanticModel"))
        if candidates:
            return candidates[0]

    if target.is_dir():
        if (target / "definition").exists():
            return target / "definition"
        sm_candidates = list(target.glob("*.SemanticModel/definition")) or list(target.glob("*.SemanticModel"))
        if sm_candidates:
            return sm_candidates[0]
        for sub in target.iterdir():
            if sub.is_dir() and sub.name.endswith(".SemanticModel"):
                return sub / "definition" if (sub / "definition").exists() else sub
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
    """Retrieves titles of running Power BI Desktop windows using pure Windows API (0ms, no CLI)."""
    titles = []
    if sys.platform != "win32":
        return titles

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
                title = buff.value
                if "Power BI Desktop" in title or "(Power BI Project)" in title:
                    titles.append(title)
        return True

    EnumWindows(EnumWindowsProc(foreach_window), 0)
    return titles


def detect_active_powerbi_model() -> Optional[Path]:
    """Auto-detects the open .pbip model from trace logs, window titles, or cached sessions."""
    pbip_pattern = re.compile(r"([a-zA-Z]:\\[^\x00\"\'\r\n\t<>|*?]+?\.pbip)", re.IGNORECASE)

    # 1. Search Power BI Desktop trace logs (decoding UTF-16 LE + UTF-8)
    trace_dirs: List[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    user_home = Path.home()

    trace_dirs.append(user_home / "Microsoft" / "Power BI Desktop Store App" / "Traces")
    if local_app_data:
        lad = Path(local_app_data)
        trace_dirs.append(lad / "Microsoft" / "Power BI Desktop" / "Traces")
        trace_dirs.append(lad / "Microsoft" / "Power BI Desktop Store App" / "Traces")
        pkg_dir = lad / "Packages"
        if pkg_dir.is_dir():
            for p in pkg_dir.glob("Microsoft.MicrosoftPowerBIDesktop*"):
                trace_dirs.append(p / "LocalCache" / "Local" / "Microsoft" / "Power BI Desktop" / "Traces")

    candidate_logs: List[Path] = []
    for td in trace_dirs:
        if td.is_dir():
            try:
                candidate_logs.extend(list(td.glob("**/*.log")))
            except OSError:
                pass

    candidate_logs.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)

    for log_file in candidate_logs[:8]:
        try:
            raw = log_file.read_bytes()
            # Strip UTF-16 null bytes so standard regex matches
            clean_text = raw.replace(b"\x00", b"").decode("utf-8", errors="ignore")
            matches = pbip_pattern.findall(clean_text)
            for m in reversed(matches):
                p = Path(m)
                if p.is_file() and p.suffix.lower() == ".pbip":
                    return p
        except Exception:
            continue

    # 2. Match active window title to report files on disk
    for title in _get_active_pbi_window_titles():
        # Title formats: "test • Last saved: Today..." or "test - Power BI Desktop"
        name_match = re.split(r"[\s•\-]", title)[0].strip()
        if name_match:
            # Check last cached path or recent directories
            if PROJECT_CACHE_FILE.is_file():
                try:
                    last_path = Path(PROJECT_CACHE_FILE.read_text(encoding="utf-8").strip())
                    if last_path.is_file() and last_path.stem.lower() == name_match.lower():
                        return last_path
                except Exception:
                    pass

    # 3. Check persistent cache from previous successful run
    if PROJECT_CACHE_FILE.is_file():
        try:
            cached = Path(PROJECT_CACHE_FILE.read_text(encoding="utf-8").strip())
            if cached.is_file() and cached.suffix.lower() == ".pbip":
                return cached
        except Exception:
            pass

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
                            # Stop if non-empty line has zero indentation
                            if nxt.strip() and not nxt.startswith((" ", "\t")):
                                i -= 1
                                break
                            # Stop if a new TMDL construct starts
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
