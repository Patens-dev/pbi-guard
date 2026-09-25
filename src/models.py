from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Column:
    name: str
    table: str
    is_calculated: bool = False
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class Measure:
    name: str
    table: str
    expression: str
    file_path: Path
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class SemanticModel:
    tables: List[str] = field(default_factory=list)
    measures: Dict[str, Measure] = field(default_factory=dict)
    columns: Dict[str, Dict[str, Column]] = field(default_factory=dict)
    calculated_columns: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class AssertionResult:
    name: str
    passed: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    reason: Optional[str] = None
    is_error: bool = False
    rule_type: Optional[str] = None
    target: Optional[str] = None