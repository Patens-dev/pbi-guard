import re
from typing import Any, Callable, Dict, List, Optional, Set

from .models import AssertionResult, Measure, SemanticModel
from .utils import (
    clean_measure_name,
    extract_column_references,
    match_wildcards,
    normalize_dax,
    strip_dax_comments_and_literals,
)

AssertionHandler = Callable[[SemanticModel, Dict[str, Any]], AssertionResult]
ASSERTION_REGISTRY: Dict[str, AssertionHandler] = {}


def register(*type_names: str):
    def decorator(func: AssertionHandler):
        for t in type_names:
            ASSERTION_REGISTRY[t] = func
        return func
    return decorator


def _resolve_measure_dependencies(
    measure_name: str,
    model: SemanticModel,
    visited: Optional[Set[str]] = None,
    transitive: bool = True,
) -> Set[str]:
    """Performs DAG traversal to resolve direct and transitive measure dependencies."""
    if visited is None:
        visited = set()

    c_name = clean_measure_name(measure_name)
    if c_name not in model.measures:
        return set()

    clean_dax = strip_dax_comments_and_literals(model.measures[c_name].expression)
    raw_refs = re.findall(r"\[([^\]]+)\]", clean_dax)

    known_map = {m.lower(): m for m in model.measures.keys()}
    direct_deps = set()
    for ref in raw_refs:
        ref_c = clean_measure_name(ref)
        if ref_c.lower() in known_map and ref_c.lower() != c_name.lower():
            direct_deps.add(known_map[ref_c.lower()])

    if not transitive:
        return direct_deps

    all_deps = set(direct_deps)
    for dep in direct_deps:
        if dep not in visited:
            visited.add(dep)
            sub = _resolve_measure_dependencies(dep, model, visited, transitive=True)
            all_deps.update(sub)

    return all_deps


@register("dax_dependency_chain", "measure_dependency_chain")
def assert_dax_dependency_chain(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Dependency Chain Check")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)

    if target not in model.measures:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measure [{target}] to exist in model",
            actual="Measure not found",
            rule_type="dax_dependency_chain",
            target=f"[{target}]",
        )

    direct_only = bool(rule.get("direct_only", False))
    resolved_deps = _resolve_measure_dependencies(target, model, transitive=not direct_only)

    must_depend = rule.get("must_depend_on") or rule.get("depends_on") or []
    if isinstance(must_depend, str):
        must_depend = [must_depend]

    forbid_deps = rule.get("forbid_dependencies") or rule.get("forbidden_dependencies") or []
    if isinstance(forbid_deps, str):
        forbid_deps = [forbid_deps]

    failures = []
    missing_deps = [
        f"[{clean_measure_name(d)}]"
        for d in must_depend
        if not any(clean_measure_name(d).lower() == r.lower() for r in resolved_deps)
    ]
    if missing_deps:
        failures.append(f"Missing required upstream dependency: {', '.join(missing_deps)}")

    forbidden_found = [
        f"[{clean_measure_name(d)}]"
        for d in forbid_deps
        if any(clean_measure_name(d).lower() == r.lower() for r in resolved_deps)
    ]
    if forbidden_found:
        failures.append(f"Contains forbidden upstream dependency: {', '.join(forbidden_found)}")

    if failures:
        actual_deps_str = ", ".join(f"[{d}]" for d in sorted(resolved_deps)) or "None (standalone measure)"
        mode_str = "direct" if direct_only else "transitive"
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"[{target}] to depend on: {', '.join(f'[{clean_measure_name(x)}]' for x in must_depend)}",
            actual=f"{' | '.join(failures)}\n         │ Resolved {mode_str} dependencies: {actual_deps_str}",
            rule_type="dax_dependency_chain",
            target=f"[{target}]",
        )

    return AssertionResult(name=name, passed=True, rule_type="dax_dependency_chain", target=f"[{target}]")


@register("column_usage_rule", "dax_column_rule", "column_guard")
def assert_column_usage_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Usage Check")
    target = rule.get("measure")
    pattern_filter = rule.get("measures_matching") or rule.get("measure_pattern")
    exclude_filter = rule.get("exclude_measures") or []

    targets: List[Measure] = []
    if target:
        c_name = clean_measure_name(target)
        if c_name in model.measures:
            targets.append(model.measures[c_name])
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                if not (exclude_filter and match_wildcards(m_name, exclude_filter)):
                    targets.append(m)

    if not targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target or pattern_filter}'",
            actual="No matching measures found in model",
            rule_type="column_usage_rule",
            target=str(target or pattern_filter),
        )

    forbid_cols = rule.get("forbid_column_references") or rule.get("forbid_columns") or []
    if isinstance(forbid_cols, str):
        forbid_cols = [forbid_cols]

    allowed_tables = rule.get("allowed_tables") or []
    if isinstance(allowed_tables, str):
        allowed_tables = [allowed_tables]

    must_ref_tables = rule.get("must_reference_tables") or rule.get("must_reference_table") or []
    if isinstance(must_ref_tables, str):
        must_ref_tables = [must_ref_tables]

    violations = []
    for m in targets:
        clean_dax = strip_dax_comments_and_literals(m.expression)
        col_refs = extract_column_references(m.expression)
        referenced_tables = {tbl for tbl, _ in col_refs}

        if forbid_cols:
            for tbl, col in col_refs:
                full_col = f"{tbl}[{col}]"
                for pat in forbid_cols:
                    if match_wildcards(full_col, pat.strip()) or match_wildcards(col, pat.strip()):
                        violations.append(
                            f"[{m.name}] references forbidden column '{full_col}'\n         │ DAX: {m.expression}"
                        )

        if allowed_tables:
            for tbl in referenced_tables:
                if not match_wildcards(tbl, allowed_tables):
                    violations.append(
                        f"[{m.name}] references column in unallowed table '{tbl}' (Allowed: {allowed_tables})\n         │ DAX: {m.expression}"
                    )

        if must_ref_tables:
            for req_tbl in must_ref_tables:
                table_found = any(match_wildcards(tbl, req_tbl) for tbl in referenced_tables) or bool(
                    re.search(rf"(?:'|\b){re.escape(req_tbl)}(?:'|\b)", clean_dax, re.IGNORECASE)
                )
                if not table_found:
                    violations.append(
                        f"[{m.name}] missing required table reference to '{req_tbl}'\n         │ DAX: {m.expression}"
                    )

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected="Measures adhere to column/table scoping governance",
            actual="\n".join(violations),
            rule_type="column_usage_rule",
            target=str(target or pattern_filter),
        )

    return AssertionResult(name=name, passed=True, rule_type="column_usage_rule", target=str(target or pattern_filter))


