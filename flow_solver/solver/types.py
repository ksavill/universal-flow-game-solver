from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from ..graph import NodeId
from ..puzzle import Color

SolverName = Literal["z3", "dfs"]
PathEdge = Tuple[NodeId, NodeId]


@dataclass
class SolveResult:
    node_color: Dict[NodeId, Optional[Color]]  # None => unused
    paths: Dict[Color, List[NodeId]]  # ordered node ids from terminal->terminal
    # Explicit selected adjacencies.  Node colors alone cannot distinguish a
    # path from an unused same-color chord between two adjacent cells.
    path_edges: Dict[Color, List[PathEdge]] = field(default_factory=dict)
    # Solver-specific diagnostics.  Kept as a JSON-friendly mapping so callers
    # that only use ``node_color``/``paths`` remain source-compatible.
    stats: Dict[str, Any] = field(default_factory=dict)
    # None means uniqueness was not checked (or could not be established).
    unique: Optional[bool] = None
