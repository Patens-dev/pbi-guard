import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .models import AssertionResult, Column, Measure, SemanticModel
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


def _get_format_string(properties: Dict[str, str]) -> str:
    """Retrieves format string or format hint across case variations."""
    for k, v in properties.items():
        if k.lower() in ("formatstring", "format_string", "format"):
            return v
    # Check Power BI annotation format hints
    for k, v in properties.items():
        if "formathint" in k.lower() and "currency" in v.lower():
            return "currency"
    return ""


def _get_property(properties: Dict[str, str], target_key: str, default: str = "") -> str:
    """Case-insensitive property lookup."""
    target_clean = target_key.lower()
    for k, v in properties.items():
        if k.lower() == target_clean:
            return v
    return default


def _find_column_in_model(target: str, model: SemanticModel) -> Optional[str]:
    c_clean = clean_measure_name(target).lower()
    for t_name, cols in model.columns.items():
        for c_name in cols.keys():
            if c_name.lower() == c_clean:
                return f"{t_name}[{c_name}]"
    return None


def _resolve_measure_dependencies(
    measure_name: str,
    model: SemanticModel,
    visited: Optional[Set[str]] = None,
    transitive: bool = True,
) -> Set[str]:
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


# ==============================================================================
# 1. ARCHITECTURE & TABLE ASSERTIONS
# ==============================================================================

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


# ==============================================================================
# 2. COLUMN ASSERTIONS
# ==============================================================================

@register("column_naming", "column_forbidden_pattern")
def assert_column_naming(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Naming Rule")
    table_filter = rule.get("table") or rule.get("table_pattern", "*")
    forbid_prefixes = rule.get("forbid_prefixes", [])
    forbid_patterns = rule.get("forbid_patterns", [])

    violations = []
    for t_name, cols in model.columns.items():
        if match_wildcards(t_name, table_filter):
            for c_name in cols.keys():
                for p in forbid_prefixes:
                    if c_name.lower().startswith(p.lower()):
                        violations.append(f"{t_name}[{c_name}] starts with forbidden prefix '{p}'")
                for pat in forbid_patterns:
                    if match_wildcards(c_name, pat):
                        violations.append(f"{t_name}[{c_name}] matches forbidden pattern '{pat}'")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"No column names in '{table_filter}' starting with {forbid_prefixes}",
            actual=f"Violations: {', '.join(violations)}",
            rule_type="column_naming",
            target=table_filter,
        )
    return AssertionResult(name=name, passed=True, rule_type="column_naming", target=table_filter)


@register("column_exists")
def assert_column_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Exists Check")
    table_name = rule.get("table", "")
    raw_cols = rule.get("columns") or [rule.get("column", "")]
    target_cols = [clean_measure_name(c) for c in raw_cols if c]

    matching_tables = [t for t in model.tables if match_wildcards(t, table_name)]
    if not matching_tables:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Table '{table_name}' to exist",
            actual=f"Available tables: {', '.join(model.tables) or 'none'}",
            rule_type="column_exists",
            target=table_name,
        )

    missing = []
    for tbl in matching_tables:
        existing_cols = {c.lower(): c for c in model.columns.get(tbl, {}).keys()}
        for col in target_cols:
            if col.lower() not in existing_cols:
                missing.append(f"{tbl}[{col}]")

    if missing:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Column(s) {', '.join(target_cols)} to exist in '{table_name}'",
            actual=f"Missing: {', '.join(missing)}",
            rule_type="column_exists",
            target=table_name,
        )
    return AssertionResult(name=name, passed=True, rule_type="column_exists", target=table_name)