@register("dax_exact")
def assert_dax_exact(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Exact Contract Match")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)
    expected_dax = rule.get("expected", "")

    if target not in model.measures:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measure [{target}] to exist",
            actual="Measure not found in model",
            rule_type="dax_exact",
            target=f"[{target}]",
        )

    actual_norm = normalize_dax(model.measures[target].expression)
    expected_norm = normalize_dax(expected_dax)

    if actual_norm.lower() != expected_norm.lower():
        return AssertionResult(
            name=name,
            passed=False,
            expected=expected_dax,
            actual=model.measures[target].expression,
            rule_type="dax_exact",
            target=f"[{target}]",
        )

    return AssertionResult(name=name, passed=True, rule_type="dax_exact", target=f"[{target}]")


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
            rule_type="no_calculated_columns",
            target=table_filter,
        )
    return AssertionResult(name=name, passed=True, rule_type="no_calculated_columns", target=table_filter)


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
            rule_type="no_auto_date_tables",
        )
    return AssertionResult(name=name, passed=True, rule_type="no_auto_date_tables")


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
            rule_type="column_rule",
            target=f"{table_filter}[{col_filter}]",
        )
    return AssertionResult(name=name, passed=True, rule_type="column_rule", target=f"{table_filter}[{col_filter}]")


@register("measure_exists")
def assert_measure_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Exists Check")
    target = clean_measure_name(rule.get("measure", ""))

    if target in model.measures:
        return AssertionResult(name=name, passed=True, rule_type="measure_exists", target=f"[{target}]")

    available = ", ".join(f"[{m}]" for m in sorted(model.measures.keys())) or "none"
    return AssertionResult(
        name=name,
        passed=False,
        expected=f"Measure [{target}] to exist",
        actual=f"Available measures: {available}",
        rule_type="measure_exists",
        target=f"[{target}]",
    )


@register("dax_rule", "dax_must_contain", "dax_forbidden_pattern")
def assert_dax_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Rule Check")
    target = rule.get("measure")
    pattern_filter = rule.get("measures_matching") or rule.get("measure_pattern")
    exclude_measures = rule.get("exclude_measures") or []

    targets: List[Measure] = []
    if target:
        c_name = clean_measure_name(target)
        if c_name in model.measures:
            targets.append(model.measures[c_name])
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                if not (exclude_measures and match_wildcards(m_name, exclude_measures)):
                    targets.append(m)

    if not targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target or pattern_filter}'",
            actual="No matching measures found in model",
            rule_type="dax_rule",
            target=str(target or pattern_filter),
        )

    must_call = rule.get("must_call")
    forbid_op = rule.get("forbid_operator")
    references = rule.get("references")
    check_magic_nums = rule.get("forbid_raw_numeric_literals") or rule.get("forbid_magic_numbers")
    allowed_nums = rule.get("allowed_literals", [0, 1])

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

        if check_magic_nums:
            found_nums = re.findall(r"(?:[+\-*/=><]\s*|\b)(\d+(?:\.\d+)?)\b", clean_dax)
            unauthorized = [n for n in found_nums if float(n) not in [float(a) for a in allowed_nums]]
            if unauthorized:
                failures.append(
                    f"[{m.name}] contains hard-coded numeric literal(s): {', '.join(unauthorized)}\n         │ DAX: {m.expression}"
                )

    if failures:
        return AssertionResult(
            name=name,
            passed=False,
            expected="DAX best-practice contracts met",
            actual="\n".join(failures),
            rule_type="dax_rule",
            target=str(target or pattern_filter),
        )
    return AssertionResult(name=name, passed=True, rule_type="dax_rule", target=str(target or pattern_filter))


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
            rule_type="measure_format",
            target=str(target or pattern_filter),
        )
    return AssertionResult(name=name, passed=True, rule_type="measure_format", target=str(target or pattern_filter))


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
            rule_type="measure_naming",
        )
    return AssertionResult(name=name, passed=True, rule_type="measure_naming")