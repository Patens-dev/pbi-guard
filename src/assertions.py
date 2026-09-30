"""Semantic model assertions and rule evaluation engine."""

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


def _strip_dax_tokens_for_literal_scan(expression: str) -> str:
    """
    Strips comments, string literals, measure references [Measure], and table names 'Table'
    to ensure numeric scanner only checks raw scalar literals.
    """
    clean = strip_dax_comments_and_literals(expression)
    clean = re.sub(r"\[[^\]]*\]", " ", clean)  # Strip [Column / Measure] references
    clean = re.sub(r"'[^']*'", " ", clean)    # Strip 'Table Name' references
    return clean


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
            actual=f"{len(violations)} calculated column(s)",
            reason=f"Found: {', '.join(violations)}",
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
            expected="Auto Date/Time disabled in Power BI file options",
            actual=f"{len(auto_tables)} hidden local date table(s)",
            reason=f"Detected: {', '.join(auto_tables)}",
            rule_type="no_auto_date_tables",
            target="Model Architecture",
        )
    return AssertionResult(name=name, passed=True, rule_type="no_auto_date_tables", target="Model Architecture")


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
                        violations.append(f"{t_name}[{c_name}] has prefix '{p}'")
                for pat in forbid_patterns:
                    if match_wildcards(c_name, pat):
                        violations.append(f"{t_name}[{c_name}] matches '{pat}'")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"No columns matching forbidden prefixes/patterns in '{table_filter}'",
            actual=f"{len(violations)} non-conforming column(s)",
            reason="; ".join(violations),
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
            actual="Table missing from model",
            reason=f"Available tables: {', '.join(model.tables) or 'none'}",
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
            expected=f"Required columns exist in '{table_name}': {', '.join(target_cols)}",
            actual=f"Missing {len(missing)} column(s)",
            reason=f"Missing: {', '.join(missing)}",
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
                            violations.append(f"{t_name}[{c_name}] summarizeBy='{actual_sb or 'default'}'")
                    if is_hidden is not None:
                        actual_hide = _get_property(col.properties, "isHidden", "false").lower() == "true"
                        if actual_hide != bool(is_hidden):
                            violations.append(f"{t_name}[{c_name}] isHidden={actual_hide}")
                    if data_type is not None:
                        actual_dt = _get_property(col.properties, "dataType")
                        if actual_dt.lower() != str(data_type).lower():
                            violations.append(f"{t_name}[{c_name}] dataType='{actual_dt or 'default'}'")

    target_str = f"{table_filter}[{col_filter}]"
    if matched_columns == 0:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Column matching '{target_str}'",
            actual="Column not found",
            reason="No matching column was found in the semantic model",
            rule_type="column_rule",
            target=target_str,
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
            actual=f"{len(violations)} non-conforming property value(s)",
            reason="; ".join(violations),
            rule_type="column_rule",
            target=target_str,
        )
    return AssertionResult(name=name, passed=True, rule_type="column_rule", target=target_str)


# ==============================================================================
# 3. DAX LOGIC, CONTRACT PINNING & LINEAGE ASSERTIONS
# ==============================================================================

@register("dax_exact")
def assert_dax_exact(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Exact Contract Match")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)
    expected_dax = (rule.get("expected") or "").strip()

    if target not in model.measures:
        col_hint = _find_column_in_model(target, model)
        extra_msg = f" (exists as column '{col_hint}', not as a measure)" if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=expected_dax,
            actual="[Not Found in Model]",
            reason=f"Measure [{target}] is missing from model definitions{extra_msg}",
            rule_type="dax_exact",
            target=target,
        )

    actual_dax = (model.measures[target].expression or "").strip()
    actual_norm = normalize_dax(actual_dax)
    expected_norm = normalize_dax(expected_dax)

    if actual_norm.lower() != expected_norm.lower():
        return AssertionResult(
            name=name,
            passed=False,
            expected=expected_dax,
            actual=actual_dax,
            reason="Formula expression diverged from certified baseline contract",
            rule_type="dax_exact",
            target=target,
        )

    return AssertionResult(name=name, passed=True, rule_type="dax_exact", target=target)


