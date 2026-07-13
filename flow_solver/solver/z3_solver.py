from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

from ..graph import NodeId
from ..puzzle import Color, Puzzle
from ..validation import validate_puzzle
from .types import PathEdge, SolveResult
from .validation import validate_solution


class PuzzleValidationError(ValueError):
    """The puzzle failed structural or static-feasibility validation."""


class PuzzleUnsolvableError(ValueError):
    """The puzzle is valid but has no solution under its declared rules."""


class SolveTimeoutError(ValueError):
    """The shared end-to-end solver deadline expired."""


class SolverUnknownError(ValueError):
    """Z3 returned UNKNOWN for a reason other than the configured deadline."""


class SolverInvariantError(RuntimeError):
    """The internal model failed independent solution validation."""


class _Deadline:
    def __init__(self, timeout_ms: int | None) -> None:
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive or None")
        self.started = time.perf_counter()
        self.timeout_ms = timeout_ms
        self.expires = (
            None if timeout_ms is None else self.started + (float(timeout_ms) / 1000.0)
        )

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def check(self, stage: str) -> None:
        if self.expires is not None and time.perf_counter() >= self.expires:
            raise SolveTimeoutError(
                f"Exact edge solver timed out after {self.timeout_ms}ms during {stage}"
            )

    def remaining_ms(self, stage: str) -> int | None:
        self.check(stage)
        if self.expires is None:
            return None
        remaining = (self.expires - time.perf_counter()) * 1000.0
        if remaining <= 0.0:
            self.check(stage)
        # Z3 accepts integer milliseconds.  Ceiling avoids accidentally giving
        # it a zero timeout while the Python-side deadline still has time left.
        return max(1, int(math.ceil(remaining)))


@dataclass(frozen=True)
class _PreparedPuzzle:
    puzzle: Puzzle
    nodes: Tuple[NodeId, ...]
    colors: Tuple[Color, ...]
    edges: Tuple[Tuple[NodeId, NodeId], ...]
    incident: Mapping[NodeId, Tuple[int, ...]]
    terminals_by_node: Mapping[NodeId, Color]
    allowed_colors: Mapping[NodeId, FrozenSet[Color]]


@dataclass
class _Candidate:
    result: SolveResult
    selected_edge_vars: Set[Tuple[int, Color]]


def _prepare_puzzle(
    puzzle: Puzzle,
    *,
    deadline: _Deadline,
    stats: Dict[str, Any],
) -> _PreparedPuzzle:
    validation_started = time.perf_counter()
    report = validate_puzzle(puzzle)
    stats["validation_ms"] = (time.perf_counter() - validation_started) * 1000.0
    deadline.check("puzzle validation")
    if not report.valid:
        details = "; ".join(issue.message for issue in report.errors[:4])
        if len(report.errors) > 4:
            details += f"; and {len(report.errors) - 4} more error(s)"
        raise PuzzleValidationError(f"Puzzle validation failed: {details}")

    preprocessing_started = time.perf_counter()
    nodes = tuple(sorted(puzzle.graph.nodes))
    colors = tuple(puzzle.all_colors())
    edges = tuple(sorted(puzzle.graph.edges()))

    incident_lists: Dict[NodeId, List[int]] = {node: [] for node in nodes}
    for edge_index, (u, v) in enumerate(edges):
        incident_lists[u].append(edge_index)
        incident_lists[v].append(edge_index)
        if edge_index % 256 == 0:
            deadline.check("topology indexing")
    incident = {node: tuple(indices) for node, indices in incident_lists.items()}

    terminals_by_node = puzzle.terminal_nodes()
    terminal_nodes = set(terminals_by_node)
    allowed: Dict[NodeId, Set[Color]] = {node: set() for node in nodes}

    # A color can never traverse a foreign terminal.  Restricting each domain
    # to static reachability with those terminals removed is exact and often
    # eliminates a substantial fraction of the edge/color variables.
    for color_index, color in enumerate(colors):
        start, goal = puzzle.terminals[color]
        blocked = terminal_nodes - {start, goal}
        reached: Set[NodeId] = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for neighbor in puzzle.graph.neighbors(node):
                if neighbor in blocked or neighbor in reached:
                    continue
                reached.add(neighbor)
                stack.append(neighbor)
            if len(reached) % 256 == 0:
                deadline.check("color reachability preprocessing")
        if goal not in reached:
            raise PuzzleUnsolvableError(
                f"Puzzle is UNSAT: terminals for {color!r} cannot reach each other "
                "without crossing another terminal"
            )
        for node in reached:
            owner = terminals_by_node.get(node)
            if owner is None or owner == color:
                allowed[node].add(color)
        if color_index % 8 == 0:
            deadline.check("color-domain preprocessing")

    # Peel color assignments that cannot supply the two distinct incident
    # edges required by a nonterminal path channel.  This is a fixed-point
    # necessary-condition pass, not a heuristic, so it cannot remove a valid
    # solution.
    changed = True
    pass_index = 0
    while changed:
        changed = False
        pass_index += 1
        for node_index, node in enumerate(nodes):
            if node in terminals_by_node:
                continue
            for color in tuple(allowed[node]):
                possible_neighbors = sum(
                    1 for neighbor in puzzle.graph.neighbors(node) if color in allowed[neighbor]
                )
                if possible_neighbors < 2:
                    allowed[node].remove(color)
                    changed = True
            if node_index % 256 == 0:
                deadline.check("degree-domain preprocessing")

    for color in colors:
        start, goal = puzzle.terminals[color]
        for endpoint in (start, goal):
            possible_neighbors = sum(
                1 for neighbor in puzzle.graph.neighbors(endpoint) if color in allowed[neighbor]
            )
            if possible_neighbors < 1:
                raise PuzzleUnsolvableError(
                    f"Puzzle is UNSAT: terminal {color!r} at {endpoint!r} has no "
                    "remaining color-compatible adjacency"
                )

    if puzzle.fill:
        for tile_id, tile_nodes in puzzle.tiles.items():
            if not any(allowed[node] for node in tile_nodes):
                raise PuzzleUnsolvableError(
                    f"Puzzle is UNSAT: required cell/tile {tile_id!r} cannot be used "
                    "by any terminal pair"
                )

    stats["preprocessing_ms"] = (time.perf_counter() - preprocessing_started) * 1000.0
    stats["domain_pruning_passes"] = pass_index
    stats["nodes"] = len(nodes)
    stats["edges"] = len(edges)
    stats["colors"] = len(colors)
    deadline.check("preprocessing")

    return _PreparedPuzzle(
        puzzle=puzzle,
        nodes=nodes,
        colors=colors,
        edges=edges,
        incident=incident,
        terminals_by_node=terminals_by_node,
        allowed_colors={node: frozenset(values) for node, values in allowed.items()},
    )


