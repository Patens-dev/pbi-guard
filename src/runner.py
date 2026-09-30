from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .assertions import ASSERTION_REGISTRY
from .formatter import fmt
from .models import AssertionResult, SemanticModel
from .reporter import generate_report


def run_suite(
    model: SemanticModel,
    config: Dict[str, Any],
    verbose: bool = False,
    report_path: Optional[Path] = None,
) -> Tuple[bool, List[AssertionResult]]:
    assertions = config.get("assertions", [])
    if not assertions:
        return True, []

    results: List[AssertionResult] = []
    for item in assertions:
        name = item.get("name", "Unnamed Assertion")
        t_type = item.get("type")
        handler = ASSERTION_REGISTRY.get(t_type)

        if not handler:
            results.append(
                AssertionResult(
                    name=name,
                    passed=False,
                    is_error=True,
                    reason=f"Unknown assertion type '{t_type}'",
                    rule_type=t_type,
                )
            )
            continue

        try:
            res = handler(model, item)
            if not res.rule_type:
                res.rule_type = t_type
            if not res.target:
                res.target = (
                    item.get("measure")
                    or item.get("table")
                    or item.get("column")
                    or item.get("measures_matching")
                    or item.get("table_pattern")
                )
            results.append(res)
        except Exception as ex:
            results.append(
                AssertionResult(
                    name=name,
                    passed=False,
                    is_error=True,
                    reason=str(ex),
                    rule_type=t_type,
                )
            )

    failed_count = sum(1 for r in results if not r.passed or r.is_error)

    if report_path:
        generate_report(results, model, config, report_path)

    return failed_count == 0, results