from __future__ import annotations

import time
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from flow_solver.graph import Graph, Node
from flow_solver.puzzle import Puzzle
from flow_solver.solver import (
    PuzzleUnsolvableError,
    SolveResult,
    SolveTimeoutError,
    check_uniqueness_with_z3,
    solve_with_z3,
    validate_solution,
)


ROOT = Path(__file__).resolve().parents[1]


def graph_puzzle(
    node_ids: Iterable[str],
    edges: Iterable[Tuple[str, str]],
    terminals: Dict[str, Tuple[str, str]],
    *,
    fill: bool,
    tiles: Dict[str, List[str]] | None = None,
) -> Puzzle:
    graph = Graph()
    ids = list(node_ids)
    terminal_nodes = {node for pair in terminals.values() for node in pair}
    for index, node_id in enumerate(ids):
        graph.add_node(
            Node(
                id=node_id,
                pos=(float(index), 0.0, 0.0),
                kind="terminal" if node_id in terminal_nodes else "cell",
            )
        )
    for u, v in edges:
        graph.add_edge(u, v)
    return Puzzle(
        graph=graph,
        tiles=tiles or {node_id: [node_id] for node_id in ids},
        terminals=terminals,
        fill=fill,
    )


class Z3EdgeSolverCorrectnessTests(unittest.TestCase):
    def test_same_color_adjacent_chords_are_not_implicitly_selected(self) -> None:
        # The only full-cover route snakes across both rows.  Its endpoints and
        # middle cells touch across unused vertical edges; the old node-color
        # formulation incorrectly counted those chords and declared UNSAT.
        puzzle = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
A..
A..
"""
        )

        result = solve_with_z3(puzzle, timeout_ms=5_000)

        self.assertEqual(
            result.paths["A"],
            ["0,0", "1,0", "2,0", "2,1", "1,1", "0,1"],
        )
        self.assertEqual(len(result.path_edges["A"]), 5)
        self.assertEqual(sum(1 for _ in puzzle.graph.edges()), 7)
        self.assertTrue(validate_solution(puzzle, result).valid)

    def test_bridge_fill_requires_any_channel_not_every_channel(self) -> None:
        puzzle = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
A+A
"""
        )

        result = solve_with_z3(puzzle, timeout_ms=2_000)

        self.assertEqual(result.paths["A"], ["0,0", "1,0:h", "2,0"])
        self.assertIsNone(result.node_color["1,0:v"])
        self.assertTrue(validate_solution(puzzle, result).valid)

    def test_one_color_cannot_use_two_channels_of_same_bridge_tile(self) -> None:
        puzzle = graph_puzzle(
            ["s", "h", "m", "v", "t"],
            [("s", "h"), ("h", "m"), ("m", "v"), ("v", "t")],
            {"A": ("s", "t")},
            fill=True,
            tiles={
                "s": ["s"],
                "bridge": ["h", "v"],
                "m": ["m"],
                "t": ["t"],
            },
        )

        with self.assertRaises(PuzzleUnsolvableError):
            solve_with_z3(puzzle, timeout_ms=2_000)

    def test_fill_false_leaves_unreachable_channels_unused(self) -> None:
        puzzle = graph_puzzle(
            ["s", "t", "unused"],
            [("s", "t")],
            {"A": ("s", "t")},
            fill=False,
        )

        result = solve_with_z3(puzzle, timeout_ms=2_000)

        self.assertEqual(result.paths["A"], ["s", "t"])
        self.assertIsNone(result.node_color["unused"])
        self.assertTrue(validate_solution(puzzle, result).valid)

    def test_lazy_connectivity_cuts_reject_a_forced_disconnected_cycle(self) -> None:
        # Local degrees admit s--t plus the filled four-cycle.  There is only
        # one edge from that cycle to the terminal path, so no globally valid
        # simple terminal-to-terminal path can cover it.
        puzzle = graph_puzzle(
            ["s", "t", "a", "b", "c", "d"],
            [
                ("s", "t"),
                ("t", "a"),
                ("a", "b"),
                ("b", "c"),
                ("c", "d"),
                ("d", "a"),
            ],
            {"A": ("s", "t")},
            fill=True,
        )

        with self.assertRaises(PuzzleUnsolvableError):
            solve_with_z3(puzzle, timeout_ms=2_000)

    def test_existing_touching_path_regression_is_sat(self) -> None:
        puzzle = Puzzle.from_file(
            ROOT / "puzzles" / "square" / "8x8" / "classic_level_104.flow"
        )

        result = solve_with_z3(puzzle, timeout_ms=5_000)

        self.assertTrue(validate_solution(puzzle, result).valid)
        self.assertEqual(sum(len(path) for path in result.paths.values()), 64)
        self.assertLess(result.stats["total_ms"], 5_000)


