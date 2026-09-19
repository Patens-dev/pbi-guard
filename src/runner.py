from typing import Any, Dict, List

from .assertions import ASSERTION_REGISTRY
from .formatter import fmt
from .models import AssertionResult, SemanticModel


def run_suite(model: SemanticModel, config: Dict[str, Any], verbose: bool = False) -> bool:
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
                AssertionResult(name=name, passed=False, is_error=True, reason=f"Unknown assertion type '{t_type}'")
            )
            continue

        try:
            results.append(handler(model, item))
        except Exception as ex:
            results.append(AssertionResult(name=name, passed=False, is_error=True, reason=str(ex)))

    # Render results
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
    return failed_count == 0