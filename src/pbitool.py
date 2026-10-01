"""Power BI Desktop External Tools (.pbitool.json) registration and management."""

import json
import os
from pathlib import Path
import shutil
import sys
from typing import List, Optional

# Valid 32x32 Base64 PNG icon (WPF-compatible, prevents Power BI parser crashes)
SHIELD_ICON_DATA = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAA"
    "BHNCSVQICAgIfAhkiAAAAAlwSFlzAAAOxAAADsQBlSsOGwAAAZpJREFUWIXt1z1rhEAQ"
    "huHnFXeFDyhaWFiIIKW9jT+hnV+QwsLCwsbaqEWsBAW1EgtFwUYQxEtxd7MQk3g3Xk5U"
    "2GdnYZ5n2GEXsFqtrqSUb13Xrc1s2773fb9QSs1s5u12+xVFkXmeZz/h8Xi8e543w7Ks"
    "2wsh5n1/n6bJYVmW70KIecvlcllV1Xocx79mkiQpTdM+hmFMQgj5OI4T27btdrt9m0VR"
    "5J7n/WZZlk2S5G9Zp9PpHyGE3DAMfdm2rc3j8fg6juOa9/v9Z57nv57nTSilvjRNq3u9"
    "Xh0EAeW/oV5KKeU8z2/3+/3ZdV3LNE2rKIrbOI4LznzXdXvf9784nU5/pmnak8vl8uJ5"
    "3jTLsqdpmi9VVRW0bVuWZVmN49h/q+97WZZlXdd1j+dxs9m8y7Js5lgsFj6nNE37WJbl"
    "exAE5EIIeZqmffhRHMf1fD6vj8fja2VZljuOY9u2bb88z/MVRXFt27Z9HMeh7/vXOI5/"
    "0XEc18Mw0DCM/TAMff/3+bKslxBCeZ7n+3mef4Zh6ON5nBBi7vs+dF333ff9LwghZpzz"
    "fwFpL2Y63mJv/AAAAABJRU5ErkJggg=="
)


def get_all_target_directories() -> List[Path]:
    """Returns valid directories where Power BI Desktop searches for external tools."""
    dirs: List[Path] = []

    # 1. 64-bit Common Files (Standard Power BI Desktop MSI / EXE installer)
    common_program_files = os.environ.get("CommonProgramFiles")
    if common_program_files:
        dirs.append(Path(common_program_files) / "Microsoft Shared" / "Power BI Desktop" / "External Tools")

    # 2. 32-bit Common Files fallback
    common_program_files_x86 = os.environ.get("CommonProgramFiles(x86)")
    if common_program_files_x86:
        dirs.append(Path(common_program_files_x86) / "Microsoft Shared" / "Power BI Desktop" / "External Tools")

    # 3. Microsoft Store App directories
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        lad = Path(local_app_data)
        dirs.append(lad / "Microsoft" / "Power BI Desktop Store App" / "External Tools")
        pkg_dir = lad / "Packages"
        if pkg_dir.is_dir():
            for pkg in pkg_dir.glob("Microsoft.MicrosoftPowerBIDesktop*"):
                dirs.append(pkg / "LocalCache" / "Local" / "Microsoft" / "Power BI Desktop" / "External Tools")

    return dirs


def get_pythonw_executable() -> str:
    """
    Strictly resolves to a working pythonw.exe to prevent command prompt flashes.
    Does NOT use .resolve() to avoid breaking Microsoft Store execution aliases.
    """
    curr_exe = Path(sys.executable)

    # 1. Already running through pythonw
    if curr_exe.stem.lower() == "pythonw":
        return str(curr_exe)

    # 2. Check current directory / Scripts folder
    sibling = curr_exe.with_name("pythonw.exe")
    if sibling.is_file():
        return str(sibling)

    # 3. Virtualenv repair: Copy pythonw.exe from base runtime into venv if missing
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
                    return str(dest)
                except Exception:
                    pass
                return str(base_bin)

    # 4. Global system PATH
    which_pw = shutil.which("pythonw")
    if which_pw:
        return str(Path(which_pw))

    return str(curr_exe)


def generate_manifest(target_model_path: Optional[Path] = None) -> dict:
    """Generates the .pbitool.json registration document."""
    project_root = Path(__file__).resolve().parent.parent
    main_py = project_root / "main.py"
    pythonw = get_pythonw_executable()

    arg_parts = [f'"{main_py}"', "web"]
    if target_model_path:
        arg_parts.append(f'"{target_model_path}"')

    return {
        "version": "1.0",
        "name": "PBI Guard",
        "description": "Silent Logic Guard & Real-Time Dashboard",
        "path": pythonw,
        "arguments": " ".join(arg_parts),
        "iconData": SHIELD_ICON_DATA,
    }


def install_external_tool(target_model_path: Optional[Path] = None) -> List[Path]:
    """Installs pbi-guard.pbitool.json into all candidate directories."""
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
            # Skip directories that lack write permissions
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