#!/usr/bin/env python3
"""
pbi-guard: Power BI-native contract testing runner for TMDL models.
"""

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
import yaml


# ----------------------------------------------------------------------
# Terminal Formatter
# ----------------------------------------------------------------------

class TerminalFormatter:
    def __init__(self, enable_color: Optional[bool] = None):
        if enable_color is not None:
            self.enabled = enable_color
        else:
            is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
            self.enabled = is_tty and "NO_COLOR" not in os.environ

        self.GREEN = "\033[92m" if self.enabled else ""
        self.RED = "\033[91m" if self.enabled else ""
        self.YELLOW = "\033[93m" if self.enabled else ""
        self.BOLD = "\033[1m" if self.enabled else ""
        self.DIM = "\033[2m" if self.enabled else ""
        self.RESET = "\033[0m" if self.enabled else ""

    def pass_tag(self) -> str:
        return f"[{self.GREEN}PASS{self.RESET}]"

    def fail_tag(self) -> str:
        return f"[{self.RED}FAIL{self.RESET}]"

    def error_tag(self) -> str:
        return f"[{self.YELLOW}ERR {self.RESET}]"


fmt = TerminalFormatter()


# ----------------------------------------------------------------------
# Domain Models
# ----------------------------------------------------------------------

@dataclass
class Column:
    name: str
    table: str
    is_calculated: bool = False
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class Measure:
    name: str
    table: str
    expression: str
    file_path: Path
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class SemanticModel:
    tables: List[str] = field(default_factory=list)
    measures: Dict[str, Measure] = field(default_factory=dict)
    columns: Dict[str, Dict[str, Column]] = field(default_factory=dict)
    calculated_columns: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class AssertionResult:
    name: str
    passed: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    reason: Optional[str] = None
    is_error: bool = False


# ----------------------------------------------------------------------
# Helpers (Globbing & DAX Tokenization)
# ----------------------------------------------------------------------

def clean_measure_name(name: str) -> str:
    """Normalizes '[Total Sales]' or 'Total Sales' to 'Total Sales'."""
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1].strip()
    return name


def match_wildcards(value: str, patterns: Union[str, List[str]]) -> bool:
    """Case-insensitive wildcard check using '*' and '?'."""
    if isinstance(patterns, str):
        patterns = [p.strip() for p in patterns.split(",") if p.strip()]
    val_lower = value.lower()
    return any(fnmatch.fnmatchcase(val_lower, p.lower()) for p in patterns)


def strip_dax_comments_and_literals(dax: str) -> str:
    """Removes //, --, /* */ comments and string literals to prevent false positives."""
    dax = re.sub(r'(//|--).*$', '', dax, flags=re.MULTILINE)
    dax = re.sub(r'/\*.*?\*/', '', dax, flags=re.DOTALL)
    dax = re.sub(r'"(?:[^"\\]|\\.)*"', '""', dax)
    return dax


# ----------------------------------------------------------------------
# TMDL Parser
# ----------------------------------------------------------------------

def resolve_tmdl_directory(target_path: Path) -> Path:
    target = target_path.resolve()
    if target.is_file() and target.suffix.lower() == ".pbip":
        candidates = list(target.parent.glob("*.SemanticModel/definition"))
        if candidates:
            return candidates[0]
        candidates = list(target.parent.glob("*.SemanticModel"))
        if candidates:
            return candidates[0]
    if target.is_dir():
        if (target / "definition").exists():
            return target / "definition"
        return target
    return target