@register("dax_dependency_chain", "measure_dependency_chain")
def assert_dax_dependency_chain(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "DAX Dependency Chain Check")
    raw_measure = rule.get("measure", "")
    target = clean_measure_name(raw_measure)

    if target not in model.measures:
        col_hint = _find_column_in_model(target, model)
        extra = f" (exists as column '{col_hint}')" if col_hint else ""
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measure [{target}] to exist",
            actual="[Not Found]",
            reason=f"Measure [{target}] does not exist in model{extra}",
            rule_type="dax_dependency_chain",
            target=target,
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
        failures.append(f"Missing required upstream reference: {', '.join(missing_deps)}")

    forbidden_found = [
        f"[{clean_measure_name(d)}]"
        for d in forbid_deps
        if any(clean_measure_name(d).lower() == r.lower() for r in resolved_deps)
    ]
    if forbidden_found:
        failures.append(f"Contains forbidden upstream reference: {', '.join(forbidden_found)}")

    if failures:
        actual_deps = ", ".join(f"[{d}]" for d in sorted(resolved_deps)) or "None (standalone)"
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Must depend on: {', '.join(f'[{clean_measure_name(x)}]' for x in must_depend)}",
            actual=f"Actual dependencies: {actual_deps}",
            reason="; ".join(failures),
            rule_type="dax_dependency_chain",
            target=target,
        )

    return AssertionResult(name=name, passed=True, rule_type="dax_dependency_chain", target=target)


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

    target_label = clean_measure_name(targets[0].name) if len(targets) == 1 else str(target or pattern_filter)
    if not targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measures matching '{target_label}'",
            actual="No matching measures",
            reason="Target measure not found in semantic model",
            rule_type="column_usage_rule",
            target=target_label,
        )

    forbid_cols = rule.get("forbid_column_references") or rule.get("forbid_columns") or []
    if isinstance(forbid_cols, str):
        forbid_cols = [forbid_cols]

    allowed_tables = rule.get("allowed_tables") or []
    if isinstance(allowed_tables, str):
        allowed_tables = [allowed_tables]

    violations = []
    for m in targets:
        col_refs = extract_column_references(m.expression)
        referenced_tables = {tbl for tbl, _ in col_refs}

        if forbid_cols:
            for tbl, col in col_refs:
                full_col = f"{tbl}[{col}]"
                for pat in forbid_cols:
                    if match_wildcards(full_col, pat.strip()) or match_wildcards(col, pat.strip()):
                        violations.append(f"[{m.name}] references forbidden column '{full_col}'")

        if allowed_tables:
            for tbl in referenced_tables:
                if not match_wildcards(tbl, allowed_tables):
                    violations.append(f"[{m.name}] references table '{tbl}' outside allowed: {allowed_tables}")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected="Measures must strictly adhere to column usage governance",
            actual=targets[0].expression.strip() if len(targets) == 1 else f"{len(violations)} violation(s)",
            reason="; ".join(violations),
            rule_type="column_usage_rule",
            target=target_label,
        )

    return AssertionResult(name=name, passed=True, rule_type="column_usage_rule", target=target_label)


