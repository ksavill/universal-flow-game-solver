from __future__ import annotations

import copy
import json
import unittest

from flow_solver.puzzle import Puzzle
from flow_solver.schema_v2 import PuzzleSpec, SchemaV2Error, parse_v2_json
from flow_solver.solver.z3_solver import solve_with_z3


def _base_document() -> dict[str, object]:
    return {
        "format": "flow-solver-puzzle",
        "schema_version": 2,
        "topology": {
            "template": {"id": "line-with-warp", "parameters": {"length": 3}},
            "cells": {
                "a": {"kind": "ordinary"},
                "b": {"kind": "ordinary"},
                "c": {"kind": "ordinary"},
            },
            "channels": {
                "a:main": {
                    "cell": "a",
                    "kind": "cell",
                    "ports": {"E": {}, "warp": {"kind": "warp-port"}},
                },
                "b:main": {
                    "cell": "b",
                    "kind": "cell",
                    "ports": {"W": {}, "E": {}},
                },
                "c:main": {
                    "cell": "c",
                    "kind": "cell",
                    "ports": {"W": {}, "warp": {"kind": "warp-port"}},
                },
            },
            "adjacencies": [
                {
                    "id": "local-a-b",
                    "a": {"channel": "a:main", "port": "E"},
                    "b": {"channel": "b:main", "port": "W"},
                    "kind": "local",
                    "state": "open",
                },
                {
                    "id": "wall-b-c",
                    "a": {"channel": "b:main", "port": "E"},
                    "b": {"channel": "c:main", "port": "W"},
                    "kind": "local",
                    "state": "blocked",
                },
                {
                    "id": "warp-a-c",
                    "a": {"channel": "a:main", "port": "warp"},
                    "b": {"channel": "c:main", "port": "warp"},
                    "kind": "warp",
                    "state": "open",
                    "group": "warp-1",
                },
            ],
        },
        "terminals": {
            "A": {
                "endpoints": ["a:main", "c:main"],
                "color": "#ff0000",
            }
        },
        "rules": {
            "coverage": {"mode": "all-cells", "overrides": {}},
            "paths": {"endpoint_degree": 1, "internal_degree": 2, "connected": True},
            "multi_channel_cell_color_policy": "distinct",
        },
        "display": {
            "dimension": 2,
            "layers": ["board", "warps"],
            "cells": {
                "a": {"position": [0, 0], "layer": "board"},
                "b": {"position": [1, 0], "layer": "board"},
                "c": {"position": [2, 0], "layer": "board"},
            },
            "channels": {
                "b:main": {"position": [1, 0.25, 0], "layer": "board"}
            },
            "ports": {
                "a:main": {
                    "warp": {"position": [0, -0.25], "normal": [-1, 0], "layer": "warps"}
                }
            },
            "adjacencies": {
                "warp-a-c": {
                    "points": [[0, -0.25], [1, -1], [2, -0.25]],
                    "layer": "warps",
                }
            },
        },
        "catalog": {
            "app": "flow-free-warps",
            "variant": "warps",
            "pack": {"id": "test-pack", "name": "Test Pack"},
            "level": {"id": "level-1", "number": 1},
            "mode": "free-play",
            "display_size": {"label": "3x1", "width": 3, "height": 1, "unit": "cells"},
            "mechanics": ["walls", "warps"],
        },
        "meta": {"author": "unit-test", "nested": {"kept": True}},
        "extensions": {"example.org/test": {"confidence": 0.9}},
    }


def _bridge_document() -> dict[str, object]:
    cells = {
        "left": {"kind": "ordinary"},
        "bridge": {"kind": "bridge"},
        "right": {"kind": "ordinary"},
        "top": {"kind": "ordinary"},
        "bottom": {"kind": "ordinary"},
    }
    channels = {
        "left:main": {"cell": "left", "ports": {"E": {}}},
        "bridge:h": {"cell": "bridge", "kind": "bridge-horizontal", "ports": {"W": {}, "E": {}}},
        "right:main": {"cell": "right", "ports": {"W": {}}},
        "top:main": {"cell": "top", "ports": {"S": {}}},
        "bridge:v": {"cell": "bridge", "kind": "bridge-vertical", "ports": {"N": {}, "S": {}}},
        "bottom:main": {"cell": "bottom", "ports": {"N": {}}},
    }

    def adjacency(
        adjacency_id: str,
        a_channel: str,
        a_port: str,
        b_channel: str,
        b_port: str,
    ) -> dict[str, object]:
        return {
            "id": adjacency_id,
            "a": {"channel": a_channel, "port": a_port},
            "b": {"channel": b_channel, "port": b_port},
            "kind": "local",
            "state": "open",
        }

    return {
        "format": "flow-solver-puzzle",
        "schema_version": 2,
        "topology": {
            "cells": cells,
            "channels": channels,
            "adjacencies": [
                adjacency("left-h", "left:main", "E", "bridge:h", "W"),
                adjacency("h-right", "bridge:h", "E", "right:main", "W"),
                adjacency("top-v", "top:main", "S", "bridge:v", "N"),
                adjacency("v-bottom", "bridge:v", "S", "bottom:main", "N"),
            ],
        },
        "terminals": {
            "A": {"endpoints": ["left:main", "right:main"]},
            "B": {"endpoints": ["top:main", "bottom:main"]},
        },
        "display": {
            "channels": {
                "left:main": {"position": [-1, 0, 0]},
                "bridge:h": {"position": [0, 0, 0.1]},
                "right:main": {"position": [1, 0, 0]},
                "top:main": {"position": [0, 1, 0]},
                "bridge:v": {"position": [0, 0, -0.1]},
                "bottom:main": {"position": [0, -1, 0]},
            }
        },
    }