def parse_tmdl_directory(model_dir: Path) -> SemanticModel:
    resolved_dir = resolve_tmdl_directory(model_dir)
    tmdl_files = list(resolved_dir.glob("**/*.tmdl"))

    if not tmdl_files:
        raise FileNotFoundError(f"No .tmdl files found in '{resolved_dir}'.")

    model = SemanticModel()
    table_pattern = re.compile(r"^\s*table\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s\r\n]+))", re.MULTILINE)
    col_pattern = re.compile(r"^\s*column\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))(?:\s*=\s*(.*))?")
    measure_pattern = re.compile(r"^\s*measure\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))\s*=\s*(.*)")

    for file_path in tmdl_files:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        current_table: Optional[str] = None
        current_column: Optional[Column] = None
        current_measure: Optional[Measure] = None

        table_match = table_pattern.search(content)
        if table_match and "table " in lines[0]:
            groups = table_match.groups()
            current_table = (groups[0] or groups[1] or groups[2]).strip()
            if current_table not in model.tables:
                model.tables.append(current_table)
                model.columns[current_table] = {}
                model.calculated_columns[current_table] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if current_table:
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
                    elif remainder:
                        expr_lines.append(remainder)
                    else:
                        i += 1
                        while i < len(lines):
                            nxt = lines[i]
                            if nxt.strip() and not nxt.startswith((" ", "\t")):
                                i -= 1
                                break
                            if re.match(r"^\s*(measure|column|table|partition)\b", nxt) or \
                               re.match(r"^\s*(formatString|displayFolder|description|lineageTag)\s*:", nxt):
                                i -= 1
                                break
                            expr_lines.append(nxt.strip())
                            i += 1

                    full_expr = "\n".join(expr_lines).strip()
                    current_measure = Measure(
                        name=m_name, table=current_table, expression=full_expr, file_path=file_path
                    )
                    model.measures[m_name] = current_measure
                    current_column = None
                    i += 1
                    continue

                if current_column and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    current_column.properties[k.strip()] = v.strip()

                if current_measure and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    current_measure.properties[k.strip()] = v.strip().strip('"\'')

            i += 1

    return model


# ----------------------------------------------------------------------
# Power BI-Native Assertions
# ----------------------------------------------------------------------

AssertionHandler = Callable[[SemanticModel, Dict[str, Any]], AssertionResult]
ASSERTION_REGISTRY: Dict[str, AssertionHandler] = {}


def register(*type_names: str):
    def decorator(func: AssertionHandler):
        for t in type_names:
            ASSERTION_REGISTRY[t] = func
        return func
    return decorator


@register("no_calculated_columns", "no_calc_columns")
def _no_calculated_columns(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "No Calculated Columns")
    table_filter = rule.get("table") or rule.get("tables") or rule.get("table_pattern", "*")

    violations = []
    for t_name, cols in model.calculated_columns.items():
        if match_wildcards(t_name, table_filter) and cols:
            violations.extend([f"{t_name}[{c}]" for c in cols])

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"0 calculated columns in '{table_filter}'",
            actual=f"Found {len(violations)}: {', '.join(violations)}",
        )
    return AssertionResult(name=name, passed=True)


