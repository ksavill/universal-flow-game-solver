from __future__ import annotations

import math
import unittest

from flow_solver.puzzle import Puzzle


class CircleOrientationTests(unittest.TestCase):
    def test_circle_flow_positions_match_editor_orientation(self) -> None:
        text = "\n".join(
            [
                "# type: circle",
                "A..A....",
                "B..B....",
                "",
            ]
        )
        puzzle = Puzzle.from_flow_text(text, source_name="circle_test.flow")
        n00 = puzzle.graph.nodes["0,0"].pos
        n10 = puzzle.graph.nodes["1,0"].pos
        n20 = puzzle.graph.nodes["2,0"].pos
        n30 = puzzle.graph.nodes["3,0"].pos

        # Cells use sector centers (half-step from top boundary) and increase clockwise.
        # For 8 sectors: 0=top-right, 1=right, 2=right-bottom, 3=bottom-right.
        self.assertGreater(float(n00[0]), 0.0)
        self.assertGreater(float(n00[1]), 0.0)
        self.assertGreater(float(n10[0]), 0.0)
        self.assertGreater(float(n10[1]), 0.0)
        self.assertGreater(float(n20[0]), 0.0)
        self.assertLess(float(n20[1]), 0.0)
        self.assertGreater(float(n30[0]), 0.0)
        self.assertLess(float(n30[1]), 0.0)

        def ang(pos: tuple[float, float, float]) -> float:
            return (math.degrees(math.atan2(float(pos[1]), float(pos[0]))) + 360.0) % 360.0

        a0 = ang(n00)
        a1 = ang(n10)
        a2 = ang(n20)
        a3 = ang(n30)
        # Clockwise means geometric angle decreases by one sector step (45deg) each increment.
        self.assertAlmostEqual((a0 - a1) % 360.0, 45.0, places=6)
        self.assertAlmostEqual((a1 - a2) % 360.0, 45.0, places=6)
        self.assertAlmostEqual((a2 - a3) % 360.0, 45.0, places=6)


if __name__ == "__main__":
    unittest.main()
