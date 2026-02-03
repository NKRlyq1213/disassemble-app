from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class SheetSpec:
    width: float
    height: float
    quantity: int  # 0 表示不限（solver 自動算最少片數）


@dataclass(frozen=True)
class PartSpec:
    part_id: str
    width: float
    height: float
    quantity: int
    rotate: bool  # True=允許旋轉90度；False=不允許


@dataclass(frozen=True)
class PartInstance:
    instance_id: str
    part_id: str
    width: float
    height: float
    rotate: bool


@dataclass(frozen=True)
class Placement:
    sheet_index: int
    instance_id: str
    part_id: str
    x: float
    y: float
    width: float
    height: float
    rotated: bool


@dataclass(frozen=True)
class SolveResult:
    sheet_width: float
    sheet_height: float
    kerf: float
    sheets_used: int
    utilization: float
    placements: List[Placement]
    unplaced: List[PartInstance]
    solver: str
    note: Optional[str] = None
