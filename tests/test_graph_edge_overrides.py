from __future__ import annotations

import io
import json
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import app
from flow_solver.puzzle import Puzzle


class GraphEdgeOverrideTests(unittest.TestCase):
    def test_puzzle_json_applies_walls_and_warps(self) -> None:
        payload = {
            "space": {
                "type": "graph",
                "nodes": {
                    "0": {"pos": [0, 0, 0]},
                    "1": {"pos": [1, 0, 0]},
                    "2": {"pos": [2, 0, 0]},
                },
                "edges": [["0", "1"], ["1", "2"]],
                "edge_overrides": {
                    "remove": [["0", "1"]],
                    "add": [["0", "2"]],
                },
                "warps": [["1", "2"]],
                "walls": [["0", "2"]],
            },
            "terminals": {"A": ["0", "2"]},
            "meta": {"source": "unit-test"},
        }
        puzzle = Puzzle.from_json(json.dumps(payload))
        edges = set(puzzle.graph.edges())
        self.assertIn(("0", "2"), edges)
        self.assertIn(("1", "2"), edges)
        self.assertNotIn(("0", "1"), edges)

    def test_image_generate_accepts_manual_edge_overrides(self) -> None:
        image = Image.new("RGB", (40, 40), color=(255, 255, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        file_data = buf.getvalue()

        client = TestClient(app)
        edge_overrides = {
            "add": [["0,0", "1,1"]],
            "remove": [["0,0", "1,0"]],
            "warps": [["0,1", "2,1"]],
            "walls": [["1,0", "1,1"]],
        }
        resp = client.post(
            "/image/generate",
            files={"file": ("unit.png", file_data, "image/png")},
            data={
                "target_type": "graph",
                "graph_layout": "grid",
                "grid_width": "3",
                "grid_height": "2",
                "auto_terminals": "false",
                "auto_classify": "false",
                "edge_overrides_json": json.dumps(edge_overrides),
            },
        )
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        body = resp.json()
        graph_json = json.loads(body["text"])
        space = graph_json["space"]
        self.assertIn("edge_overrides", space)
        self.assertIn("warps", space)
        self.assertIn("walls", space)
        add_pairs = {tuple(pair) for pair in space["edge_overrides"].get("add", [])}
        remove_pairs = {tuple(pair) for pair in space["edge_overrides"].get("remove", [])}
        self.assertIn(("0,0", "1,1"), add_pairs)
        self.assertIn(("0,1", "2,1"), add_pairs)
        self.assertIn(("0,0", "1,0"), remove_pairs)
        self.assertIn(("1,0", "1,1"), remove_pairs)

    def test_image_generate_rejects_invalid_edge_overrides(self) -> None:
        image = Image.new("RGB", (40, 40), color=(255, 255, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        file_data = buf.getvalue()

        client = TestClient(app)
        resp = client.post(
            "/image/generate",
            files={"file": ("unit.png", file_data, "image/png")},
            data={
                "target_type": "graph",
                "graph_layout": "grid",
                "grid_width": "3",
                "grid_height": "2",
                "auto_terminals": "false",
                "auto_classify": "false",
                "edge_overrides_json": json.dumps({"add": "bad"}),
            },
        )
        self.assertEqual(resp.status_code, 400, msg=resp.text)
        detail = str(resp.json().get("detail", ""))
        self.assertIn("edge_overrides_json", detail)


if __name__ == "__main__":
    unittest.main()
