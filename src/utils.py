import fnmatch
import re
from typing import List, Union


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