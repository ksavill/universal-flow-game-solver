from __future__ import annotations

import io
import json
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import app
from flow_solver.puzzle import Puzzle


def _line_document() -> dict[str, object]:
    return {
        "format": "flow-solver-puzzle",
        "schema_version": 2,
        "topology": {
            "template": {"id": "grid", "parameters": {"width": 3, "height": 1}},
            "cells": {key: {"kind": "ordinary"} for key in ("a", "b", "c")},
            "channels": {
                "a:main": {"cell": "a", "ports": {"E": {}}},
                "b:main": {"cell": "b", "ports": {"W": {}, "E": {}}},
                "c:main": {"cell": "c", "ports": {"W": {}}},
            },
            "adjacencies": [
                {
                    "id": "a-b",
                    "a": {"channel": "a:main", "port": "E"},
                    "b": {"channel": "b:main", "port": "W"},
                    "kind": "local",
                    "state": "open",
                },
                {
                    "id": "b-c",
                    "a": {"channel": "b:main", "port": "E"},
                    "b": {"channel": "c:main", "port": "W"},
                    "kind": "seam",
                    "state": "open",
                    "group": "face-seam",
                },
            ],
        },
        "terminals": {
            "A": {"endpoints": ["a:main", "c:main"], "color": "#ff0000"}
        },
        "display": {
            "channels": {
                "a:main": {"position": [0, 0]},
                "b:main": {"position": [1, 0]},
                "c:main": {"position": [2, 0]},
            }
        },
        "catalog": {
            "app": "flow-free-shapes",
            "variant": "shapes",
            "pack": {"id": "test", "name": "Test Pack"},
            "level": {"id": "1", "number": 1},
            "display_size": {"label": "3x1", "width": 3, "height": 1, "unit": "cells"},
            "mechanics": ["seams"],
        },
        "meta": {"author": "unit-test"},
    }


class SchemaV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.text = json.dumps(_line_document())

    def test_parse_exposes_catalog_size_and_validation(self) -> None:
        response = self.client.post(
            "/parse", json={"name": "line.json", "text": self.text}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["size_label"], "3x1")
        self.assertTrue(body["validation"]["valid"])
        self.assertEqual(body["meta"]["app"], "flow-free-shapes")
        self.assertEqual(body["meta"]["pack"], "Test Pack")

    def test_graph_payload_retains_typed_adjacencies_and_catalog(self) -> None:
        response = self.client.post(
            "/graph", json={"name": "line.json", "text": self.text}
        )
        self.assertEqual(response.status_code, 200, response.text)
        graph = response.json()["graph"]
        self.assertEqual(graph["schema_version"], 2)
        self.assertEqual(graph["catalog"]["variant"], "shapes")
        by_id = {edge["id"]: edge for edge in graph["adjacencies"]}
        self.assertEqual(by_id["b-c"]["kind"], "seam")

    def test_validate_and_solve_return_explicit_path_edges_and_stats(self) -> None:
        validation = self.client.post(
            "/validate",
            json={
                "name": "line.json",
                "text": self.text,
                "check_solvable": True,
                "timeout_ms": 2_000,
            },
        )
        self.assertEqual(validation.status_code, 200, validation.text)
        self.assertTrue(validation.json()["solvable"])

        solved = self.client.post(
            "/solve",
            json={"name": "line.json", "text": self.text, "timeout_ms": 2_000},
        )
        self.assertEqual(solved.status_code, 200, solved.text)
        body = solved.json()
        self.assertEqual(body["paths"]["A"], ["a:main", "b:main", "c:main"])
        self.assertEqual(
            body["path_edges"]["A"],
            [["a:main", "b:main"], ["b:main", "c:main"]],
        )
        self.assertGreater(body["stats"]["z3_checks"], 0)

    def test_unknown_schema_version_is_rejected_strictly(self) -> None:
        raw = _line_document()
        raw["schema_version"] = 99
        response = self.client.post(
            "/parse", json={"name": "future.json", "text": json.dumps(raw)}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported version", response.json()["detail"])

    def test_image_grid_variants_emit_canonical_schema_v2(self) -> None:
        image = Image.new("RGB", (80, 80), color=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        cases = {
            "square": ("grid", 9, 12, 0),
            "hex": ("hex_grid", 9, 16, 0),
            "circle": ("ring", 9, 15, 3),
        }
        for target, (template_id, cells, edges, seams) in cases.items():
            with self.subTest(target=target):
                response = self.client.post(
                    "/image/generate",
                    files={"file": (f"{target}.png", buffer.getvalue(), "image/png")},
                    data={
                        "target_type": target,
                        "grid_width": "3",
                        "grid_height": "3",
                        "auto_terminals": "false",
                        "auto_classify": "false",
                        "output_schema_version": "2",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(body["name"].endswith(".json"))
                payload = json.loads(body["text"])
                self.assertEqual(payload["topology"]["template"]["id"], template_id)
                self.assertEqual(len(payload["topology"]["cells"]), cells)
                self.assertEqual(len(payload["topology"]["adjacencies"]), edges)
                self.assertEqual(
                    sum(edge["kind"] == "seam" for edge in payload["topology"]["adjacencies"]),
                    seams,
                )
                self.assertEqual(payload["terminals"], {})
                self.assertEqual(len(Puzzle.from_json(body["text"]).graph.nodes), cells)


if __name__ == "__main__":
    unittest.main()
