from __future__ import annotations

import threading
import time
import multiprocessing
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Set, Tuple

from ..graph import NodeId
from ..puzzle import Color, Puzzle
from .types import PathEdge, SolveResult
from .validation import validate_solution
from .z3_solver import (
    PuzzleUnsolvableError,
    SolveTimeoutError,
    SolverInvariantError,
    SolverUnknownError,
    _Deadline,
    _PreparedPuzzle,
    _prepare_puzzle,
)


def _sat_worker(
    engine: str,
    clauses: list[list[int]],
    phase_hints: list[int],
    connection: Any,
) -> None:
    """Run a native SAT engine behind a killable process boundary."""

    try:
        from pysat.solvers import Solver

        with Solver(name=engine, bootstrap_with=clauses) as solver:
            if phase_hints:
                solver.set_phases(phase_hints)
            status = bool(solver.solve())
            connection.send((status, solver.get_model() if status else None, None))
    except BaseException as exc:  # pragma: no cover - child process diagnostics
        connection.send((None, None, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


@dataclass
class _SatCandidate:
    result: SolveResult
    selected_edge_vars: Set[Tuple[int, Color]]


class _PySatSession:
    """Incremental CNF version of the explicit edge-colored path model."""

    def __init__(
        self,
        prepared: _PreparedPuzzle,
        *,
        deadline: _Deadline,
        stats: Dict[str, Any],
    ) -> None:
        try:
            from pysat.card import CardEnc, EncType
            from pysat.formula import IDPool
            from pysat.solvers import Solver
        except Exception as exc:  # pragma: no cover - optional fallback path
            raise ImportError("python-sat is not installed") from exc

        self.prepared = prepared
        self.deadline = deadline
        self.stats = stats
        self.CardEnc = CardEnc
        self.EncType = EncType
        self.pool = IDPool()
        self.x: Dict[Tuple[NodeId, Color], int] = {}
        self.y: Dict[Tuple[int, Color], int] = {}
        self.clauses: list[list[int]] = []
        self._cut_keys: Set[Tuple[Color, frozenset[NodeId]]] = set()
        self._build()
        self.phase_hints = self._build_phase_hints()

        last_error: Optional[Exception] = None
        self.solver_name = ""
        self.solver: Any = None
        # CaDiCaL is orders of magnitude faster on the large, sparse hex CNFs.
        # MiniSat-like engines retain interruptible deadlines for smaller jobs.
        configured_engine = os.getenv("FLOW_PYSAT_ENGINE", "").strip().lower()
        max_degree = max((len(indices) for indices in prepared.incident.values()), default=0)
        self.portfolio_engines: tuple[str, ...] = (
            ("cadical195", "maplechrono")
            if not configured_engine and max_degree >= 5 and 90 <= len(prepared.nodes) <= 105
            else ()
        )
        solver_order = (
            (configured_engine,)
            if configured_engine
            else (
            ("cadical195", "glucose42", "maplechrono")
            if max_degree >= 5 and 90 <= len(prepared.nodes) <= 105
            else ("glucose42", "maplechrono", "minisat22")
            )
        )
        for solver_name in solver_order:
            try:
                self.solver = Solver(
                    name=solver_name,
                    bootstrap_with=self.clauses,
                    use_timer=True,
                )
                self.solver_name = solver_name
                if self.phase_hints:
                    self.solver.set_phases(self.phase_hints)
                break
            except Exception as exc:  # pragma: no cover - platform wheel variance
                last_error = exc
        if self.solver is None:
            raise RuntimeError(f"No bundled PySAT engine is available: {last_error}")
        self.stats["solver"] = (
            "pysat-portfolio-" + "+".join(self.portfolio_engines)
            if self.portfolio_engines
            else f"pysat-{self.solver_name}"
        )
        self.stats["hard_interruptible"] = bool(self.portfolio_engines) or not self.solver_name.startswith("cadical")

    def close(self) -> None:
        if self.solver is not None:
            self.solver.delete()
            self.solver = None

    def _build_phase_hints(self) -> list[int]:
        p = self.prepared
        distances: Dict[Color, Dict[NodeId, int]] = {}
        for color in p.colors:
            start, goal = p.puzzle.terminals[color]
            distance = {start: 0, goal: 0}
            pending = [start, goal]
            cursor = 0
            while cursor < len(pending):
                node = pending[cursor]
                cursor += 1
                for neighbor in p.puzzle.graph.neighbors(node):
                    if color not in p.allowed_colors[neighbor] or neighbor in distance:
                        continue
                    distance[neighbor] = distance[node] + 1
                    pending.append(neighbor)
            distances[color] = distance

        preferred: Dict[NodeId, Color] = {}
        for node in p.nodes:
            owner = p.terminals_by_node.get(node)
            if owner is not None:
                preferred[node] = owner
                continue
            candidates = [
                (distances[color].get(node, len(p.nodes) + 1), index, color)
                for index, color in enumerate(p.colors)
                if color in p.allowed_colors[node]
            ]
            if candidates:
                preferred[node] = min(candidates)[2]

        phases = [
            variable if preferred.get(node) == color else -variable
            for (node, color), variable in self.x.items()
        ]
        for (edge_index, color), variable in self.y.items():
            u, v = p.edges[edge_index]
            phases.append(
                variable
                if preferred.get(u) == color and preferred.get(v) == color
                else -variable
            )
        return phases

    def _extend_cardinality(
        self,
        kind: str,
        literals: list[int],
        bound: int,
        *,
        condition: Optional[int] = None,
    ) -> None:
        if kind == "equals":
            encoded = self.CardEnc.equals(
                literals,
                bound,
                vpool=self.pool,
                encoding=self.EncType.seqcounter,
            )
        elif kind == "atmost":
            encoded = self.CardEnc.atmost(
                literals,
                bound,
                vpool=self.pool,
                encoding=self.EncType.seqcounter,
            )
        elif kind == "atleast":
            encoded = self.CardEnc.atleast(
                literals,
                bound,
                vpool=self.pool,
                encoding=self.EncType.seqcounter,
            )
        else:  # pragma: no cover - internal contract
            raise ValueError(kind)
        prefix = [] if condition is None else [-condition]
        self.clauses.extend(prefix + list(clause) for clause in encoded.clauses)

    def _build(self) -> None:
        p = self.prepared
        started = time.perf_counter()

        for node in p.nodes:
            for color in p.colors:
                if color in p.allowed_colors[node]:
                    self.x[node, color] = self.pool.id(("x", node, color))
        for edge_index, (u, v) in enumerate(p.edges):
            for color in p.colors:
                if (u, color) in self.x and (v, color) in self.x:
                    self.y[edge_index, color] = self.pool.id(("y", edge_index, color))
            if edge_index % 128 == 0:
                self.deadline.check("SAT edge variable construction")

        for node_index, node in enumerate(p.nodes):
            node_vars = [self.x[node, color] for color in p.colors if (node, color) in self.x]
            if len(node_vars) > 1:
                self._extend_cardinality("atmost", node_vars, 1)
            if node_index % 128 == 0:
                self.deadline.check("SAT channel capacity constraints")

        for item_index, ((edge_index, color), edge_var) in enumerate(self.y.items()):
            u, v = p.edges[edge_index]
            self.clauses.append([-edge_var, self.x[u, color]])
            self.clauses.append([-edge_var, self.x[v, color]])
            if item_index % 256 == 0:
                self.deadline.check("SAT edge endpoint constraints")

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
                if len(edge_vars) < target:
                    self.clauses.append([-channel_var])
                else:
                    self._extend_cardinality(
                        "equals",
                        edge_vars,
                        target,
                        condition=channel_var,
                    )
                constraint_index += 1
                if constraint_index % 256 == 0:
                    self.deadline.check("SAT path degree constraints")

        for color in p.colors:
            start, goal = p.puzzle.terminals[color]
            self.clauses.append([self.x[start, color]])
            self.clauses.append([self.x[goal, color]])

        for tile_index, tile_nodes in enumerate(p.puzzle.tiles.values()):
            for color in p.colors:
                tile_color_vars = [
                    self.x[node, color] for node in tile_nodes if (node, color) in self.x
                ]
                if len(tile_color_vars) > 1:
                    self._extend_cardinality("atmost", tile_color_vars, 1)
            if p.puzzle.fill:
                tile_vars = [
                    self.x[node, color]
                    for node in tile_nodes
                    for color in p.colors
                    if (node, color) in self.x
                ]
                self.clauses.append(tile_vars)
            if tile_index % 128 == 0:
                self.deadline.check("SAT physical tile constraints")

        self.stats["build_ms"] = (time.perf_counter() - started) * 1000.0
        self.stats["node_color_vars"] = len(self.x)
        self.stats["edge_color_vars"] = len(self.y)
        self.stats["cnf_variables"] = self.pool.top
        self.stats["cnf_clauses"] = len(self.clauses)
        self.deadline.check("SAT constraint construction")

    def _check(self) -> Optional[Set[int]]:
        remaining_ms = self.deadline.remaining_ms("PySAT check")
        if self.portfolio_engines:
            started = time.perf_counter()
            context = multiprocessing.get_context("spawn")
            receivers = []
            processes = []
            for engine in self.portfolio_engines:
                parent_connection, child_connection = context.Pipe(duplex=False)
                process = context.Process(
                    target=_sat_worker,
                    args=(
                        engine,
                        self.clauses,
                        [] if engine.startswith("cadical") else self.phase_hints,
                        child_connection,
                    ),
                    daemon=True,
                )
                process.start()
                child_connection.close()
                receivers.append((engine, parent_connection))
                processes.append(process)
            expires = None if remaining_ms is None else time.perf_counter() + remaining_ms / 1000.0
            result: Optional[tuple[Any, Any, Any]] = None
            try:
                while result is None:
                    for _engine, receiver in receivers:
                        if receiver.poll(0.01):
                            result = receiver.recv()
                            break
                    if result is not None:
                        break
                    if expires is not None and time.perf_counter() >= expires:
                        raise SolveTimeoutError(
                            f"Exact SAT solver timed out after {self.deadline.timeout_ms}ms during SAT check"
                        )
            finally:
                for _engine, receiver in receivers:
                    receiver.close()
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(1.0)
                    if process.is_alive():
                        process.kill()
                        process.join(1.0)
            status, raw_model, error = result
            self.stats["check_ms"] += (time.perf_counter() - started) * 1000.0
            self.stats["sat_checks"] += 1
            self.stats["z3_checks"] = self.stats["sat_checks"]
            self.deadline.check("PySAT portfolio check")
            if error:
                raise SolverUnknownError(f"SAT portfolio worker failed: {error}")
            if status is False:
                return None
            if status is not True or raw_model is None:
                raise SolverUnknownError("SAT portfolio returned an unknown result")
            return {literal for literal in raw_model if literal > 0}

        if self.solver_name.startswith("cadical"):
            started = time.perf_counter()
            context = multiprocessing.get_context("spawn")
            parent_connection, child_connection = context.Pipe(duplex=False)
            process = context.Process(
                target=_sat_worker,
                args=("cadical195", self.clauses, [], child_connection),
                daemon=True,
            )
            process.start()
            child_connection.close()
            process.join(None if remaining_ms is None else remaining_ms / 1000.0)
            if process.is_alive():
                process.terminate()
                process.join(2.0)
                parent_connection.close()
                raise SolveTimeoutError(
                    f"Exact SAT solver timed out after {self.deadline.timeout_ms}ms during SAT check"
                )
            if not parent_connection.poll():
                parent_connection.close()
                raise SolverUnknownError("CaDiCaL worker exited without returning a result")
            status, raw_model, error = parent_connection.recv()
            parent_connection.close()
            self.stats["check_ms"] += (time.perf_counter() - started) * 1000.0
            self.stats["sat_checks"] += 1
            self.stats["z3_checks"] = self.stats["sat_checks"]
            self.deadline.check("PySAT check")
            if error:
                raise SolverUnknownError(f"CaDiCaL worker failed: {error}")
            if status is False:
                return None
            if status is not True or raw_model is None:
                raise SolverUnknownError("CaDiCaL returned an unknown result")
            return {literal for literal in raw_model if literal > 0}

        timer: Optional[threading.Timer] = None
        interruptible = not self.solver_name.startswith("cadical")
        if remaining_ms is not None and interruptible:
            timer = threading.Timer(remaining_ms / 1000.0, self.solver.interrupt)
            timer.daemon = True
            timer.start()
        started = time.perf_counter()
        try:
            status = (
                self.solver.solve_limited(expect_interrupt=True)
                if interruptible
                else self.solver.solve()
            )
        finally:
            if timer is not None:
                timer.cancel()
            clear_interrupt = getattr(self.solver, "clear_interrupt", None)
            if callable(clear_interrupt):
                try:
                    clear_interrupt()
                except NotImplementedError:
                    pass
        self.stats["check_ms"] += (time.perf_counter() - started) * 1000.0
        self.stats["sat_checks"] += 1
        self.stats["z3_checks"] = self.stats["sat_checks"]
        self.deadline.check("PySAT check")
        if status is None:
            raise SolveTimeoutError(
                f"Exact SAT solver timed out after {self.deadline.timeout_ms}ms during SAT check"
            )
        if status is False:
            return None
        model = self.solver.get_model()
        if model is None:
            raise SolverUnknownError("PySAT reported SAT without returning a model")
        return {literal for literal in model if literal > 0}

    def _selected_from_model(
        self,
        model: Set[int],
    ) -> Tuple[Dict[Color, Set[NodeId]], Dict[Color, Set[int]], Set[Tuple[int, Color]]]:
        selected_nodes = {color: set() for color in self.prepared.colors}
        selected_edges = {color: set() for color in self.prepared.colors}
        selected_edge_vars: Set[Tuple[int, Color]] = set()
        for (node, color), variable in self.x.items():
            if variable in model:
                selected_nodes[color].add(node)
        for key, variable in self.y.items():
            if variable in model:
                edge_index, color = key
                selected_edges[color].add(edge_index)
                selected_edge_vars.add(key)
        return selected_nodes, selected_edges, selected_edge_vars

    def _add_connectivity_cuts(
        self,
        selected_nodes: Mapping[Color, Set[NodeId]],
        selected_edges: Mapping[Color, Set[int]],
    ) -> int:
        p = self.prepared
        added = 0
        for color in p.colors:
            adjacency = {node: [] for node in selected_nodes[color]}
            for edge_index in selected_edges[color]:
                u, v = p.edges[edge_index]
                adjacency[u].append(v)
                adjacency[v].append(u)
            start, goal = p.puzzle.terminals[color]
            unseen = set(selected_nodes[color])
            while unseen:
                seed = unseen.pop()
                component = {seed}
                pending = [seed]
                while pending:
                    node = pending.pop()
                    for neighbor in adjacency[node]:
                        if neighbor in unseen:
                            unseen.remove(neighbor)
                            component.add(neighbor)
                            pending.append(neighbor)
                if start in component:
                    continue
                frozen = frozenset(component)
                cut_key = (color, frozen)
                if cut_key in self._cut_keys:
                    raise SolverInvariantError(
                        f"Connectivity separator repeated an existing cut for {color!r}"
                    )
                boundary = [
                    self.y[edge_index, color]
                    for edge_index, (u, v) in enumerate(p.edges)
                    if ((u in component) != (v in component)) and (edge_index, color) in self.y
                ]
                prefix = [-self.x[node, color] for node in component]
                minimum = 1 if goal in component else 2
                if len(boundary) < minimum:
                    self.solver.add_clause(prefix)
                    self.clauses.append(prefix)
                else:
                    encoded = self.CardEnc.atleast(
                        boundary,
                        minimum,
                        vpool=self.pool,
                        encoding=self.EncType.seqcounter,
                    )
                    for clause in encoded.clauses:
                        final_clause = prefix + list(clause)
                        self.solver.add_clause(final_clause)
                        self.clauses.append(final_clause)
                self._cut_keys.add(cut_key)
                added += 1
        self.stats["connectivity_cuts"] += added
        return added

    def _extract_result(
        self,
        selected_nodes: Mapping[Color, Set[NodeId]],
        selected_edges: Mapping[Color, Set[int]],
    ) -> SolveResult:
        p = self.prepared
        started = time.perf_counter()
        paths: Dict[Color, list[NodeId]] = {}
        path_edges: Dict[Color, list[PathEdge]] = {}
        for color in p.colors:
            adjacency: Dict[NodeId, list[Tuple[NodeId, int]]] = {
                node: [] for node in selected_nodes[color]
            }
            for edge_index in selected_edges[color]:
                u, v = p.edges[edge_index]
                adjacency[u].append((v, edge_index))
                adjacency[v].append((u, edge_index))
            start, goal = p.puzzle.terminals[color]
            path = [start]
            used_path_edges: list[PathEdge] = []
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
                        f"Cannot reconstruct selected path {color!r} at {current!r}; candidates={candidates}"
                    )
                neighbor, edge_index = candidates[0]
                if neighbor in visited:
                    raise SolverInvariantError(f"Selected path {color!r} repeats channel {neighbor!r}")
                path.append(neighbor)
                used_path_edges.append((current, neighbor))
                visited.add(neighbor)
                current = neighbor
                previous_edge = edge_index
            if visited != selected_nodes[color]:
                raise SolverInvariantError(
                    f"Selected path {color!r} has disconnected channels: "
                    f"{sorted(selected_nodes[color] - visited)}"
                )
            paths[color] = path
            path_edges[color] = used_path_edges

        node_color: Dict[NodeId, Optional[Color]] = {node: None for node in p.nodes}
        for color in p.colors:
            for node in selected_nodes[color]:
                if node_color[node] is not None:
                    raise SolverInvariantError(f"Channel {node!r} was selected by multiple colors")
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
                "Internal SAT solution failed validation: " + "; ".join(validation.errors[:4])
            )
        self.stats["extraction_ms"] += (time.perf_counter() - started) * 1000.0
        return result

    def next_connected_solution(self) -> Optional[_SatCandidate]:
        while True:
            model = self._check()
            if model is None:
                return None
            selected_nodes, selected_edges, selected_edge_vars = self._selected_from_model(model)
            if self._add_connectivity_cuts(selected_nodes, selected_edges):
                continue
            return _SatCandidate(
                result=self._extract_result(selected_nodes, selected_edges),
                selected_edge_vars=selected_edge_vars,
            )

    def block_edge_assignment(self, selected: Set[Tuple[int, Color]]) -> None:
        clause = [
            -variable if key in selected else variable
            for key, variable in self.y.items()
        ]
        if not clause:
            raise SolverInvariantError("A solved puzzle has no edge variables")
        self.solver.add_clause(clause)
        self.clauses.append(clause)
        self.stats["solution_blocks"] += 1


