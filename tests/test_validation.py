from __future__ import annotations

import json
import unittest

from flow_solver.puzzle import Puzzle
from flow_solver.validation import validate_puzzle


class PuzzleValidationTests(unittest.TestCase):
    def test_bridge_channels_do_not_report_disconnected_physical_board(self) -> None:
        puzzle = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
#B#
A+A
#B#
"""
        )

        report = validate_puzzle(puzzle)

        self.assertEqual(report.stats["components"], 2)
        self.assertEqual(report.stats["physical_components"], 1)
        self.assertNotIn("disconnected_graph", {issue.code for issue in report.warnings})

    def test_valid_square_passes_structural_validation(self) -> None:
        puzzle = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
            ABC
            ...
            ABC
"""
        )
        report = validate_puzzle(puzzle)
        self.assertTrue(report.valid, report.to_dict())
        self.assertEqual(report.stats["components"], 1)

    def test_disconnected_terminal_pair_is_rejected(self) -> None:
        payload = {
            "space": {
                "type": "graph",
                "nodes": {"a": {"pos": [0, 0]}, "b": {"pos": [1, 0]}},
                "edges": [],
            },
            "terminals": {"A": ["a", "b"]},
        }
        report = validate_puzzle(Puzzle.from_json(json.dumps(payload)))
        codes = {issue.code for issue in report.errors}
        self.assertIn("terminal_pair_disconnected", codes)
        self.assertIn("isolated_terminal", codes)

    def test_full_cover_bipartite_parity_is_checked(self) -> None:
        # A 2x2 board has equal bipartition sizes, but this pair places both
        # terminals on the same side, making full coverage impossible.
        puzzle = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
A.
.A
"""
        )
        report = validate_puzzle(puzzle)
        self.assertIn("bipartite_parity", {issue.code for issue in report.errors})

    def test_optional_fill_skips_required_cell_checks(self) -> None:
        payload = {
            "fill": False,
            "space": {
                "type": "graph",
                "nodes": {
                    "a": {"pos": [0, 0]},
                    "b": {"pos": [1, 0]},
                    "unused": {"pos": [5, 0]},
                },
                "edges": [["a", "b"]],
            },
            "terminals": {"A": ["a", "b"]},
        }
        report = validate_puzzle(Puzzle.from_json(json.dumps(payload)))
        self.assertTrue(report.valid, report.to_dict())
        self.assertIn("disconnected_graph", {issue.code for issue in report.warnings})


if __name__ == "__main__":
    unittest.main()