class SchemaV2Tests(unittest.TestCase):
    def test_parse_round_trip_and_compile_typed_adjacencies(self) -> None:
        raw = _base_document()
        spec = PuzzleSpec.from_dict(raw)

        canonical_text = spec.to_json()
        self.assertEqual(canonical_text, spec.to_json())
        self.assertEqual(parse_v2_json(canonical_text), spec)

        canonical = json.loads(canonical_text)
        self.assertEqual(canonical["format"], "flow-solver-puzzle")
        self.assertEqual(canonical["schema_version"], 2)
        self.assertEqual(
            [item["id"] for item in canonical["topology"]["adjacencies"]],
            ["local-a-b", "wall-b-c", "warp-a-c"],
        )

        puzzle = Puzzle.from_json(canonical_text)
        self.assertIsNotNone(puzzle.source_spec)
        self.assertEqual(puzzle.tiles, {"a": ["a:main"], "b": ["b:main"], "c": ["c:main"]})
        self.assertEqual(puzzle.terminals, {"A": ("a:main", "c:main")})
        self.assertTrue(puzzle.fill)
        self.assertEqual(puzzle.graph.nodes["a:main"].pos, (0.0, 0.0, 0.0))
        self.assertEqual(puzzle.graph.nodes["b:main"].pos, (1.0, 0.25, 0.0))
        self.assertEqual(set(puzzle.graph.edges()), {("a:main", "b:main"), ("a:main", "c:main")})
        self.assertEqual(puzzle.meta["terminal_colors"], {"A": "#ff0000"})
        self.assertEqual(puzzle.meta["nested"], {"kept": True})

    def test_bridge_channels_compile_independently_and_solve(self) -> None:
        puzzle = Puzzle.from_json(json.dumps(_bridge_document()))

        self.assertEqual(puzzle.tiles["bridge"], ["bridge:h", "bridge:v"])
        self.assertNotIn("bridge:v", puzzle.graph.neighbors("bridge:h"))
        self.assertEqual(puzzle.graph.degree("bridge:h"), 2)
        self.assertEqual(puzzle.graph.degree("bridge:v"), 2)

        result = solve_with_z3(puzzle, timeout_ms=2_000)
        self.assertEqual(result.paths["A"], ["left:main", "bridge:h", "right:main"])
        self.assertEqual(result.paths["B"], ["top:main", "bridge:v", "bottom:main"])

    def test_optional_coverage_maps_to_fill_false(self) -> None:
        raw = _base_document()
        raw["rules"]["coverage"]["mode"] = "optional"  # type: ignore[index]
        puzzle = Puzzle.from_json(json.dumps(raw))
        self.assertFalse(puzzle.fill)

    def test_legacy_json_dispatch_is_unchanged(self) -> None:
        legacy = {
            "space": {
                "type": "graph",
                "nodes": {"0": {"pos": [0, 0]}, "1": {"pos": [1, 0]}},
                "edges": [["0", "1"]],
            },
            "terminals": {"A": ["0", "1"]},
            "fill": False,
            "meta": {"legacy": True},
        }
        puzzle = Puzzle.from_json(json.dumps(legacy))
        self.assertIsNone(puzzle.source_spec)
        self.assertEqual(set(puzzle.graph.edges()), {("0", "1")})
        self.assertEqual(puzzle.terminals, {"A": ("0", "1")})
        self.assertFalse(puzzle.fill)
        self.assertEqual(puzzle.meta, {"legacy": True})

    def test_unknown_version_and_structural_fields_are_rejected(self) -> None:
        bad_version = _base_document()
        bad_version["schema_version"] = 3
        with self.assertRaisesRegex(SchemaV2Error, r"\$\.schema_version: unsupported version 3"):
            Puzzle.from_json(json.dumps(bad_version))

        unknown_field = _base_document()
        unknown_field["topology"]["typo"] = True  # type: ignore[index]
        with self.assertRaisesRegex(SchemaV2Error, r"\$\.topology: unknown field\(s\): typo"):
            PuzzleSpec.from_dict(unknown_field)

    def test_reference_and_terminal_validation(self) -> None:
        cases: list[tuple[str, dict[str, object], str]] = []

        missing_cell = _base_document()
        missing_cell["topology"]["channels"]["a:main"]["cell"] = "missing"  # type: ignore[index]
        cases.append(("missing cell", missing_cell, "references unknown cell"))

        missing_port = _base_document()
        missing_port["topology"]["adjacencies"][0]["a"]["port"] = "missing"  # type: ignore[index]
        cases.append(("missing port", missing_port, "references unknown port"))

        duplicate_terminal = _base_document()
        duplicate_terminal["terminals"]["B"] = {"endpoints": ["a:main", "b:main"]}  # type: ignore[index]
        cases.append(("duplicate terminal", duplicate_terminal, "already a terminal"))

        duplicate_adjacency = _base_document()
        duplicate_adjacency["topology"]["adjacencies"].append(  # type: ignore[index]
            copy.deepcopy(duplicate_adjacency["topology"]["adjacencies"][0])  # type: ignore[index]
        )
        cases.append(("duplicate adjacency", duplicate_adjacency, "adjacency id is duplicated"))

        bad_layer = _base_document()
        bad_layer["display"]["cells"]["a"]["layer"] = "missing"  # type: ignore[index]
        cases.append(("bad layer", bad_layer, "undeclared display layer"))

        for label, raw, message in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(SchemaV2Error, message):
                    PuzzleSpec.from_dict(raw)

    def test_port_reuse_and_parallel_enabled_edges_are_rejected(self) -> None:
        reused_port = _base_document()
        reused_port["topology"]["adjacencies"][1]["state"] = "open"  # type: ignore[index]
        reused_port["topology"]["adjacencies"][1]["a"]["port"] = "W"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaV2Error, "open port"):
            PuzzleSpec.from_dict(reused_port)

        parallel = _base_document()
        parallel["topology"]["channels"]["a:main"]["ports"]["parallel"] = {}  # type: ignore[index]
        parallel["topology"]["channels"]["b:main"]["ports"]["parallel"] = {}  # type: ignore[index]
        parallel["topology"]["adjacencies"].append(  # type: ignore[index]
            {
                "id": "parallel-a-b",
                "a": {"channel": "a:main", "port": "parallel"},
                "b": {"channel": "b:main", "port": "parallel"},
                "kind": "seam",
                "state": "open",
            }
        )
        with self.assertRaisesRegex(SchemaV2Error, "parallel enabled adjacencies"):
            PuzzleSpec.from_dict(parallel)

    def test_compiler_applies_per_cell_coverage_overrides(self) -> None:
        raw = _base_document()
        raw["rules"]["coverage"]["overrides"] = {  # type: ignore[index]
            "b": {"min_used_channels": 0, "max_used_channels": 0}
        }
        spec = PuzzleSpec.from_dict(raw)
        puzzle = spec.compile()
        self.assertEqual(puzzle.cell_coverage_bounds("a"), (1, 1))
        self.assertEqual(puzzle.cell_coverage_bounds("b"), (0, 0))
        result = solve_with_z3(puzzle, timeout_ms=2_000)
        self.assertNotIn("b:main", result.paths["A"])

    def test_compiler_supports_multi_channel_coverage_policy(self) -> None:
        raw = _bridge_document()
        raw["rules"] = {
            "coverage": {
                "mode": "all-cells",
                "overrides": {"bridge": {"min_used_channels": 2, "max_used_channels": 2}},
            },
            "multi_channel_cell_color_policy": "allow",
        }
        puzzle = Puzzle.from_json(json.dumps(raw))
        self.assertEqual(puzzle.cell_coverage_bounds("bridge"), (2, 2))
        self.assertEqual(puzzle.multi_channel_cell_color_policy, "allow")
        result = solve_with_z3(puzzle, timeout_ms=2_000)
        self.assertEqual({result.node_color["bridge:h"], result.node_color["bridge:v"]}, {"A", "B"})

    def test_declared_path_length_rules_are_solved_and_independently_validated(self) -> None:
        raw = _base_document()
        raw["rules"]["coverage"]["overrides"] = {  # type: ignore[index]
            "b": {"min_used_channels": 0, "max_used_channels": 0}
        }
        raw["rules"]["paths"]["minimum_nodes"] = 2  # type: ignore[index]
        raw["rules"]["paths"]["maximum_nodes"] = 2  # type: ignore[index]
        puzzle = Puzzle.from_json(json.dumps(raw))
        result = solve_with_z3(puzzle, timeout_ms=2_000)
        self.assertEqual(len(result.paths["A"]), 2)

        raw["rules"]["paths"]["minimum_nodes"] = 3  # type: ignore[index]
        raw["rules"]["paths"].pop("maximum_nodes")  # type: ignore[index]
        impossible = Puzzle.from_json(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "UNSAT"):
            solve_with_z3(impossible, timeout_ms=2_000)


if __name__ == "__main__":
    unittest.main()
