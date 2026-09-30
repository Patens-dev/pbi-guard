import argparse
import os
from pathlib import Path
import sys
from typing import Optional
import yaml

from .formatter import fmt
from .parser import (
    PROJECT_CACHE_FILE,
    detect_active_powerbi_model,
    find_local_model,
    get_model_project_dir,
    parse_tmdl_directory,
    resolve_tmdl_directory,
)
from src.pbitool import install_external_tool, uninstall_external_tool
from src.runner import run_suite
from src.server import launch_web_server
from src.snapshot import freeze_model_baseline


def _clean_path_input(raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    cleaned = str(raw).strip().strip('"\'')
    return Path(cleaned) if cleaned else None


def _resolve_model_or_exit(explicit_path: Optional[Path]) -> Path:
    if explicit_path:
        cleaned = Path(str(explicit_path).strip('"\''))
        resolved = resolve_tmdl_directory(cleaned)
        if resolved.exists():
            PROJECT_CACHE_FILE.write_text(str(cleaned.resolve()), encoding="utf-8")
            return resolved
        print(f"{fmt.RED}Error:{fmt.RESET} Model path '{cleaned}' does not exist.", file=sys.stderr)
        sys.exit(2)

    auto_found = detect_active_powerbi_model()
    if auto_found:
        PROJECT_CACHE_FILE.write_text(str(auto_found.resolve()), encoding="utf-8")
        return resolve_tmdl_directory(auto_found)

    auto_local = find_local_model()
    if auto_local:
        PROJECT_CACHE_FILE.write_text(str(auto_local.resolve()), encoding="utf-8")
        return resolve_tmdl_directory(auto_local)

    print(f"{fmt.RED}Error:{fmt.RESET} No active Power BI model found.", file=sys.stderr)
    sys.exit(2)


def handle_web(args: argparse.Namespace) -> None:
    raw_target = args.target or args.model
    target_path = _clean_path_input(raw_target)
    initial = resolve_tmdl_directory(target_path) if target_path else None
    launch_web_server(initial_model=initial, auto_open=True)


def handle_freeze(args: argparse.Namespace) -> None:
    raw_target = args.target or args.model
    target_path = _clean_path_input(raw_target)
    model_path = _resolve_model_or_exit(target_path)
    output_arg = _clean_path_input(args.output)

    try:
        lock_path, count = freeze_model_baseline(
            model_path=model_path,
            output_file=output_arg,
            pattern=args.pattern,
            forbid_multipliers=not args.allow_multipliers,
            freeze_formats=not args.no_formats,
        )
        print(f"{fmt.pass_tag()} Frozen {fmt.BOLD}{count}{fmt.RESET} measure contracts into: {lock_path}\n")
    except Exception as exc:
        print(f"{fmt.RED}Freeze Failed:{fmt.RESET} {exc}", file=sys.stderr)
        sys.exit(1)


def handle_check(args: argparse.Namespace) -> None:
    raw_target = args.target or args.model
    target_path = _clean_path_input(raw_target)
    model_path = _resolve_model_or_exit(target_path) if target_path else _resolve_model_or_exit(None)

    project_root = get_model_project_dir(model_path)
    config_path = project_root / "pbi-guard.lock.yml"

    if not config_path.is_file():
        print(f"{fmt.RED}Error:{fmt.RESET} No lockfile found at {config_path}. Run freeze first.", file=sys.stderr)
        sys.exit(2)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        model = parse_tmdl_directory(model_path)
        success, results = run_suite(model, config, verbose=args.verbose)
        sys.exit(0 if success else 1)
    except Exception as exc:
        print(f"{fmt.RED}Execution Error:{fmt.RESET} {exc}", file=sys.stderr)
        sys.exit(2)


def handle_pbitool(args: argparse.Namespace) -> None:
    if args.uninstall:
        removed = uninstall_external_tool()
        if removed:
            print(f"{fmt.pass_tag()} Removed PBI Guard from Power BI Desktop External Tools.")
    else:
        model_path_arg = _clean_path_input(args.model)
        installed_paths = install_external_tool(target_model_path=model_path_arg)
        print(f"{fmt.pass_tag()} Registered in Power BI Desktop External Tools ribbon!")
        for path in installed_paths:
            print(f"       └── Manifest: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbi-guard", description="PBI Guard: Business Logic Firewall.")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # In src/cli.py inside build_parser()

    p_web = subparsers.add_parser("web", aliases=["serve", "gui"], help="Launch live web dashboard")
    p_web.add_argument("target", nargs="?", type=str)
    p_web.add_argument("-m", "--model", type=str)
    p_web.add_argument("--restarting", action="store_true", help=argparse.SUPPRESS)

    p_freeze = subparsers.add_parser("freeze", help="Freeze formulas into baseline lockfile")
    p_freeze.add_argument("target", nargs="?", type=str)
    p_freeze.add_argument("-m", "--model", type=str)
    p_freeze.add_argument("-o", "--output", type=str)
    p_freeze.add_argument("-p", "--pattern", type=str, default="*")
    p_freeze.add_argument("--allow-multipliers", action="store_true")
    p_freeze.add_argument("--no-formats", action="store_true")

    p_check = subparsers.add_parser("check", help="Verify model against frozen baseline")
    p_check.add_argument("target", nargs="?", type=str)
    p_check.add_argument("-m", "--model", type=str)
    p_check.add_argument("-v", "--verbose", action="store_true")

    p_tool = subparsers.add_parser("pbitool", help="Manage External Tools integration")
    p_tool.add_argument("-m", "--model", type=str)
    p_tool.add_argument("--uninstall", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command or args.command in ("web", "serve", "gui", "sync"):
        handle_web(args)
    elif args.command == "freeze":
        handle_freeze(args)
    elif args.command == "check":
        handle_check(args)
    elif args.command == "pbitool":
        handle_pbitool(args)