class _EdgeZ3Session:
    """Incremental, pure-Boolean path model with lazy connectivity cuts."""

    def __init__(
        self,
        prepared: _PreparedPuzzle,
        *,
        z3: Any,
        deadline: _Deadline,
        stats: Dict[str, Any],
    ) -> None:
        self.prepared = prepared
        self.z3 = z3
        self.deadline = deadline
        self.stats = stats
        self.solver = z3.SolverFor("QF_FD")
        self.x: Dict[Tuple[NodeId, Color], Any] = {}
        self.y: Dict[Tuple[int, Color], Any] = {}
        self._cut_keys: Set[Tuple[Color, FrozenSet[NodeId]]] = set()
        self._build()

    def _build(self) -> None:
        z3 = self.z3
        p = self.prepared
        started = time.perf_counter()

        color_index = {color: index for index, color in enumerate(p.colors)}
        node_index = {node: index for index, node in enumerate(p.nodes)}

        for node in p.nodes:
            for color in p.colors:
                if color in p.allowed_colors[node]:
                    self.x[node, color] = z3.Bool(
                        f"x_{node_index[node]}_{color_index[color]}"
                    )
        self.deadline.check("node/color variable construction")

        for edge_index, (u, v) in enumerate(p.edges):
            for color in p.colors:
                if (u, color) in self.x and (v, color) in self.x:
                    self.y[edge_index, color] = z3.Bool(
                        f"y_{edge_index}_{color_index[color]}"
                    )
            if edge_index % 128 == 0:
                self.deadline.check("edge/color variable construction")

        # A channel belongs to at most one color.
        for node_index_, node in enumerate(p.nodes):
            node_vars = [self.x[node, color] for color in p.colors if (node, color) in self.x]
            if len(node_vars) > 1:
                self.solver.add(z3.AtMost(*node_vars, 1))
            if node_index_ % 128 == 0:
                self.deadline.check("channel capacity constraints")

        # Selecting an edge selects both endpoint channels for the same color.
        for item_index, ((edge_index, color), edge_var) in enumerate(self.y.items()):
            u, v = p.edges[edge_index]
            self.solver.add(
                z3.Implies(edge_var, self.x[u, color]),
                z3.Implies(edge_var, self.x[v, color]),
            )
            if item_index % 256 == 0:
                self.deadline.check("edge endpoint constraints")

        # Used terminals have degree one; every other used channel has degree
        # two.  Edge -> endpoint implications make unused-channel degree zero
        # without an additional reverse constraint.
        constraint_index = 0
        for node in p.nodes:
            for color in p.colors:
                channel_var = self.x.get((node, color))
                if channel_var is None:
                    continue
                edge_vars = [
                    self.y[edge_index, color]
                    for edge_index in p.incident[node]
                    if (edge_index, color) in self.y
                ]
                target = 1 if p.terminals_by_node.get(node) == color else 2
                degree_constraint = (
                    z3.PbEq([(edge_var, 1) for edge_var in edge_vars], target)
                    if len(edge_vars) >= target
                    else z3.BoolVal(False)
                )
                self.solver.add(z3.Implies(channel_var, degree_constraint))
                constraint_index += 1
                if constraint_index % 256 == 0:
                    self.deadline.check("path degree constraints")

        for color in p.colors:
            start, goal = p.puzzle.terminals[color]
            self.solver.add(self.x[start, color], self.x[goal, color])

        # Preserve the existing bridge/multi-channel tile contract:
        # different colors may occupy independent channels simultaneously, but
        # one path may not self-cross by using two channels in the same tile.
        # Full coverage means at least one channel per physical tile, not every
        # internal bridge channel.
        for tile_index, tile_nodes in enumerate(p.puzzle.tiles.values()):
            for color in p.colors:
                tile_color_vars = [
                    self.x[node, color] for node in tile_nodes if (node, color) in self.x
                ]
                if len(tile_color_vars) > 1:
                    self.solver.add(z3.AtMost(*tile_color_vars, 1))
            if p.puzzle.fill:
                tile_vars = [
                    self.x[node, color]
                    for node in tile_nodes
                    for color in p.colors
                    if (node, color) in self.x
                ]
                self.solver.add(z3.Or(*tile_vars))
            if tile_index % 128 == 0:
                self.deadline.check("physical tile constraints")

        self.stats["build_ms"] = (time.perf_counter() - started) * 1000.0
        self.stats["node_color_vars"] = len(self.x)
        self.stats["edge_color_vars"] = len(self.y)
        self.deadline.check("constraint construction")

    def _selected_from_model(
        self, model: Any
    ) -> Tuple[Dict[Color, Set[NodeId]], Dict[Color, Set[int]], Set[Tuple[int, Color]]]:
        z3 = self.z3
        selected_nodes: Dict[Color, Set[NodeId]] = {
            color: set() for color in self.prepared.colors
        }
        selected_edges: Dict[Color, Set[int]] = {
            color: set() for color in self.prepared.colors
        }
        selected_edge_vars: Set[Tuple[int, Color]] = set()

        for index, ((node, color), variable) in enumerate(self.x.items()):
            if z3.is_true(model.eval(variable, model_completion=True)):
                selected_nodes[color].add(node)
            if index % 512 == 0:
                self.deadline.check("model extraction")
        for index, ((edge_index, color), variable) in enumerate(self.y.items()):
            if z3.is_true(model.eval(variable, model_completion=True)):
                selected_edges[color].add(edge_index)
                selected_edge_vars.add((edge_index, color))
            if index % 512 == 0:
                self.deadline.check("model extraction")

        return selected_nodes, selected_edges, selected_edge_vars

    def _add_connectivity_cuts(
        self,
        selected_nodes: Mapping[Color, Set[NodeId]],
        selected_edges: Mapping[Color, Set[int]],
    ) -> int:
        z3 = self.z3
        p = self.prepared
        added = 0

        for color in p.colors:
            adjacency: Dict[NodeId, List[NodeId]] = {
                node: [] for node in selected_nodes[color]
            }
            for edge_index in selected_edges[color]:
                u, v = p.edges[edge_index]
                adjacency[u].append(v)
                adjacency[v].append(u)

            start, goal = p.puzzle.terminals[color]
            unseen = set(selected_nodes[color])
            while unseen:
                seed = min(unseen)
                component = {seed}
                unseen.remove(seed)
                stack = [seed]
                while stack:
                    node = stack.pop()
                    for neighbor in adjacency[node]:
                        if neighbor in unseen:
                            unseen.remove(neighbor)
                            component.add(neighbor)
                            stack.append(neighbor)

                if start in component:
                    continue

                frozen = frozenset(component)
                cut_key = (color, frozen)
                if cut_key in self._cut_keys:
                    raise SolverInvariantError(
                        f"Connectivity separator repeated an existing cut for {color!r}"
                    )

                boundary_vars = [
                    self.y[edge_index, color]
                    for edge_index, (u, v) in enumerate(p.edges)
                    if ((u in component) != (v in component))
                    and (edge_index, color) in self.y
                ]
                active_vars = [self.x[node, color] for node in component]

                # A terminal-free portion of a simple path must be entered and
                # exited (two boundary edges).  The goal-terminal case uses one
                # for robustness, although degree parity normally keeps both
                # terminals together even before connectivity cuts.
                minimum_boundary = 1 if goal in component else 2
                boundary_constraint = (
                    z3.PbGe([(variable, 1) for variable in boundary_vars], minimum_boundary)
                    if len(boundary_vars) >= minimum_boundary
                    else z3.BoolVal(False)
                )
                self.solver.add(
                    z3.Implies(z3.Or(*active_vars), boundary_constraint)
                )
                self._cut_keys.add(cut_key)
                added += 1

            self.deadline.check("connectivity separation")

        self.stats["connectivity_cuts"] += added
        return added

    def _extract_result(
        self,
        selected_nodes: Mapping[Color, Set[NodeId]],
        selected_edges: Mapping[Color, Set[int]],
    ) -> SolveResult:
        p = self.prepared
        extraction_started = time.perf_counter()
        paths: Dict[Color, List[NodeId]] = {}
        path_edges: Dict[Color, List[PathEdge]] = {}

        for color in p.colors:
            adjacency: Dict[NodeId, List[Tuple[NodeId, int]]] = {
                node: [] for node in selected_nodes[color]
            }
            for edge_index in selected_edges[color]:
                u, v = p.edges[edge_index]
                adjacency[u].append((v, edge_index))
                adjacency[v].append((u, edge_index))

            start, goal = p.puzzle.terminals[color]
            path = [start]
            used_path_edges: List[PathEdge] = []
            visited = {start}
            previous_edge: Optional[int] = None
            current = start

            while current != goal:
                candidates = [
                    (neighbor, edge_index)
                    for neighbor, edge_index in adjacency.get(current, [])
                    if edge_index != previous_edge
                ]
                if len(candidates) != 1:
                    raise SolverInvariantError(
                        f"Cannot reconstruct selected path {color!r} at {current!r}; "
                        f"candidates={candidates}"
                    )
                neighbor, edge_index = candidates[0]
                if neighbor in visited:
                    raise SolverInvariantError(
                        f"Selected path {color!r} repeats channel {neighbor!r}"
                    )
                path.append(neighbor)
                used_path_edges.append((current, neighbor))
                visited.add(neighbor)
                current = neighbor
                previous_edge = edge_index
                if len(path) > len(p.nodes):
                    raise SolverInvariantError(
                        f"Selected path {color!r} exceeded the topology size"
                    )

            if visited != selected_nodes[color]:
                missing = sorted(selected_nodes[color] - visited)
                raise SolverInvariantError(
                    f"Selected path {color!r} has disconnected channels: {missing}"
                )
            paths[color] = path
            path_edges[color] = used_path_edges

        node_color: Dict[NodeId, Optional[Color]] = {node: None for node in p.nodes}
        for color in p.colors:
            for node in selected_nodes[color]:
                if node_color[node] is not None:
                    raise SolverInvariantError(
                        f"Channel {node!r} was selected by multiple colors"
                    )
                node_color[node] = color

        result = SolveResult(
            node_color=node_color,
            paths=paths,
            path_edges=path_edges,
            stats=self.stats,
        )
        validation = validate_solution(p.puzzle, result)
        if not validation.valid:
            raise SolverInvariantError(
                "Internal solution failed validation: " + "; ".join(validation.errors[:4])
            )
        self.stats["extraction_ms"] += (time.perf_counter() - extraction_started) * 1000.0
        self.deadline.check("solution extraction and validation")
        return result

    def next_connected_solution(self) -> Optional[_Candidate]:
        """Return the next globally valid path model, or None if exhausted."""

        z3 = self.z3
        while True:
            remaining_ms = self.deadline.remaining_ms("Z3 check")
            if remaining_ms is not None:
                self.solver.set(timeout=remaining_ms)

            check_started = time.perf_counter()
            status = self.solver.check()
            self.stats["check_ms"] += (time.perf_counter() - check_started) * 1000.0
            self.stats["z3_checks"] += 1
            self.deadline.check("Z3 check")

            if status == z3.unsat:
                return None
            if status == z3.unknown:
                reason = self.solver.reason_unknown()
                if "timeout" in reason.lower():
                    raise SolveTimeoutError(
                        f"Z3 edge solver timed out after "
                        f"{self.deadline.timeout_ms}ms during Z3 check"
                    )
                raise SolverUnknownError(
                    f"Z3 returned UNKNOWN (no solution reported). Reason: {reason}"
                )

            model = self.solver.model()
            selected_nodes, selected_edges, selected_edge_vars = self._selected_from_model(model)
            if self._add_connectivity_cuts(selected_nodes, selected_edges):
                continue

            result = self._extract_result(selected_nodes, selected_edges)
            return _Candidate(
                result=result,
                selected_edge_vars=selected_edge_vars,
            )

    def block_edge_assignment(self, selected: Set[Tuple[int, Color]]) -> None:
        """Block one exact edge-colored path assignment for uniqueness checks."""

        differences = []
        for index, (key, variable) in enumerate(self.y.items()):
            differences.append(self.z3.Not(variable) if key in selected else variable)
            if index % 512 == 0:
                self.deadline.check("uniqueness blocking clause")
        if not differences:
            raise SolverInvariantError("A solved puzzle has no edge variables")
        self.solver.add(self.z3.Or(*differences))
        self.stats["solution_blocks"] += 1
        self.deadline.check("uniqueness blocking clause")


