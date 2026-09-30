"""Snapshot and formula freeze generator for Power BI semantic models."""

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from .models import SemanticModel
from .parser import get_model_project_dir, parse_tmdl_directory
from .utils import clean_measure_name, get_git_info, match_wildcards


def freeze_model_baseline(
    model_path: Path,
    output_file: Optional[Path] = None,
    pattern: str = "*",
    forbid_multipliers: bool = True,
    freeze_formats: bool = True,
) -> Tuple[Path, int]:
    """
    Reads TMDL definitions and freezes all matching measures into an immutable YAML contract.
    Writes 'pbi-guard.lock.yml' directly next to the .pbip project file.
    Always completely overwrites previous baseline to establish the new source of truth.
    """
    model: SemanticModel = parse_tmdl_directory(model_path)
    project_root = get_model_project_dir(model_path)

    # Output file is anchored right next to the .pbip project
    if output_file is None:
        target_lock_path = project_root / "pbi-guard.lock.yml"
    else:
        if len(output_file.parts) == 1:
            target_lock_path = project_root / output_file
        else:
            target_lock_path = output_file.resolve()

    target_lock_path.parent.mkdir(parents=True, exist_ok=True)

    git_meta = get_git_info(model_path)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        stored_model_path = os.path.relpath(model_path.resolve(), target_lock_path.parent)
    except ValueError:
        stored_model_path = str(model_path.resolve())

    assertions: List[Dict[str, Any]] = []
    frozen_count = 0

    for m_name, measure in sorted(model.measures.items()):
        if not match_wildcards(m_name, pattern):
            continue

        clean_name = clean_measure_name(m_name)

        # 1. Exact DAX formula lock
        assertions.append({
            "name": f"Lock [{clean_name}] Logic",
            "type": "dax_exact",
            "measure": clean_name,
            "expected": measure.expression,
        })
        frozen_count += 1

        # 2. Block unapproved fudge-factor multipliers (* 1.xx)
        if forbid_multipliers:
            assertions.append({
                "name": f"Guard [{clean_name}] Multipliers",
                "type": "dax_rule",
                "measure": clean_name,
                "forbid_magic_numbers": True,
                "allowed_literals": [0, 1],
            })

        # 3. Optional format string lock
        fmt_str = measure.properties.get("formatString") or measure.properties.get("format")
        if freeze_formats and fmt_str:
            assertions.append({
                "name": f"Format [{clean_name}]",
                "type": "measure_format",
                "measure": clean_name,
                "format": fmt_str,
            })

    baseline = {
        "# metadata": {
            "generator": "pbi-guard freeze",
            "created_utc": now_utc,
            "git_commit": git_meta["commit"],
            "git_branch": git_meta["branch"],
            "model_path": stored_model_path.replace("\\", "/"),
            "measures_frozen": frozen_count,
        },
        "version": 1,
        "model_path": stored_model_path.replace("\\", "/"),
        "assertions": assertions,
    }

    # Atomic write to overwrite completely
    target_lock_path.write_text(yaml.safe_dump(baseline, sort_keys=False), encoding="utf-8")
    return target_lock_path, frozen_count