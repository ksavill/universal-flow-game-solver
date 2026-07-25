from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def infer_bounded_region_seams(
    nodes: Dict[str, Dict[str, Any]],
    edges: Iterable[Tuple[str, str]],
    terminals: Dict[str, List[str]],
    *,
    distance_ratio: float = 2.0,
    timeout_ms: int = 60_000,
    max_candidates: int = 400,
    initial_seam_budget: int = 4,
    max_seam_budget: int = 12,
    tiles: Optional[Dict[str, List[str]]] = None,
    excluded_candidate_nodes: Iterable[str] = (),
    forbidden_edges: Iterable[Tuple[str, str]] = (),
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """Infer a small set of short seam edges for an overlapping region board.

    Shapes boards with tracks crossing over one another hide some adjacencies
    behind the thick overpass outline. Candidate seams are limited to two
    ordinary cell pitches. Exact SAT then admits at most a small seam budget
    and returns only candidate edges actually used by a connected solution.
    """

    info: Dict[str, Any] = {
        "attempted": False,
        "candidate_edges": 0,
        "selected_edges": 0,
        "cut_rounds": 0,
        "warnings": [],
    }
    if len(nodes) < 2 or not terminals:
        info["warnings"].append("Region seam inference needs nodes and terminals.")
        return [], info

    positions: Dict[str, Tuple[float, float]] = {}
    for node_id, node in nodes.items():
        data = node.get("data") if isinstance(node, dict) else None
        center = data.get("pixel_center") if isinstance(data, dict) else None
        if not isinstance(center, (list, tuple)) or len(center) < 2:
            info["warnings"].append("Region seam inference needs pixel-center metadata.")
            return [], info
        try:
            positions[str(node_id)] = (float(center[0]), float(center[1]))
        except (TypeError, ValueError):
            info["warnings"].append("Region seam inference found an invalid pixel center.")
            return [], info

    def canonical(left: str, right: str) -> Tuple[str, str]:
        return (left, right) if left < right else (right, left)

    normalized_edges = {
        tuple(sorted((str(left), str(right))))
        for left, right in edges
        if str(left) in nodes and str(right) in nodes and str(left) != str(right)
    }
    excluded_nodes = {str(node_id) for node_id in excluded_candidate_nodes}
    forbidden = {
        canonical(str(left), str(right))
        for left, right in forbidden_edges
        if str(left) != str(right)
    }
    channel_to_tile: Dict[str, str] = {}
    normalized_tiles: Optional[Dict[str, List[str]]] = None
    if tiles is not None:
        normalized_tiles = {
            str(tile_id): [str(channel_id) for channel_id in channels]
            for tile_id, channels in tiles.items()
        }
        for tile_id, channels in normalized_tiles.items():
            for channel_id in channels:
                channel_to_tile[channel_id] = tile_id
    edge_lengths = [
        math.dist(positions[left], positions[right])
        for left, right in normalized_edges
    ]
    if not edge_lengths:
        info["warnings"].append("Region seam inference needs an existing adjacency pitch.")
        return [], info
    pitch = sorted(edge_lengths)[len(edge_lengths) // 2]
    if pitch <= 1.0:
        info["warnings"].append("Region seam inference found an invalid adjacency pitch.")
        return [], info

    node_ids = sorted(nodes)
    candidates = [
        (left, right)
        for index, left in enumerate(node_ids)
        for right in node_ids[index + 1 :]
        if (left, right) not in normalized_edges
        and left not in excluded_nodes
        and right not in excluded_nodes
        and (left, right) not in forbidden
        and channel_to_tile.get(left, left) != channel_to_tile.get(right, right)
        and math.dist(positions[left], positions[right]) <= pitch * distance_ratio
    ]
    info.update(
        attempted=True,
        pitch=round(pitch, 3),
        distance_ratio=round(float(distance_ratio), 3),
        candidate_edges=len(candidates),
    )
    if not candidates:
        return [], info
    if len(candidates) > max_candidates:
        info["warnings"].append(
            f"Region seam inference found {len(candidates)} candidates, above the "
            f"safety limit of {max_candidates}."
        )
        return [], info

    try:
        from pysat.card import CardEnc, EncType
        from pysat.solvers import Solver

        from flow_solver.puzzle import Puzzle
        from flow_solver.solver.pysat_solver import _PySatSession
        from flow_solver.solver.z3_solver import _Deadline, _prepare_puzzle
    except ImportError as exc:
        info["warnings"].append(f"Minimum-seam inference is unavailable: {exc}")
        return [], info

    graph = {
        "space": {
            "type": "graph",
            "nodes": nodes,
            "edges": [list(edge) for edge in sorted(normalized_edges)] + [
                list(edge) for edge in candidates
            ],
        },
        "terminals": terminals,
        "meta": {"generated": "region_seam_inference"},
    }
    if normalized_tiles is not None:
        graph["tiles"] = normalized_tiles
    stats: Dict[str, Any] = {
        "solver": "pysat-region-seam-inference",
        "validation_ms": 0.0,
        "preprocessing_ms": 0.0,
        "build_ms": 0.0,
        "check_ms": 0.0,
        "extraction_ms": 0.0,
        "sat_checks": 0,
        "z3_checks": 0,
        "connectivity_cuts": 0,
        "solution_blocks": 0,
        "uniqueness_checked": False,
    }
    try:
        deadline = _Deadline(timeout_ms)
        puzzle = Puzzle.from_json(json.dumps(graph))
        prepared = _prepare_puzzle(puzzle, deadline=deadline, stats=stats)
        candidate_indices = {
            index
            for index, edge in enumerate(prepared.edges)
            if tuple(sorted(edge)) not in normalized_edges
        }
        first_budget = max(0, min(int(initial_seam_budget), int(max_seam_budget)))
        budgets = list(range(first_budget, int(max_seam_budget) + 1, 2))
        if budgets and budgets[-1] != int(max_seam_budget):
            budgets.append(int(max_seam_budget))
        for budget in budgets:
            deadline.check("region seam optimization")
            session = _PySatSession(prepared, deadline=deadline, stats=stats)
            try:
                candidate_variables = [
                    variable
                    for (edge_index, _color), variable in session.y.items()
                    if edge_index in candidate_indices
                ]
                if budget < len(candidate_variables):
                    cardinality = CardEnc.atmost(
                        lits=candidate_variables,
                        bound=budget,
                        vpool=session.pool,
                        encoding=EncType.seqcounter,
                    )
                    session.clauses.extend(cardinality.clauses)

                if len(prepared.nodes) >= 80:
                    # Large crossing boards are much faster under CaDiCaL. The
                    # existing PySAT wrapper runs it behind a killable process,
                    # preserving the shared inference deadline.
                    session.portfolio_engines = ("cadical195",)
                    candidate_variable_set = set(candidate_variables)
                    session.phase_hints = [
                        literal
                        for literal in session.phase_hints
                        if abs(literal) not in candidate_variable_set
                    ] + [-variable for variable in candidate_variables]
                else:
                    # Small graphs avoid process startup and use the same
                    # interruptible exact model in-process.
                    session.solver.delete()
                    session.solver = Solver(
                        name="glucose42",
                        bootstrap_with=session.clauses,
                    )
                    session.solver_name = "glucose42"
                    session.portfolio_engines = ()
                    session.solver.set_phases(
                        [-variable for variable in candidate_variables]
                    )
                model = session._check()
                info["cut_rounds"] = 0
                if model is None:
                    continue
                _selected_nodes, _selected_edges, selected_vars = (
                    session._selected_from_model(model)
                )
                selected = sorted(
                    {
                        tuple(sorted(prepared.edges[edge_index]))
                        for edge_index, _color in selected_vars
                        if edge_index in candidate_indices
                    }
                )
                reduced_graph = {
                    "space": {
                        "type": "graph",
                        "nodes": nodes,
                        "edges": [
                            list(edge)
                            for edge in sorted(normalized_edges | set(selected))
                        ],
                    },
                    "terminals": terminals,
                    "meta": {"generated": "region_seam_validation"},
                }
                if normalized_tiles is not None:
                    reduced_graph["tiles"] = normalized_tiles
                validation_stats: Dict[str, Any] = {
                    "solver": "pysat-region-seam-validation",
                    "validation_ms": 0.0,
                    "preprocessing_ms": 0.0,
                    "build_ms": 0.0,
                    "check_ms": 0.0,
                    "extraction_ms": 0.0,
                    "sat_checks": 0,
                    "z3_checks": 0,
                    "connectivity_cuts": 0,
                    "solution_blocks": 0,
                    "uniqueness_checked": False,
                }
                validation_session = None
                try:
                    reduced_puzzle = Puzzle.from_json(json.dumps(reduced_graph))
                    reduced_prepared = _prepare_puzzle(
                        reduced_puzzle,
                        deadline=deadline,
                        stats=validation_stats,
                    )
                    validation_session = _PySatSession(
                        reduced_prepared,
                        deadline=deadline,
                        stats=validation_stats,
                    )
                    validation_session.solver.delete()
                    validation_session.solver = Solver(
                        name="glucose42",
                        bootstrap_with=validation_session.clauses,
                    )
                    validation_session.solver_name = "glucose42"
                    validation_session.portfolio_engines = ()
                    if validation_session.next_connected_solution() is None:
                        continue
                finally:
                    if validation_session is not None:
                        validation_session.close()
                info["selected_edges"] = len(selected)
                info["seam_budget"] = budget
                info["local_model_seed"] = True
                info["validation_cuts"] = int(
                    validation_stats.get("connectivity_cuts", 0)
                )
                return selected, info
            finally:
                session.close()
        info["warnings"].append(
            f"No solvable completion used at most {max_seam_budget} inferred seams."
        )
        return [], info
    except Exception as exc:
        info["warnings"].append(
            f"Region seam inference failed: {type(exc).__name__}: {exc}"
        )
        return [], info