@register("column_rule", "column_property_equals")
def assert_column_rule(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Rule Check")
    table_filter = rule.get("table") or rule.get("table_pattern", "*")
    col_filter = rule.get("column") or rule.get("column_pattern", "*")

    summarize_by = rule.get("summarize_by") or rule.get("expected")
    is_hidden = rule.get("hidden") or rule.get("is_hidden")
    data_type = rule.get("data_type") or rule.get("dataType")

    violations = []
    matched_columns = 0

    for t_name, cols in model.columns.items():
        if match_wildcards(t_name, table_filter):
            for c_name, col in cols.items():
                if match_wildcards(c_name, col_filter):
                    matched_columns += 1
                    if summarize_by is not None:
                        actual_sb = _get_property(col.properties, "summarizeBy")
                        if actual_sb.lower() != str(summarize_by).lower():
                            violations.append(f"{t_name}[{c_name}] (summarizeBy='{actual_sb or 'default'}')")
                    if is_hidden is not None:
                        actual_hide = _get_property(col.properties, "isHidden", "false").lower() == "true"
                        if actual_hide != bool(is_hidden):
                            violations.append(f"{t_name}[{c_name}] (isHidden={actual_hide})")
                    if data_type is not None:
                        actual_dt = _get_property(col.properties, "dataType")
                        if actual_dt.lower() != str(data_type).lower():
                            violations.append(f"{t_name}[{c_name}] (dataType='{actual_dt or 'default'}')")

    if matched_columns == 0:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Target column(s) matching '{table_filter}[{col_filter}]'",
            actual="No matching columns found in model",
            rule_type="column_rule",
            target=f"{table_filter}[{col_filter}]",
        )

    if violations:
        expected_desc = []
        if summarize_by is not None:
            expected_desc.append(f"summarizeBy='{summarize_by}'")
        if is_hidden is not None:
            expected_desc.append(f"isHidden={is_hidden}")
        if data_type is not None:
            expected_desc.append(f"dataType='{data_type}'")

        return AssertionResult(
            name=name,
            passed=False,
            expected=", ".join(expected_desc),
            actual=f"Violations: {', '.join(violations)}",
            rule_type="column_rule",
            target=f"{table_filter}[{col_filter}]",
        )
    return AssertionResult(name=name, passed=True, rule_type="column_rule", target=f"{table_filter}[{col_filter}]")


@register("column_format")
def assert_column_format(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Column Format Check")
    table_filter = rule.get("table") or rule.get("table_pattern", "*")
    col_filter = rule.get("column") or rule.get("columns") or rule.get("columns_matching", "*")
    fmt_type = str(rule.get("format", "")).lower()

    target_cols: List[Tuple[str, str, Column]] = []
    for t_name, cols in model.columns.items():
        if match_wildcards(t_name, table_filter):
            for c_name, col in cols.items():
                if match_wildcards(c_name, col_filter):
                    target_cols.append((t_name, c_name, col))

    if not target_cols:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching column(s) for '{table_filter}[{col_filter}]'",
            actual="No matching columns found in model",
            rule_type="column_format",
            target=f"{table_filter}[{col_filter}]",
        )

    violations = []
    for t_name, c_name, col in target_cols:
        actual_fmt = _get_format_string(col.properties)
        if fmt_type == "currency":
            is_currency = (
                any(sym in actual_fmt for sym in ["$", "€", "£", "¥", "₹", "#,##"])
                or "currency" in actual_fmt.lower()
            )
            if not is_currency:
                violations.append(f"{t_name}[{c_name}] (formatString='{actual_fmt or 'none'}')")
        elif fmt_type in ["percentage", "percent"]:
            if "%" not in actual_fmt and "percent" not in actual_fmt.lower():
                violations.append(f"{t_name}[{c_name}] (formatString='{actual_fmt or 'none'}')")
        else:
            if fmt_type not in actual_fmt.lower():
                violations.append(f"{t_name}[{c_name}] (formatString='{actual_fmt or 'none'}')")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"format: '{fmt_type}'",
            actual=f"Violations: {', '.join(violations)}",
            rule_type="column_format",
            target=f"{table_filter}[{col_filter}]",
        )
    return AssertionResult(name=name, passed=True, rule_type="column_format", target=f"{table_filter}[{col_filter}]")


# ==============================================================================
# 3. DAX LOGIC, CONTRACT PINNING & LINEAGE ASSERTIONS
# ==============================================================================

