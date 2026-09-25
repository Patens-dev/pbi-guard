import fnmatch
from pathlib import Path
import re
import subprocess
from typing import Dict, List, Set, Tuple, Union


def clean_measure_name(name: str) -> str:
    """Normalizes '[Total Sales]' or 'Total Sales' to 'Total Sales'."""
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1].strip()
    return name


def match_wildcards(value: str, patterns: Union[str, List[str]]) -> bool:
    """Case-insensitive wildcard check using '*' and '?' globbing."""
    if isinstance(patterns, str):
        patterns = [p.strip() for p in patterns.split(",") if p.strip()]
    val_lower = value.lower()
    return any(fnmatch.fnmatchcase(val_lower, p.lower()) for p in patterns)


def strip_dax_comments_and_literals(dax: str) -> str:
    """
    Removes //, --, /* */ comments and string literals
    to prevent false positives during static operator inspection.
    """
    dax = re.sub(r"(//|--).*$", "", dax, flags=re.MULTILINE)
    dax = re.sub(r"/\*.*?\*/", "", dax, flags=re.DOTALL)
    dax = re.sub(r'"(?:[^"\\]|\\.)*"', '""', dax)
    return dax


def normalize_dax(expression: str) -> str:
    """Removes comments and normalizes all whitespace for stable formula comparison."""
    clean = strip_dax_comments_and_literals(expression)
    return " ".join(clean.split()).strip()


def extract_column_references(dax: str) -> List[Tuple[str, str]]:
    """
    Extracts table and column references from a DAX string.
    Matches:
      'Table Name'[Column Name] -> ('Table Name', 'Column Name')
      TableName[Column Name]    -> ('TableName', 'Column Name')
    """
    clean = strip_dax_comments_and_literals(dax)
    pattern = re.compile(r"(?:'([^']+)'|([a-zA-Z_][a-zA-Z0-9_]*))\s*\[([^\]]+)\]")
    results = []
    for match in pattern.finditer(clean):
        tbl = match.group(1) or match.group(2)
        col = match.group(3).strip()
        results.append((tbl.strip(), col))
    return results


def extract_measure_references(dax: str, known_measures: Set[str]) -> List[str]:
    """
    Extracts referenced measure names from a DAX string by cross-referencing
    bracketed tokens with defined measures in the semantic model.
    """
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


def get_git_info(target_dir: Path) -> Dict[str, str]:
    """Retrieves current Git commit SHA and branch name if inside a git repository."""
    info = {"commit": "N/A (untracked)", "branch": "N/A"}
    cwd = target_dir if target_dir.is_dir() else target_dir.parent
    try:
        res_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=2,
        )
        if res_commit.returncode == 0:
            info["commit"] = res_commit.stdout.strip()

        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=2,
        )
        if res_branch.returncode == 0:
            info["branch"] = res_branch.stdout.strip()
    except Exception:
        pass
    return info