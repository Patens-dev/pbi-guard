import argparse
import sys
from pathlib import Path
import yaml

from .formatter import fmt
from .parser import parse_tmdl_directory
from .runner import run_suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pbi-guard",
        description="Power BI TMDL Static Analysis and Contract Testing Runner."
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=Path("pbi_tests.yml"), help="Path to YAML test config file"
    )
    parser.add_argument(
        "-m", "--model", type=Path, help="Path to .pbip, .SemanticModel, or definition directory"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Display verbose pass details"
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config_path = args.config.resolve()
    if not config_path.is_file():
        print(f"{fmt.RED}Error:{fmt.RESET} Config file '{config_path}' not found.", file=sys.stderr)
        sys.exit(2)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        print(f"{fmt.RED}Error parsing YAML config:{fmt.RESET} {exc}", file=sys.stderr)
        sys.exit(2)

    if args.model:
        model_input = args.model
    else:
        cfg_model = config.get("model_path")
        if not cfg_model:
            print(f"{fmt.RED}Error:{fmt.RESET} No model path provided via --model or YAML.", file=sys.stderr)
            sys.exit(2)
        model_input = config_path.parent / cfg_model

    try:
        model = parse_tmdl_directory(model_input)
        success = run_suite(model, config, verbose=args.verbose)
        sys.exit(0 if success else 1)
    except FileNotFoundError as fnf:
        print(f"{fmt.RED}Path Error:{fmt.RESET} {fnf}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"{fmt.RED}Fatal Error:{fmt.RESET} {exc}", file=sys.stderr)
        sys.exit(2)