def solve_with_pysat(
    puzzle: Puzzle,
    *,
    timeout_ms: int | None = 30_000,
    check_unique: bool = False,
) -> SolveResult:
    deadline = _Deadline(timeout_ms)
    stats: Dict[str, Any] = {
        "solver": "pysat",
        "validation_ms": 0.0,
        "preprocessing_ms": 0.0,
        "build_ms": 0.0,
        "check_ms": 0.0,
        "extraction_ms": 0.0,
        "sat_checks": 0,
        "z3_checks": 0,
        "connectivity_cuts": 0,
        "solution_blocks": 0,
        "uniqueness_checked": bool(check_unique),
    }
    prepared = _prepare_puzzle(puzzle, deadline=deadline, stats=stats)
    session = _PySatSession(prepared, deadline=deadline, stats=stats)
    try:
        first = session.next_connected_solution()
        if first is None:
            raise PuzzleUnsolvableError("Puzzle is UNSAT (no solution found)")
        if check_unique:
            session.block_edge_assignment(first.selected_edge_vars)
            alternative = session.next_connected_solution()
            first.result.unique = alternative is None
        deadline.check("final SAT result")
        stats["total_ms"] = deadline.elapsed_ms()
        return first.result
    finally:
        session.close()


__all__ = ["solve_with_pysat"]
