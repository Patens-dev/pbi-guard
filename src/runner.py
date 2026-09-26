from pathlib import Path
from typing import Any, Dict, List, Optional

from .assertions import ASSERTION_REGISTRY
from .formatter import fmt
from .models import AssertionResult, SemanticModel
from .reporter import generate_report


def run_suite(
    model: SemanticModel,
    config: Dict[str, Any],
    verbose: bool = False,
    report_path: Optional[Path] = None,
) -> bool:
    assertions = config.get("assertions", [])
    if not assertions:
        print(f"{fmt.YELLOW}Warning:{fmt.RESET} No assertions defined in config.")
        return True

    print(
        f"\n{fmt.BOLD}Model Summary:{fmt.RESET} {len(model.tables)} table(s), "
        f"{len(model.measures)} measure(s) parsed.\n"
    )

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
                fmt.print_diff("Expected", res.expected, fmt.GREEN)
            if res.actual:
                fmt.print_diff("Actual  ", res.actual, fmt.RED)
            print()

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    failed_count = total - passed_count
    summary_color = fmt.GREEN if failed_count == 0 else fmt.RED

    print(
        f"\n{fmt.BOLD}Summary:{fmt.RESET} {summary_color}{passed_count}/{total} passed{fmt.RESET}, "
        f"{failed_count} failed.\n"
    )

    if report_path:
        out_file = generate_report(results, model, config, report_path)
        print(f"{fmt.BOLD}Audit Sign-Off Report Exported:{fmt.RESET} {out_file}\n")

    return failed_count == 0