class Z3EdgeSolverUniquenessTests(unittest.TestCase):
    def test_two_diamond_routes_are_non_unique(self) -> None:
        puzzle = graph_puzzle(
            ["s", "a", "b", "t"],
            [("s", "a"), ("a", "t"), ("s", "b"), ("b", "t")],
            {"A": ("s", "t")},
            fill=False,
        )

        result = check_uniqueness_with_z3(puzzle, timeout_ms=2_000)

        self.assertFalse(result.unique)
        self.assertTrue(validate_solution(puzzle, result).valid)
        self.assertEqual(result.stats["solution_blocks"], 1)

    def test_disconnected_cycle_does_not_create_false_non_uniqueness(self) -> None:
        puzzle = graph_puzzle(
            ["s", "t", "a", "b", "c", "d"],
            [
                ("s", "t"),
                ("t", "a"),
                ("a", "b"),
                ("b", "c"),
                ("c", "d"),
                ("d", "a"),
            ],
            {"A": ("s", "t")},
            fill=False,
        )

        result = check_uniqueness_with_z3(puzzle, timeout_ms=2_000)

        self.assertTrue(result.unique)
        self.assertGreaterEqual(result.stats["connectivity_cuts"], 1)
        self.assertEqual(result.paths["A"], ["s", "t"])

    def test_regular_solve_leaves_uniqueness_unchecked(self) -> None:
        puzzle = graph_puzzle(
            ["s", "t"],
            [("s", "t")],
            {"A": ("s", "t")},
            fill=False,
        )

        result = solve_with_z3(puzzle, timeout_ms=2_000)

        self.assertIsNone(result.unique)
        self.assertFalse(result.stats["uniqueness_checked"])


class Z3EdgeSolverValidationAndDeadlineTests(unittest.TestCase):
    def test_missing_optional_pysat_runtime_falls_back_to_z3(self) -> None:
        puzzle = graph_puzzle(
            ["s", "t"],
            [("s", "t")],
            {"A": ("s", "t")},
            fill=True,
        )

        with patch(
            "flow_solver.solver.pysat_solver.solve_with_pysat",
            side_effect=ImportError("python-sat is not installed"),
        ):
            result = solve_with_z3(puzzle, timeout_ms=2_000)

        self.assertEqual(result.paths["A"], ["s", "t"])
        self.assertEqual(result.stats["solver"], "z3-edge-qffd")

    def test_solution_validator_rejects_node_color_not_backed_by_path(self) -> None:
        puzzle = graph_puzzle(
            ["s", "t", "unused"],
            [("s", "t")],
            {"A": ("s", "t")},
            fill=False,
        )
        bad = SolveResult(
            node_color={"s": "A", "t": "A", "unused": "A"},
            paths={"A": ["s", "t"]},
        )

        report = validate_solution(puzzle, bad)

        self.assertFalse(report.valid)
        self.assertTrue(any("unused" in error for error in report.errors))

    def test_timeout_covers_preprocessing_and_constraint_building(self) -> None:
        puzzle = Puzzle.from_file(
            ROOT / "puzzles" / "square" / "15x15" / "classic_level_150.flow"
        )
        started = time.perf_counter()

        with self.assertRaises(SolveTimeoutError):
            solve_with_z3(puzzle, timeout_ms=1)

        # A wide bound avoids clock/scheduler flakiness while proving the old
        # behavior (9+ seconds after a 1ms timeout) cannot recur.
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
