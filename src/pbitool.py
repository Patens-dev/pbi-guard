"""Power BI Desktop External Tools (.pbitool.json) registration and management."""

import json
import os
from pathlib import Path
import shutil
import sys
from typing import List, Optional

# Minimal embedded SVG icon for Power BI Desktop ribbon integration
SHIELD_ICON_DATA = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%234F46E5'>"
    "<path d='M12 2L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-3zm0 "
    "10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-2.33v8.02z'/>"
    "</svg>"
)


def get_all_target_directories() -> List[Path]:
    """Returns all directories where Power BI Desktop searches for external tools."""
    dirs: List[Path] = []

    # 1. User-level directory (Recommended: Requires no admin rights, checked first)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        lad = Path(local_app_data)
        dirs.append(lad / "Microsoft" / "Power BI Desktop" / "External Tools")
        dirs.append(lad / "Microsoft" / "Power BI Desktop Store App" / "External Tools")
        pkg_dir = lad / "Packages"
        if pkg_dir.is_dir():
            for pkg in pkg_dir.glob("Microsoft.MicrosoftPowerBIDesktop*"):
                dirs.append(pkg / "LocalCache" / "Local" / "Microsoft" / "Power BI Desktop" / "External Tools")

    # 2. 64-bit Common Files (Default modern Power BI Desktop)
    common_program_files = os.environ.get("CommonProgramFiles")
    if common_program_files:
        dirs.append(Path(common_program_files) / "Microsoft Shared" / "Power BI Desktop" / "External Tools")

    # 3. 32-bit Common Files fallback
    common_program_files_x86 = os.environ.get("CommonProgramFiles(x86)")
    if common_program_files_x86:
        dirs.append(Path(common_program_files_x86) / "Microsoft Shared" / "Power BI Desktop" / "External Tools")

    return dirs


def get_pythonw_executable() -> str:
    """
    Strictly resolves to a working pythonw.exe to prevent console flashes.
    If the current virtualenv lacks pythonw.exe, automatically copies it
    from the base Python runtime into the virtualenv Scripts folder.
    """
    curr_exe = Path(sys.executable)

    # 1. Already running through pythonw
    if curr_exe.stem.lower() == "pythonw":
        return str(curr_exe.resolve())

    # 2. Check current virtualenv/Scripts folder
    sibling = curr_exe.with_name("pythonw.exe")
    if sibling.is_file():
        return str(sibling.resolve())

    # 3. Virtualenv repair: Copy pythonw.exe from base installation into venv
    if hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix:
        base_candidates = [
            Path(sys.base_prefix) / "pythonw.exe",
            Path(sys.base_prefix) / "Scripts" / "pythonw.exe",
        ]
        for base_bin in base_candidates:
            if base_bin.is_file():
                try:
                    dest = curr_exe.with_name("pythonw.exe")
                    shutil.copy2(base_bin, dest)
                    return str(dest.resolve())
                except Exception:
                    pass
                return str(base_bin.resolve())

    # 4. Global system PATH
    which_pw = shutil.which("pythonw")
    if which_pw:
        return str(Path(which_pw).resolve())

    return str(curr_exe.resolve())


def generate_manifest(target_model_path: Optional[Path] = None) -> dict:
    """Generates the .pbitool.json registration document."""
    project_root = Path(__file__).resolve().parent.parent
    main_py = project_root / "main.py"
    pythonw = get_pythonw_executable()

    arg_parts = [f'"{main_py.resolve()}"', "web"]
    if target_model_path:
        arg_parts.append(f'"{str(target_model_path.resolve())}"')

    return {
        "version": "1.0",
        "name": "PBI Guard",
        "description": "Silent Logic Guard & Real-Time Dashboard",
        "path": pythonw,
        "arguments": " ".join(arg_parts),
        "iconData": SHIELD_ICON_DATA,
    }


def install_external_tool(target_model_path: Optional[Path] = None) -> List[Path]:
    """
    Installs pbi-guard.pbitool.json to all accessible Power BI directories.
    Prioritizes non-elevated user locations to avoid spawning elevation prompts.
    """
    manifest_data = generate_manifest(target_model_path=target_model_path)
    json_text = json.dumps(manifest_data, indent=2)
    installed_paths: List[Path] = []

    for target_dir in get_all_target_directories():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / "pbi-guard.pbitool.json"
            target_file.write_text(json_text, encoding="utf-8")
            installed_paths.append(target_file)
        except OSError:
            # Skip locations requiring elevated permissions if user directories succeeded
            continue

    return installed_paths


def uninstall_external_tool() -> bool:
    """Removes pbi-guard.pbitool.json from all tool directories."""
    removed = False
    for target_dir in get_all_target_directories():
        manifest_file = target_dir / "pbi-guard.pbitool.json"
        if manifest_file.is_file():
            try:
                manifest_file.unlink()
                removed = True
            except OSError:
                pass
    return removed