@register("no_auto_date_tables")
def _no_auto_date_tables(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Auto Date/Time Disabled")
    auto_tables = [t for t in model.tables if t.lower().startswith(("localdatetable_", "datetabletemplate_"))]
    if auto_tables:
        return AssertionResult(
            name=name,
            passed=False,
            expected="Auto Date/Time turned off in file options",
            actual=f"Found {len(auto_tables)} hidden local date table(s): {', '.join(auto_tables)}",
        )
    return AssertionResult(name=name, passed=True)


@register("column_rule", "column_property_equals")
def _column_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Rule Check")
    table_filter = rule.get("table") or rule.get("table_pattern", "*")
    col_filter = rule.get("column") or rule.get("column_pattern", "*")

    summarize_by = rule.get("summarize_by") or rule.get("expected")
    is_hidden = rule.get("hidden") or rule.get("is_hidden")

    violations = []
    for t_name, cols in model.columns.items():
        if match_wildcards(t_name, table_filter):
            for c_name, col in cols.items():
                if match_wildcards(c_name, col_filter):
                    if summarize_by is not None:
                        actual_sb = col.properties.get("summarizeBy", "").lower()
                        if actual_sb != str(summarize_by).lower():
                            violations.append(f"{t_name}[{c_name}] (summarizeBy='{actual_sb or 'default'}')")
                    if is_hidden is not None:
                        actual_hide = col.properties.get("isHidden", "false").lower() == "true"
                        if actual_hide != bool(is_hidden):
                            violations.append(f"{t_name}[{c_name}] (isHidden={actual_hide})")

    if violations:
        expected_desc = []
        if summarize_by is not None:
            expected_desc.append(f"summarizeBy='{summarize_by}'")
        if is_hidden is not None:
            expected_desc.append(f"isHidden={is_hidden}")

        return AssertionResult(
            name=name,
            passed=False,
            expected=", ".join(expected_desc),
            actual=f"Violations: {', '.join(violations)}",
        )
    return AssertionResult(name=name, passed=True)


@register("measure_exists")
def _measure_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Exists Check")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)

    if target in model.measures:
        return AssertionResult(name=name, passed=True)

    available = ", ".join(f"[{m}]" for m in sorted(model.measures.keys())) or "none"
    return AssertionResult(
        name=name,
        passed=False,
        expected=f"Measure [{target}] to exist",
        actual=f"Available measures: {available}",
    )


@register("dax_rule", "dax_must_contain", "dax_forbidden_pattern")
def _dax_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Rule Check")

    # Target measure resolution
    target = rule.get("measure")
    pattern_filter = rule.get("measures_matching") or rule.get("measure_pattern")

    targets: List[Measure] = []
    if target:
        c_name = clean_measure_name(target)
        if c_name in model.measures:
            targets.append(model.measures[c_name])
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                targets.append(m)

    if not targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target or pattern_filter}'",
            actual="No matching measures found in model",
        )

    # Specific checks
    must_call = rule.get("must_call")
    forbid_op = rule.get("forbid_operator")
    references = rule.get("references")

    failures = []

    for m in targets:
        clean_dax = strip_dax_comments_and_literals(m.expression)

        # 1. Check required function call: must_call: "DIVIDE"
        if must_call:
            funcs = [must_call] if isinstance(must_call, str) else must_call
            for f in funcs:
                if not re.search(rf"\b{re.escape(f.strip())}\s*\(", clean_dax, re.IGNORECASE):
                    failures.append(f"[{m.name}] does not call '{f}()'\n         │ DAX: {m.expression}")

        # 2. Check forbidden operator: forbid_operator: "/"
        if forbid_op == "/":
            if "/" in clean_dax:
                failures.append(f"[{m.name}] uses raw '/' division instead of DIVIDE()\n         │ DAX: {m.expression}")

        # 3. Check measure references: references: ["[Total Sales]", "[Total COGS]"]
        if references:
            refs = [references] if isinstance(references, str) else references
            for r in refs:
                clean_ref = clean_measure_name(r)
                if not re.search(rf"\[{re.escape(clean_ref)}\]", clean_dax, re.IGNORECASE):
                    failures.append(f"[{m.name}] is missing reference to [{clean_ref}]\n         │ DAX: {m.expression}")

    if failures:
        return AssertionResult(
            name=name,
            passed=False,
            expected="DAX best-practice contracts met",
            actual="\n".join(failures),
        )

    return AssertionResult(name=name, passed=True)


@register("measure_format")
def _measure_format(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Format Check")
    target = rule.get("measure")
    targets_list = rule.get("measures") or ([target] if target else None)
    pattern_filter = rule.get("measures_matching")

    matched_measures: List[Measure] = []
    if targets_list:
        for t in targets_list:
            c = clean_measure_name(t)
            if c in model.measures:
                matched_measures.append(model.measures[c])
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                matched_measures.append(m)

    fmt_type = str(rule.get("format", "")).lower()
    violations = []

    for m in matched_measures:
        actual_fmt = m.properties.get("formatString", "")

        if fmt_type == "currency":
            # Power BI currency strings contain currency symbols or standard thousand separators
            if not any(sym in actual_fmt for sym in ["$", "€", "£", "¥", "₹", "#,##"]):
                violations.append(f"[{m.name}] (formatString='{actual_fmt or 'none'}')")
        elif fmt_type in ["percentage", "percent"]:
            if "%" not in actual_fmt:
                violations.append(f"[{m.name}] (formatString='{actual_fmt or 'none'}')")
        else:
            if fmt_type not in actual_fmt.lower():
                violations.append(f"[{m.name}] (formatString='{actual_fmt or 'none'}')")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"format: '{fmt_type}'",
            actual=f"Violations: {', '.join(violations)}",
        )

    return AssertionResult(name=name, passed=True)


