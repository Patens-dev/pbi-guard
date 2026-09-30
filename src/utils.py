"""Utility functions, DAX text processing, and windowless execution helpers."""

import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Windows API constants for complete window suppression
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SW_HIDE = 0


def silent_subprocess_run(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 2,
) -> Optional[subprocess.CompletedProcess]:
    """
    Executes a child process with complete suppression of conhost, wt.exe,
    and cmd.exe console allocations on Windows.
    """
    kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "cwd": cwd,
        "timeout": timeout,
    }

    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
        kwargs["startupinfo"] = startupinfo

    # Resolve executable path to prevent cmd.exe invocation on .cmd/.bat wrappers
    if cmd and sys.platform == "win32":
        resolved_bin = shutil.which(cmd[0])
        if resolved_bin:
            cmd = [resolved_bin] + cmd[1:]

    try:
        return subprocess.run(cmd, **kwargs)
    except Exception:
        return None


def _find_git_root(start_path: Path) -> Optional[Path]:
    """Finds the enclosing .git directory by walking up directory ancestors."""
    curr = start_path.resolve()
    if curr.is_file():
        curr = curr.parent
    for parent in [curr] + list(curr.parents):
        git_dir = parent / ".git"
        if git_dir.exists():
            return git_dir
    return None


def _read_git_head_pure_python(git_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts Git branch and commit SHA purely from the filesystem in <1ms.
    Eliminates all subprocess executions and console window allocations on save.
    """
    try:
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return None, None

        head_content = head_file.read_text(encoding="utf-8").strip()

        if head_content.startswith("ref:"):
            ref_path_rel = head_content[4:].strip()
            branch = ref_path_rel.split("/")[-1]
            ref_file = git_dir / ref_path_rel

            if ref_file.is_file():
                commit = ref_file.read_text(encoding="utf-8").strip()[:7]
                return commit, branch

            # Check packed-refs fallback
            packed_file = git_dir / "packed-refs"
            if packed_file.is_file():
                for line in packed_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref_path_rel:
                        return parts[0][:7], branch

            return None, branch

        # Detached HEAD (raw commit hash)
        if len(head_content) >= 7:
            return head_content[:7], "detached"
    except Exception:
        pass

    return None, None


_GIT_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}


def get_git_info(target_dir: Path, ttl_seconds: float = 30.0) -> Dict[str, str]:
    """
    Retrieves Git commit SHA and branch name with zero window flash.
    Uses pure-Python filesystem parsing first, falling back to cached subprocess execution.
    """
    cwd = target_dir if target_dir.is_dir() else target_dir.parent
    cwd_str = str(cwd.resolve())
    now = time.time()

    if cwd_str in _GIT_CACHE:
        cached_time, cached_val = _GIT_CACHE[cwd_str]
        if now - cached_time < ttl_seconds:
            return cached_val

    info = {"commit": "N/A (untracked)", "branch": "N/A"}
    git_dir = _find_git_root(cwd)

    if not git_dir:
        _GIT_CACHE[cwd_str] = (now, info)
        return info

    # 1. Pure filesystem read (0ms, 0 processes)
    commit, branch = _read_git_head_pure_python(git_dir)
    if commit and branch:
        info["commit"] = commit
        info["branch"] = branch
        _GIT_CACHE[cwd_str] = (now, info)
        return info

    # 2. Windowless subprocess fallback
    res_commit = silent_subprocess_run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, timeout=2)
    if res_commit and res_commit.returncode == 0:
        info["commit"] = res_commit.stdout.strip()

    res_branch = silent_subprocess_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, timeout=2)
    if res_branch and res_branch.returncode == 0:
        info["branch"] = res_branch.stdout.strip()

    _GIT_CACHE[cwd_str] = (now, info)
    return info


def clean_measure_name(name: Any) -> str:
    """
    Normalizes any DAX measure identifier into its bare name.
    Handles 'Table'[Measure], Table[Measure], [[Measure]], and quoted identifiers.
    """
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""

    table_qual_match = re.match(r"^(?:'[^']+'|[\w]+)\s*\[(.*)\]$", s, flags=re.UNICODE)
    if table_qual_match:
        s = table_qual_match.group(1).strip()

    prev = None
    while prev != s:
        prev = s
        s = s.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1].strip()
        if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
            s = s[1:-1].strip()

    return s.strip()


def match_wildcards(value: str, patterns: Union[str, List[str]]) -> bool:
    """Case-insensitive wildcard check using '*' and '?' globbing."""
    if isinstance(patterns, str):
        patterns = [p.strip() for p in patterns.split(",") if p.strip()]
    val_lower = value.lower()
    return any(fnmatch.fnmatchcase(val_lower, p.lower()) for p in patterns)


def strip_dax_comments_and_literals(dax: str) -> str:
    """
    Removes comments and string literals using DAX syntax rules (escaped double-quotes "").
    """
    dax = re.sub(r"(//|--).*$", "", dax, flags=re.MULTILINE)
    dax = re.sub(r"/\*.*?\*/", "", dax, flags=re.DOTALL)
    # DAX strings are delimited by quotes and escaped via paired quotes ("")
    dax = re.sub(r'"(?:""|[^"])*"', '""', dax)
    return dax


def normalize_dax(expression: str) -> str:
    """Removes comments and normalizes whitespace for reliable formula comparison."""
    clean = strip_dax_comments_and_literals(expression)
    return " ".join(clean.split()).strip()


def extract_column_references(dax: str) -> List[Tuple[str, str]]:
    """Extracts table and column references from a DAX expression."""
    clean = strip_dax_comments_and_literals(dax)
    pattern = re.compile(r"(?:'([^']+)'|([\w]+))\s*\[([^\]]+)\]", flags=re.UNICODE)
    results = []
    for match in pattern.finditer(clean):
        tbl = match.group(1) or match.group(2)
        col = match.group(3).strip()
        results.append((tbl.strip(), col))
    return results


def extract_measure_references(dax: str, known_measures: Set[str]) -> List[str]:
    """Extracts referenced measure names from a DAX string against known measures."""
    clean = strip_dax_comments_and_literals(dax)
    candidates = re.findall(r"\[([^\]]+)\]", clean)
    known_map = {m.lower(): m for m in known_measures}
    found = []
    for cand in candidates:
        cand_clean = clean_measure_name(cand)
        if cand_clean.lower() in known_map:
            canonical = known_map[cand_clean.lower()]
            if canonical not in found:
                found.append(canonical)
    return found