@register("dax_exact")
def assert_dax_exact(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Exact Contract Match")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)
    expected_dax = rule.get("expected", "")

    if target not in model.measures:
        col_hint = _find_column_in_model(target, model)
        extra_msg = f" Note: '{target}' exists as column '{col_hint}', not as a DAX measure." if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measure [{target}] to exist",
            actual=f"Measure not found in model.{extra_msg}",
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


@register("dax_dependency_chain", "measure_dependency_chain")
def assert_dax_dependency_chain(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Dependency Chain Check")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)

    if target not in model.measures:
        col_hint = _find_column_in_model(target, model)
        extra_msg = f" Note: '{target}' exists as column '{col_hint}', not as a DAX measure." if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measure [{target}] to exist in model",
            actual=f"Measure not found.{extra_msg}",
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
        col_hint = _find_column_in_model(str(target), model) if target else None
        extra_msg = f" (target '{target}' exists as column '{col_hint}', not a measure)" if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target or pattern_filter}'",
            actual=f"No matching measures found in model{extra_msg}",
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


@register("measure_exists")
def assert_measure_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Exists Check")
    raw_target = rule.get("measure", "")
    target = clean_measure_name(raw_target)

    if target in model.measures:
        return AssertionResult(name=name, passed=True, rule_type="measure_exists", target=f"[{target}]")

    col_hint = _find_column_in_model(target, model)
    extra_msg = f" Note: '[{target}]' was found as a COLUMN in table '{col_hint.split('[')[0]}', but is not a DAX measure." if col_hint else ""

    available = ", ".join(f"[{m}]" for m in sorted(model.measures.keys())) or "none"
    return AssertionResult(
        name=name,
        passed=False,
        expected=f"Measure [{target}] to exist",
        actual=f"Available measures: {available}.{extra_msg}",
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
        col_hint = _find_column_in_model(str(target), model) if target else None
        extra_msg = f" (target '{target}' exists as column '{col_hint}', not a measure)" if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target or pattern_filter}'",
            actual=f"No matching measures found in model{extra_msg}",
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


# ==============================================================================
# 4. GOVERNANCE & FORMATTING ASSERTIONS
# ==============================================================================

@register("measure_format")
def assert_measure_format(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Format Check")
    target = rule.get("measure")
    targets_list = rule.get("measures") or ([target] if target else None)
    pattern_filter = rule.get("measures_matching")

    matched_measures: List[Measure] = []
    missing_targets: List[str] = []

    if targets_list:
        for t in targets_list:
            c = clean_measure_name(t)
            if c in model.measures:
                matched_measures.append(model.measures[c])
            else:
                col_hint = _find_column_in_model(c, model)
                if col_hint:
                    missing_targets.append(f"[{c}] (exists as column '{col_hint}', not as a measure)")
                else:
                    missing_targets.append(f"[{c}]")
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                matched_measures.append(m)

    if missing_targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Target measure(s) to exist: {', '.join(targets_list)}",
            actual=f"Missing measure(s): {', '.join(missing_targets)}",
            rule_type="measure_format",
            target=str(targets_list),
        )

    if not matched_measures and pattern_filter:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measures matching pattern '{pattern_filter}'",
            actual="No matching measures found in model",
            rule_type="measure_format",
            target=str(pattern_filter),
        )

    fmt_type = str(rule.get("format", "")).lower()
    violations = []
    for m in matched_measures:
        actual_fmt = _get_format_string(m.properties)
        if fmt_type == "currency":
            is_currency = (
                any(sym in actual_fmt for sym in ["$", "€", "£", "¥", "₹", "#,##"])
                or "currency" in actual_fmt.lower()
            )
            if not is_currency:
                violations.append(f"[{m.name}] (formatString='{actual_fmt or 'none'}')")
        elif fmt_type in ["percentage", "percent"]:
            if "%" not in actual_fmt and "percent" not in actual_fmt.lower():
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
            target=str(targets_list or pattern_filter),
        )
    return AssertionResult(name=name, passed=True, rule_type="measure_format", target=str(targets_list or pattern_filter))


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