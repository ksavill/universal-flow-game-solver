from __future__ import annotations

import unittest

from backend.app import _graph_payload
from flow_solver.puzzle import Puzzle


class TerminalColorMetaTests(unittest.TestCase):
    def test_flow_terminal_colors_json_meta_is_exposed_on_graph(self) -> None:
        text = "\n".join(
            [
                "# type: square",
                '# terminal_colors: {"A":"#00ff00","B":"#ff0","Z":"#123456"}',
                "A.B",
                "...",
                "A.B",
                "",
            ]
        )
        puzzle = Puzzle.from_flow_text(text, source_name="terminal_colors.flow")
        payload = _graph_payload(puzzle)

        self.assertIn("terminal_colors", payload)
        self.assertEqual(payload["terminal_colors"], {"A": "#00ff00", "B": "#ffff00"})

    def test_flow_terminal_colors_pair_string_is_parsed(self) -> None:
        text = "\n".join(
            [
                "# type: square",
                "# terminal_colors: A=#0f0; B:#0000ff; C=bad",
                "A.B",
                "...",
                "A.B",
                "",
            ]
        )
        puzzle = Puzzle.from_flow_text(text, source_name="terminal_colors_pairs.flow")
        payload = _graph_payload(puzzle)

        self.assertIn("terminal_colors", payload)
        self.assertEqual(payload["terminal_colors"], {"A": "#00ff00", "B": "#0000ff"})


if __name__ == "__main__":
    unittest.main()
