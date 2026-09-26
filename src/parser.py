import re
from pathlib import Path
from typing import Optional

from .models import Column, Measure, SemanticModel


def resolve_tmdl_directory(target_path: Path) -> Path:
    target = target_path.resolve()
    if target.is_file() and target.suffix.lower() == ".pbip":
        candidates = list(target.parent.glob("*.SemanticModel/definition"))
        if candidates:
            return candidates[0]
        candidates = list(target.parent.glob("*.SemanticModel"))
        if candidates:
            return candidates[0]
    if target.is_dir():
        if (target / "definition").exists():
            return target / "definition"
        # Check if directory contains a *.SemanticModel folder
        sm_candidates = list(target.glob("*.SemanticModel/definition")) or list(target.glob("*.SemanticModel"))
        if sm_candidates:
            return sm_candidates[0]
        # Check subdirectories
        for sub in target.iterdir():
            if sub.is_dir() and sub.name.endswith(".SemanticModel"):
                if (sub / "definition").exists():
                    return sub / "definition"
                return sub
        return target
    return target


def parse_tmdl_directory(model_dir: Path) -> SemanticModel:
    resolved_dir = resolve_tmdl_directory(model_dir)
    tmdl_files = list(resolved_dir.glob("**/*.tmdl"))

    if not tmdl_files:
        raise FileNotFoundError(f"No .tmdl files found in '{resolved_dir}'.")

    model = SemanticModel()
    table_pattern = re.compile(r"^\s*table\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s\r\n]+))")
    col_pattern = re.compile(r"^\s*column\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))(?:\s*=\s*(.*))?")
    measure_pattern = re.compile(r"^\s*measure\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))\s*=\s*(.*)")
    partition_pattern = re.compile(r"^\s*partition\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=\r\n]+))")

    for file_path in tmdl_files:
        # Use utf-8-sig to automatically strip Windows/PBI UTF-8 BOM headers (\ufeff)
        content = file_path.read_text(encoding="utf-8-sig")
        lines = content.splitlines()

        current_table: Optional[str] = None
        current_column: Optional[Column] = None
        current_measure: Optional[Measure] = None

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("///"):
                i += 1
                continue

            # 1. Table declaration
            t_match = table_pattern.match(line)
            if t_match:
                g = t_match.groups()
                current_table = (g[0] or g[1] or g[2]).strip()
                if current_table not in model.tables:
                    model.tables.append(current_table)
                    model.columns[current_table] = {}
                    model.calculated_columns[current_table] = []
                current_column = None
                current_measure = None
                i += 1
                continue

            if current_table:
                # 2. Partition boundary (stops column/measure property bleeding)
                if partition_pattern.match(line):
                    current_column = None
                    current_measure = None
                    i += 1
                    continue

                # 3. Column parsing
                c_match = col_pattern.match(line)
                if c_match:
                    g = c_match.groups()
                    c_name = (g[0] or g[1] or g[2]).strip()
                    is_calc = c_match.group(4) is not None or "=" in line
                    current_column = Column(name=c_name, table=current_table, is_calculated=is_calc)
                    model.columns[current_table][c_name] = current_column
                    if is_calc:
                        model.calculated_columns[current_table].append(c_name)
                    current_measure = None
                    i += 1
                    continue

                # 4. Measure parsing
                m_match = measure_pattern.match(line)
                if m_match:
                    g = m_match.groups()
                    m_name = (g[0] or g[1] or g[2]).strip()
                    remainder = g[3].strip()

                    expr_lines = []
                    if remainder.startswith("```"):
                        expr_lines.append(remainder.lstrip("`"))
                        i += 1
                        while i < len(lines):
                            if "```" in lines[i]:
                                expr_lines.append(lines[i].split("```")[0])
                                break
                            expr_lines.append(lines[i])
                            i += 1
                    elif remainder:
                        expr_lines.append(remainder)
                    else:
                        i += 1
                        while i < len(lines):
                            nxt = lines[i]
                            if nxt.strip() and not nxt.startswith((" ", "\t")):
                                i -= 1
                                break
                            if re.match(r"^\s*(measure|column|table|partition)\b", nxt) or \
                               re.match(r"^\s*(formatString|displayFolder|description|lineageTag)\s*[:=]", nxt):
                                i -= 1
                                break
                            expr_lines.append(nxt.strip())
                            i += 1

                    full_expr = "\n".join(expr_lines).strip()
                    current_measure = Measure(
                        name=m_name, table=current_table, expression=full_expr, file_path=file_path
                    )
                    model.measures[m_name] = current_measure
                    current_column = None
                    i += 1
                    continue

                # 5. Property assignments (supports ':' and '=' syntax)
                separator = ":" if ":" in stripped else ("=" if "=" in stripped else None)
                if separator:
                    k, v = stripped.split(separator, 1)
                    prop_key = k.strip()
                    prop_val = v.strip().strip('"\'')

                    if current_column:
                        current_column.properties[prop_key] = prop_val
                    elif current_measure:
                        current_measure.properties[prop_key] = prop_val

            i += 1

    return model