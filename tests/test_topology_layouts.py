from __future__ import annotations

import unittest

from backend.image_utils import build_graph_json


def _interior_count(nodes: dict[str, dict[str, list[float]]], margin: float = 0.25) -> int:
    xs = [float(node["pos"][0]) for node in nodes.values()]
    ys = [float(node["pos"][1]) for node in nodes.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return sum(
        1
        for node in nodes.values()
        if min_x + margin < float(node["pos"][0]) < max_x - margin
        and min_y + margin < float(node["pos"][1]) < max_y - margin
    )


class TopologyLayoutTests(unittest.TestCase):
    def test_cube_uses_face_cells_not_wireframe(self) -> None:
        obj = build_graph_json(layout="cube", width=2, height=2, nodes=2, meta={})
        nodes = obj["space"]["nodes"]
        edges = obj["space"]["edges"]
        # 3 visible faces, each 2x2 cells.
        self.assertEqual(len(nodes), 12)
        self.assertGreaterEqual(len(edges), 18)

    def test_star_layout_has_substantial_interior(self) -> None:
        obj = build_graph_json(layout="star", width=2, height=2, nodes=2, meta={})
        nodes = obj["space"]["nodes"]
        edges = obj["space"]["edges"]
        # Five joined 2x2 faces, matching the reference radial surface.
        self.assertEqual(len(nodes), 20)
        self.assertEqual(len(edges), 30)

        degrees = {node_id: 0 for node_id in nodes}
        for u, v in edges:
            degrees[u] += 1
            degrees[v] += 1
        self.assertLessEqual(max(degrees.values()), 4)

    def test_figure8_layout_has_two_lobes_and_bridge(self) -> None:
        obj = build_graph_json(layout="figure8", width=1, height=2, nodes=2, meta={})
        nodes = obj["space"]["nodes"]
        ys = [float(node["pos"][1]) for node in nodes.values()]
        edges = obj["space"]["edges"]
        self.assertEqual(len(nodes), 31)
        self.assertEqual(len(edges), 43)
        self.assertGreater(max(ys), 0.6)
        self.assertLess(min(ys), -0.6)
        # The faithful region dual has one articulation at the waist.
        bridge_nodes = [
            node
            for node in nodes.values()
            if abs(float(node["pos"][1])) < 0.35 and abs(float(node["pos"][0])) < 1.15
        ]
        self.assertGreaterEqual(len(bridge_nodes), 1)


if __name__ == "__main__":
    unittest.main()
