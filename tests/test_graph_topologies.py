from __future__ import annotations

import io
import json
import unittest

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app
from backend.image_utils import (
    build_graph_json,
    build_graph_terminals_from_node_placements,
    detect_terminals_on_nodes,
)


class GraphTopologyTests(unittest.TestCase):
    @staticmethod
    def _project_nodes(nodes: dict[str, dict[str, object]], width: int, height: int, margin_ratio: float) -> dict[str, tuple[float, float]]:
        positions: list[tuple[str, float, float]] = []
        for node_id, node in nodes.items():
            pos = node.get("pos", [0.0, 0.0, 0.0]) if isinstance(node, dict) else [0.0, 0.0, 0.0]
            if not isinstance(pos, list | tuple) or len(pos) < 2:
                continue
            positions.append((str(node_id), float(pos[0]), float(pos[1])))
        min_x = min(p[1] for p in positions)
        max_x = max(p[1] for p in positions)
        min_y = min(p[2] for p in positions)
        max_y = max(p[2] for p in positions)
        span_x = max(1e-6, max_x - min_x)
        span_y = max(1e-6, max_y - min_y)

        margin_x = max(4.0, float(width) * max(0.04, min(0.24, margin_ratio * 1.1)))
        margin_y = max(4.0, float(height) * max(0.04, min(0.24, margin_ratio * 1.1)))
        usable_w = max(1.0, float(width) - margin_x * 2.0)
        usable_h = max(1.0, float(height) - margin_y * 2.0)

        projected: dict[str, tuple[float, float]] = {}
        for node_id, x, y in positions:
            nx = (x - min_x) / span_x if span_x > 1e-6 else 0.5
            ny = (max_y - y) / span_y if span_y > 1e-6 else 0.5
            projected[node_id] = (margin_x + nx * usable_w, margin_y + ny * usable_h)
        return projected

    def test_build_graph_json_topologies(self) -> None:
        for layout in ("cube", "star", "figure8"):
            obj = build_graph_json(
                layout=layout,
                width=6,
                height=6,
                nodes=12,
                meta={"source": "unit-test"},
            )
            self.assertEqual(obj["space"]["type"], "graph")
            self.assertEqual(obj["space"].get("topology"), layout)
            self.assertGreater(len(obj["space"]["nodes"]), 0)
            self.assertGreater(len(obj["space"]["edges"]), 0)
            terminals = obj.get("terminals", {})
            self.assertIn("A", terminals)
            self.assertEqual(len(terminals["A"]), 2)

    def test_image_generate_cube_target_emits_topology_graph(self) -> None:
        image = Image.new("RGB", (120, 120), color=(255, 255, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        file_data = buf.getvalue()

        client = TestClient(app)
        resp = client.post(
            "/image/generate",
            files={"file": ("unit_cube.png", file_data, "image/png")},
            data={
                "target_type": "cube",
                "grid_width": "6",
                "grid_height": "6",
                "auto_terminals": "false",
                "auto_classify": "false",
            },
        )
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        body = resp.json()
        graph_json = json.loads(body["text"])
        self.assertEqual(graph_json["space"]["type"], "graph")
        self.assertEqual(graph_json["space"].get("topology"), "cube")
        self.assertGreater(len(graph_json["space"]["nodes"]), 0)
        self.assertGreater(len(graph_json["space"]["edges"]), 0)
        modifier_info = body.get("detection", {}).get("modifier_info", {})
        topology_info = modifier_info.get("topology", {})
        self.assertEqual(topology_info.get("name"), "cube")

    def test_detect_terminals_on_topology_nodes(self) -> None:
        obj = build_graph_json(
            layout="cube",
            width=4,
            height=4,
            nodes=8,
            meta={"source": "unit-test"},
        )
        nodes = obj["space"]["nodes"]
        projected = self._project_nodes(nodes, width=420, height=420, margin_ratio=0.15)

        image = Image.new("RGB", (420, 420), color=(12, 12, 14))
        draw = ImageDraw.Draw(image)
        default_terminals = obj.get("terminals", {}).get("A", [])
        self.assertEqual(len(default_terminals), 2)
        chosen_ids = [str(default_terminals[0]), str(default_terminals[1])]
        for node_id in chosen_ids:
            x, y = projected[node_id]
            r = 12
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(240, 30, 30))

        placements, info = detect_terminals_on_nodes(
            image,
            nodes=nodes,
            sat_threshold=20.0,
            brightness_min=20.0,
            brightness_max=250.0,
            margin_ratio=0.15,
            cluster_threshold=45.0,
            bg_threshold=25.0,
        )
        terminals = build_graph_terminals_from_node_placements(placements)
        self.assertIn("A", terminals, msg=f"missing A terminals; info={info}")
        self.assertEqual(set(terminals["A"]), set(chosen_ids))


if __name__ == "__main__":
    unittest.main()