def solve_with_z3(
    puzzle: Puzzle,
    *,
    timeout_ms: int | None = 30_000,
    check_unique: bool = False,
) -> SolveResult:
    """Solve a Flow puzzle using explicit edge-colored paths.

    The model is a pure-Boolean finite-domain problem:

    - ``x[v, c]`` selects color ``c`` on channel/node ``v``.
    - ``y[e, c]`` selects graph adjacency ``e`` for that color's path.
    - local degree constraints form terminal-to-terminal paths plus possible
      disconnected cycles.
    - disconnected components are removed incrementally with exact cut-set
      constraints until a globally connected model is found.

    ``timeout_ms`` is one shared deadline covering import, validation,
    preprocessing, constraint construction, every incremental Z3 check,
    extraction, validation, and (when requested) uniqueness.
    """

    # The API keeps the historical ``z3`` solver name, but prefers the same
    # exact edge model compiled to CNF when python-sat is available. Bundled
    # native SAT engines are dramatically faster on sparse hex boards. Set
    # FLOW_DISABLE_PYSAT=1 to retain the pure-Z3 fallback for diagnostics.
    if os.getenv("FLOW_DISABLE_PYSAT", "").strip().lower() not in {"1", "true", "yes", "on"}:
        try:
            from .pysat_solver import solve_with_pysat
        except ImportError:
            pass
        else:
            try:
                return solve_with_pysat(
                    puzzle,
                    timeout_ms=timeout_ms,
                    check_unique=check_unique,
                )
            except ImportError:
                # The wrapper module itself is always importable, even when
                # the optional ``python-sat`` runtime is absent.  Preserve the
                # historical Z3 path instead of turning every solve into an
                # ImportError in partially provisioned environments.
                pass

    deadline = _Deadline(timeout_ms)
    stats: Dict[str, Any] = {
        "solver": "z3-edge-qffd",
        "validation_ms": 0.0,
        "preprocessing_ms": 0.0,
        "build_ms": 0.0,
        "check_ms": 0.0,
        "extraction_ms": 0.0,
        "z3_checks": 0,
        "connectivity_cuts": 0,
        "solution_blocks": 0,
        "uniqueness_checked": bool(check_unique),
    }

    try:
        import z3  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("z3-solver is required. Install with: pip install z3-solver") from e
    deadline.check("Z3 import")

    prepared = _prepare_puzzle(puzzle, deadline=deadline, stats=stats)
    session = _EdgeZ3Session(prepared, z3=z3, deadline=deadline, stats=stats)
    first = session.next_connected_solution()
    if first is None:
        raise PuzzleUnsolvableError("Puzzle is UNSAT (no solution found)")

    if check_unique:
        session.block_edge_assignment(first.selected_edge_vars)
        alternative = session.next_connected_solution()
        first.result.unique = alternative is None

    deadline.check("final result")
    stats["total_ms"] = deadline.elapsed_ms()
    return first.result


def check_uniqueness_with_z3(
    puzzle: Puzzle,
    *,
    timeout_ms: int | None = 30_000,
) -> SolveResult:
    """Solve and determine exact edge-path uniqueness within one deadline."""

    return solve_with_z3(puzzle, timeout_ms=timeout_ms, check_unique=True)


__all__ = [
    "PuzzleUnsolvableError",
    "PuzzleValidationError",
    "SolveTimeoutError",
    "SolverInvariantError",
    "SolverUnknownError",
    "check_uniqueness_with_z3",
    "solve_with_z3",
]
