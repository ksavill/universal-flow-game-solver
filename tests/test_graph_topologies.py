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
    build_region_crossover_channels,
    build_region_crossovers,
    detect_terminals_on_nodes,
    prune_nonterminal_region_leaves,
    repair_nonterminal_region_leaves,
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

    def test_prunes_only_the_first_pass_of_nonterminal_region_leaves(self) -> None:
        nodes = {node_id: {"pos": [index, 0, 0]} for index, node_id in enumerate("abcdef")}
        edges = [
            ("a", "d"),
            ("b", "c"),
            ("c", "d"),
            ("d", "e"),
            ("e", "f"),
            ("f", "d"),
        ]

        kept_nodes, kept_edges, removed = prune_nonterminal_region_leaves(
            nodes,
            edges,
            terminal_nodes={"a"},
        )

        self.assertEqual(removed, ["b"])
        self.assertEqual(set(kept_nodes), {"a", "c", "d", "e", "f"})
        self.assertNotIn(("b", "c"), kept_edges)
        # a is a terminal leaf and c becomes a leaf only after the one-pass
        # cleanup, so neither may be recursively peeled.
        self.assertIn("a", kept_nodes)
        self.assertIn("c", kept_nodes)

    def test_repairs_aligned_nonterminal_leaf_as_a_seam(self) -> None:
        nodes = {
            "terminal": {"data": {"pixel_center": [0.0, 0.0]}},
            "leaf": {"data": {"pixel_center": [10.0, 0.0]}},
            "continuation": {"data": {"pixel_center": [20.0, 0.0]}},
            "upper": {"data": {"pixel_center": [20.0, 10.0]}},
            "right": {"data": {"pixel_center": [30.0, 0.0]}},
        }
        edges = [
            ("terminal", "leaf"),
            ("continuation", "upper"),
            ("continuation", "right"),
            ("upper", "right"),
        ]

        repaired_edges, seams = repair_nonterminal_region_leaves(
            nodes,
            edges,
            terminal_nodes={"terminal"},
        )

        self.assertEqual(seams, [("continuation", "leaf")])
        self.assertIn(("continuation", "leaf"), repaired_edges)

    def test_repaired_region_seam_becomes_an_explicit_crossover(self) -> None:
        nodes = {
            "left": {"data": {"pixel_center": [10.0, 20.0]}},
            "right": {"data": {"pixel_center": [30.0, 20.0]}},
        }

        crossovers = build_region_crossovers(nodes, [("right", "left")])

        self.assertEqual(len(crossovers), 1)
        self.assertEqual(crossovers[0]["id"], "crossover-01")
        self.assertEqual(crossovers[0]["under"], ["left", "right"])
        self.assertEqual(crossovers[0]["center"], [20.0, 20.0])
        self.assertEqual(crossovers[0]["under_vector"], [1.0, 0.0])
        self.assertEqual(crossovers[0]["over_vector"], [-0.0, 1.0])

    def test_crossover_under_channel_cannot_turn_onto_surface(self) -> None:
        nodes = {
            "start": {"data": {"pixel_center": [-10.0, 0.0]}},
            "approach": {"data": {"pixel_center": [0.0, 0.0]}},
            "surface": {
                "data": {
                    "pixel_center": [10.0, 0.0],
                    "polygon": [[8.0, -2.0], [12.0, -2.0], [12.0, 2.0], [8.0, 2.0]],
                }
            },
            "continuation": {"data": {"pixel_center": [20.0, 0.0]}},
            "upper": {"data": {"pixel_center": [10.0, -10.0]}},
            "lower": {"data": {"pixel_center": [10.0, 10.0]}},
        }
        edges = [
            ("start", "approach"),
            ("approach", "surface"),
            ("surface", "continuation"),
            ("surface", "upper"),
            ("surface", "lower"),
        ]

        (
            split_nodes,
            split_edges,
            tiles,
            crossovers,
            rewritten_seams,
            forbidden,
            under_channels,
        ) = build_region_crossover_channels(
            nodes,
            edges,
            [("approach", "surface")],
        )

        self.assertEqual(len(crossovers), 1)
        under = crossovers[0]["under_channel"]
        self.assertEqual(under, "surface:under")
        self.assertEqual(tiles["surface"], ["surface", under])
        self.assertEqual(crossovers[0]["under_path"], ["approach", under, "continuation"])
        self.assertEqual(set(under_channels), {under})
        self.assertNotIn("polygon", split_nodes[under]["data"])

        neighbors = {node_id: set() for node_id in split_nodes}
        for left, right in split_edges:
            neighbors[left].add(right)
            neighbors[right].add(left)
        self.assertEqual(neighbors[under], {"approach", "continuation"})
        self.assertEqual(neighbors["surface"], {"upper", "lower"})
        self.assertNotIn(("approach", "surface"), split_edges)
        self.assertNotIn(("continuation", "surface"), split_edges)
        self.assertEqual(rewritten_seams, [("approach", under)])
        self.assertIn(("approach", "surface"), forbidden)
        self.assertIn(("continuation", "surface"), forbidden)

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

    def test_image_generate_cube_schema_v2_types_face_seams(self) -> None:
        image = Image.new("RGB", (120, 120), color=(255, 255, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        response = TestClient(app).post(
            "/image/generate",
            files={"file": ("unit_cube_v2.png", buf.getvalue(), "image/png")},
            data={
                "target_type": "cube",
                "grid_width": "2",
                "grid_height": "2",
                "auto_terminals": "false",
                "auto_classify": "false",
                "output_schema_version": "2",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = json.loads(response.json()["text"])
        self.assertEqual(payload["format"], "flow-solver-puzzle")
        adjacencies = payload["topology"]["adjacencies"]
        self.assertEqual(sum(item["kind"] == "seam" for item in adjacencies), 6)
        self.assertEqual(sum(item["kind"] == "local" for item in adjacencies), 12)
        self.assertIn("seam", payload["catalog"]["mechanics"])
        self.assertEqual(
            {cell["face"] for cell in payload["display"]["cells"].values()},
            {"0", "1", "2"},
        )

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

    def test_detects_pure_white_terminals_above_color_brightness_ceiling(self) -> None:
        obj = build_graph_json(
            layout="cube",
            width=2,
            height=2,
            nodes=2,
            meta={"source": "unit-test"},
        )
        nodes = obj["space"]["nodes"]
        projected = self._project_nodes(nodes, width=360, height=360, margin_ratio=0.15)
        chosen_ids = list(nodes)[:2]

        image = Image.new("RGB", (360, 360), color=(10, 10, 12))
        draw = ImageDraw.Draw(image)
        for node_id in chosen_ids:
            x, y = projected[node_id]
            draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=(255, 255, 255))

        placements, info = detect_terminals_on_nodes(
            image,
            nodes=nodes,
            sat_threshold=30.0,
            brightness_min=30.0,
            brightness_max=230.0,
            margin_ratio=0.15,
            cluster_threshold=60.0,
            bg_threshold=40.0,
        )
        terminals = build_graph_terminals_from_node_placements(placements)

        self.assertIn("A", terminals, msg=f"missing white terminals; info={info}")
        self.assertEqual(set(terminals["A"]), set(chosen_ids))

    def test_detects_low_saturation_gray_terminals_on_dark_topology(self) -> None:
        obj = build_graph_json(
            layout="cube",
            width=2,
            height=2,
            nodes=2,
            meta={"source": "unit-test"},
        )
        nodes = obj["space"]["nodes"]
        projected = self._project_nodes(nodes, width=360, height=360, margin_ratio=0.15)
        chosen_ids = list(nodes)[:2]

        image = Image.new("RGB", (360, 360), color=(10, 10, 18))
        draw = ImageDraw.Draw(image)
        for node_id in chosen_ids:
            x, y = projected[node_id]
            draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(159, 159, 189))

        placements, info = detect_terminals_on_nodes(
            image,
            nodes=nodes,
            sat_threshold=30.0,
            brightness_min=30.0,
            brightness_max=230.0,
            margin_ratio=0.15,
            cluster_threshold=60.0,
            bg_threshold=40.0,
            expected_pairs=1,
        )
        terminals = build_graph_terminals_from_node_placements(placements)

        self.assertIn("A", terminals, msg=f"missing gray terminals; info={info}")
        self.assertEqual(set(terminals["A"]), set(chosen_ids))
        self.assertEqual(info["recovered_pairs"], [])


if __name__ == "__main__":
    unittest.main()
