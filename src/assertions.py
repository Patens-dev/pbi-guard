import re
from typing import Any, Callable, Dict, List

from .models import AssertionResult, Measure, SemanticModel
from .utils import clean_measure_name, match_wildcards, strip_dax_comments_and_literals

AssertionHandler = Callable[[SemanticModel, Dict[str, Any]], AssertionResult]
ASSERTION_REGISTRY: Dict[str, AssertionHandler] = {}


def register(*type_names: str):
    def decorator(func: AssertionHandler):
        for t in type_names:
            ASSERTION_REGISTRY[t] = func
        return func
    return decorator


@register("no_calculated_columns", "no_calc_columns")
def assert_no_calculated_columns(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "No Calculated Columns")
    table_filter = rule.get("table") or rule.get("tables") or rule.get("table_pattern", "*")

    violations = [
        f"{t_name}[{c}]"
        for t_name, cols in model.calculated_columns.items()
        if match_wildcards(t_name, table_filter)
        for c in cols
    ]

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"0 calculated columns in '{table_filter}'",
            actual=f"Found {len(violations)}: {', '.join(violations)}",
        )
    return AssertionResult(name=name, passed=True)


@register("no_auto_date_tables")
def assert_no_auto_date_tables(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
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
def assert_column_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
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
def assert_measure_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Exists Check")
    target = clean_measure_name(rule.get("measure", ""))

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
def assert_dax_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Rule Check")
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

    must_call = rule.get("must_call")
    forbid_op = rule.get("forbid_operator")
    references = rule.get("references")
    failures = []

    for m in targets:
        clean_dax = strip_dax_comments_and_literals(m.expression)

        if must_call:
            funcs = [must_call] if isinstance(must_call, str) else must_call
            for f in funcs:
                if not re.search(rf"\b{re.escape(f.strip())}\s*\(", clean_dax, re.IGNORECASE):
                    failures.append(f"[{m.name}] does not call '{f}()'\n         │ DAX: {m.expression}")

        if forbid_op == "/" and "/" in clean_dax:
            failures.append(f"[{m.name}] uses raw '/' division instead of DIVIDE()\n         │ DAX: {m.expression}")

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
def assert_measure_format(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
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
def assert_measure_naming(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Naming Rule")
    forbid_prefixes = rule.get("forbid_prefixes", [])

    violations = [
        f"[{m}] starts with forbidden prefix '{p}'"
        for m in model.measures.keys()
        for p in forbid_prefixes
        if m.lower().startswith(p.lower())
    ]

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"No measure names starting with {forbid_prefixes}",
            actual=f"Violations: {', '.join(violations)}",
        )
    return AssertionResult(name=name, passed=True)