@register("measure_exists")
def assert_measure_exists(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Exists Check")
    raw_target = rule.get("measure", "")
    target = clean_measure_name(raw_target)

    if target in model.measures:
        return AssertionResult(name=name, passed=True, rule_type="measure_exists", target=target)

    col_hint = _find_column_in_model(target, model)
    extra_msg = f" Note: '{target}' was found as a COLUMN in table '{col_hint.split('[')[0]}'." if col_hint else ""

    return AssertionResult(
        name=name,
        passed=False,
        expected=f"Measure [{target}] to exist in model",
        actual="Measure not found",
        reason=f"Missing measure [{target}].{extra_msg}",
        rule_type="measure_exists",
        target=target,
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

    target_label = clean_measure_name(targets[0].name) if len(targets) == 1 else str(target or pattern_filter)
    if not targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Matching measure(s) for '{target_label}'",
            actual="No matching measures",
            reason="Target measure not found in semantic model",
            rule_type="dax_rule",
            target=target_label,
        )

    must_call = rule.get("must_call")
    forbid_op = rule.get("forbid_operator")
    references = rule.get("references")
    check_magic_nums = rule.get("forbid_raw_numeric_literals") or rule.get("forbid_magic_numbers")
    allowed_nums = rule.get("allowed_literals", [0, 1])

    failures = []
    expected_requirements = []

    if must_call:
        expected_requirements.append(f"Must call {must_call}()")
    if forbid_op == "/":
        expected_requirements.append("Forbidden raw '/' division (use DIVIDE)")
    if check_magic_nums:
        expected_requirements.append(f"Permitted numeric literals: {allowed_nums}")
    if references:
        expected_requirements.append(f"Required references: {references}")

    for m in targets:
        clean_dax = strip_dax_comments_and_literals(m.expression)

        if must_call:
            funcs = [must_call] if isinstance(must_call, str) else must_call
            for f in funcs:
                f_name = f.strip()
                if not re.search(rf"\b{re.escape(f_name)}\s*\(", clean_dax, re.IGNORECASE):
                    failures.append(f"Does not call required function '{f_name}()'")

        if forbid_op == "/":
            scrubbed = _strip_dax_tokens_for_literal_scan(m.expression)
            if re.search(r"(?<!/)/(?!/)", scrubbed):
                failures.append("Uses raw '/' division operator instead of safe DIVIDE()")

        if references:
            refs = [references] if isinstance(references, str) else references
            for r in refs:
                clean_ref = clean_measure_name(r)
                if not re.search(rf"\[{re.escape(clean_ref)}\]", clean_dax, re.IGNORECASE):
                    failures.append(f"Missing required upstream reference to [{clean_ref}]")

        if check_magic_nums:
            # Tokenize only naked scalars: strips measure/column references and strings
            scrubbed = _strip_dax_tokens_for_literal_scan(m.expression)
            found_nums = re.findall(r"(?<![a-zA-Z0-9_])(\d+(?:\.\d+)?)(?![a-zA-Z0-9_])", scrubbed)

            allowed_set = set()
            for a in allowed_nums:
                try:
                    allowed_set.add(float(a))
                except (ValueError, TypeError):
                    pass

            unauthorized = [n for n in found_nums if float(n) not in allowed_set]
            if unauthorized:
                failures.append(
                    f"Forbidden hardcoded literal(s) detected: {', '.join(unauthorized)} (allowed: {allowed_nums})"
                )

    if failures:
        return AssertionResult(
            name=name,
            passed=False,
            expected="; ".join(expected_requirements) if expected_requirements else "Policy compliance",
            actual=targets[0].expression.strip() if len(targets) == 1 else f"{len(failures)} policy breach(es)",
            reason="; ".join(failures),
            rule_type="dax_rule",
            target=target_label,
        )

    return AssertionResult(name=name, passed=True, rule_type="dax_rule", target=target_label)


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
                missing_targets.append(c)
    elif pattern_filter:
        for m_name, m in model.measures.items():
            if match_wildcards(m_name, pattern_filter):
                matched_measures.append(m)

    target_label = clean_measure_name(targets_list[0]) if targets_list and len(targets_list) == 1 else str(targets_list or pattern_filter)
    if missing_targets:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Measures to exist: {', '.join(targets_list)}",
            actual=f"Missing {len(missing_targets)} measure(s)",
            reason=f"Missing: {', '.join(missing_targets)}",
            rule_type="measure_format",
            target=target_label,
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
                violations.append(f"[{m.name}] has formatString='{actual_fmt or 'none'}'")
        elif fmt_type in ["percentage", "percent"]:
            if "%" not in actual_fmt and "percent" not in actual_fmt.lower():
                violations.append(f"[{m.name}] has formatString='{actual_fmt or 'none'}'")
        else:
            if fmt_type not in actual_fmt.lower():
                violations.append(f"[{m.name}] has formatString='{actual_fmt or 'none'}'")

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"Format category: '{fmt_type}'",
            actual=f"{len(violations)} non-conforming format(s)",
            reason="; ".join(violations),
            rule_type="measure_format",
            target=target_label,
        )
    return AssertionResult(name=name, passed=True, rule_type="measure_format", target=target_label)


@register("measure_naming", "measure_forbidden_pattern")
def assert_measure_naming(model: SemanticModel, rule: Dict[str, Any]) -> AssertionResult:
    name = rule.get("name", "Measure Naming Rule")
    forbid_prefixes = rule.get("forbid_prefixes", [])

    violations = [
        f"[{m}] has forbidden prefix '{p}'"
        for m in model.measures.keys()
        for p in forbid_prefixes
        if m.lower().startswith(p.lower())
    ]

    if violations:
        return AssertionResult(
            name=name,
            passed=False,
            expected=f"No measure names starting with {forbid_prefixes}",
            actual=f"{len(violations)} non-conforming measure(s)",
            reason="; ".join(violations),
            rule_type="measure_naming",
            target="Model Measures",
        )
    return AssertionResult(name=name, passed=True, rule_type="measure_naming", target="Model Measures")