@register("measure_naming", "measure_forbidden_pattern")
def _measure_naming(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Naming Rule")
    forbid_prefixes = rule.get("forbid_prefixes", [])

    violations = []
    for m in model.measures.keys():
        for prefix in forbid_prefixes:
            if m.lower().startswith(prefix.lower()):
                violations.append(f"[{m}] starts with forbidden prefix '{prefix}'")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"No measure names starting with {forbid_prefixes}",
            actual=f"Violations: {', '.join(violations)}",
        )

    return AssertionResult(name=name, passed=True)


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

def print_diff(label: str, content: str, color: str):
    lines = content.splitlines()
    if len(lines) == 1:
        print(f"       {fmt.BOLD}{label}:{fmt.RESET} {color}{lines[0]}{fmt.RESET}")
    else:
        print(f"       {fmt.BOLD}{label}:{fmt.RESET}")
        for l in lines:
            print(f"         {color}│ {l}{fmt.RESET}")


def run_suite(model: SemanticModel, config: Dict[str, Any], verbose: bool = False) -> bool:
    assertions = config.get("assertions", [])
    if not assertions:
        print(f"{fmt.YELLOW}Warning:{fmt.RESET} No assertions defined.")
        return True

    print(f"\n{fmt.BOLD}Model Summary:{fmt.RESET} {len(model.tables)} table(s), {len(model.measures)} measure(s) parsed.\n")

    results: List[AssertionResult] = []
    for item in assertions:
        name = item.get("name", "Unnamed")
        t_type = item.get("type")
        handler = ASSERTION_REGISTRY.get(t_type)
        if not handler:
            results.append(AssertionResult(name=name, passed=False, is_error=True, reason=f"Unknown type '{t_type}'"))
            continue
        try:
            results.append(handler(model, item))
        except Exception as ex:
            results.append(AssertionResult(name=name, passed=False, is_error=True, reason=str(ex)))

    for res in results:
        if res.is_error:
            print(f"{fmt.error_tag()} {res.name}\n       └── {fmt.YELLOW}Error: {res.reason}{fmt.RESET}")
        elif res.passed:
            print(f"{fmt.pass_tag()} {res.name}")
            if verbose:
                print(f"       └── {fmt.GREEN}OK{fmt.RESET}")
        else:
            print(f"{fmt.fail_tag()} {res.name}")
            if res.expected:
                print_diff("Expected", res.expected, fmt.GREEN)
            if res.actual:
                print_diff("Actual  ", res.actual, fmt.RED)
            print()

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    failed_count = total - passed_count
    sc = fmt.GREEN if failed_count == 0 else fmt.RED
    print(f"\n{fmt.BOLD}Summary:{fmt.RESET} {sc}{passed_count}/{total} passed{fmt.RESET}, {failed_count} failed.\n")
    return failed_count == 0


def main():
    parser = argparse.ArgumentParser(prog="pbi-guard", description="Power BI TMDL Contract Runner")
    parser.add_argument("-c", "--config", type=Path, default=Path("pbi_tests.yml"))
    parser.add_argument("-m", "--model", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config_path = args.config.resolve()
    if not config_path.is_file():
        print(f"{fmt.RED}Error:{fmt.RESET} Config '{config_path}' not found.", file=sys.stderr)
        sys.exit(2)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    model_input = args.model or (config_path.parent / config.get("model_path", "."))
    try:
        model = parse_tmdl_directory(model_input)
        success = run_suite(model, config, verbose=args.verbose)
        sys.exit(0 if success else 1)
    except Exception as exc:
        print(f"{fmt.RED}Fatal Error:{fmt.RESET} {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()