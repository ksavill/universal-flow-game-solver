from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flow_solver.migration import migrate_file, puzzle_to_spec
from flow_solver.puzzle import Puzzle
from flow_solver.schema_v2 import PuzzleSpec


class SchemaMigrationTests(unittest.TestCase):
    def test_square_bridge_runtime_round_trip_preserves_solver_graph(self) -> None:
        original = Puzzle.from_flow_text(
            """\
# type: square
# fill: true
# pack: Bridge Test
# terminal_colors: {"A":"#ff0000","B":"#00ff00"}
#B#
A+A
#B#
"""
        )
        spec = puzzle_to_spec(
            original,
            template_id="square-grid",
            template_parameters={"width": 3, "height": 3},
        )
        migrated = Puzzle.from_json(spec.to_json())

        self.assertEqual(set(migrated.graph.nodes), set(original.graph.nodes))
        self.assertEqual(set(migrated.graph.edges()), set(original.graph.edges()))
        self.assertEqual(migrated.tiles, original.tiles)
        self.assertEqual(migrated.terminals, original.terminals)
        self.assertEqual(migrated.fill, original.fill)
        self.assertEqual(spec.catalog.display_size.label, "3x3")
        self.assertIn("bridges", spec.catalog.mechanics)
        self.assertEqual(spec.terminals["A"].color, "#ff0000")
        self.assertEqual(spec.terminals["B"].color, "#00ff00")

    def test_pair_string_terminal_colors_are_migrated_case_insensitively(self) -> None:
        original = Puzzle.from_flow_text(
            """\
# type: square
# Terminal_Colours: a='#0f0'; B:#0000ff
A.B
...
A.B
"""
        )

        spec = puzzle_to_spec(original)

        self.assertEqual(spec.terminals["A"].color, "#0f0")
        self.assertEqual(spec.terminals["B"].color, "#0000ff")

    def test_json_list_terminal_color_records_are_migrated(self) -> None:
        original = Puzzle.from_flow_text(
            """\
# type: square
# terminal_colors: [{"letter":"A","color":"#123456"},{"color_id":"B","color":"gold"}]
A.B
...
A.B
"""
        )

        spec = puzzle_to_spec(original)

        self.assertEqual(spec.terminals["A"].color, "#123456")
        self.assertEqual(spec.terminals["B"].color, "gold")

    def test_mapping_records_and_custom_node_metadata_survive_round_trip(self) -> None:
        raw = {
            "fill": False,
            "space": {
                "type": "graph",
                "nodes": {
                    "left": {
                        "pos": [1.25, -2.5, 0.75],
                        "kind": "region",
                        "data": {"polygon": [[0, 0], [1, 0], [1, 1]], "confidence": 0.9},
                    },
                    "right": {
                        "pos": [4, -2, 0],
                        "kind": "region",
                        "data": {"polygon": [[3, 0], [4, 0], [4, 1]], "confidence": 0.8},
                    },
                },
                "edges": [["left", "right"]],
            },
            "terminals": {"A": ["left", "right"]},
            "meta": {
                "terminal_colors": {"A": {"hex": "#abcdef"}},
                "import": {"detector": "regions", "threshold": 0.87},
            },
        }
        original = Puzzle.from_json(json.dumps(raw))

        migrated = puzzle_to_spec(original).compile()

        self.assertEqual(migrated.graph.nodes["left"].pos, original.graph.nodes["left"].pos)
        self.assertEqual(migrated.graph.nodes["left"].kind, "region")
        self.assertEqual(
            migrated.graph.nodes["left"].data["polygon"],
            original.graph.nodes["left"].data["polygon"],
        )
        self.assertEqual(migrated.graph.nodes["left"].data["confidence"], 0.9)
        self.assertEqual(migrated.meta["import"], raw["meta"]["import"])
        self.assertEqual(migrated.source_spec.terminals["A"].color, "#abcdef")

    def test_legacy_graph_typed_edge_mapping_is_preserved(self) -> None:
        raw = {
            "fill": False,
            "space": {
                "type": "graph",
                "nodes": {
                    "a": {"pos": [0, 0]},
                    "b": {"pos": [1, 0]},
                    "c": {"pos": [2, 0]},
                },
                "edges": [["a", "b"]],
                "warps": [["b", "c"]],
            },
            "terminals": {"A": ["a", "c"]},
        }
        puzzle = Puzzle.from_json(json.dumps(raw))
        spec = puzzle_to_spec(puzzle, edge_kinds={("b", "c"): "warp"})
        kinds = {edge.id: edge.kind for edge in spec.topology.adjacencies}
        self.assertIn("warp", kinds.values())
        self.assertEqual(set(spec.compile().graph.edges()), {("a", "b"), ("b", "c")})

    def test_file_migration_recovers_warps_and_generic_added_edges(self) -> None:
        raw = {
            "fill": False,
            "space": {
                "type": "graph",
                "nodes": {
                    "a": {"pos": [0, 0]},
                    "b": {"pos": [1, 0]},
                    "c": {"pos": [2, 0]},
                    "d": {"pos": [3, 0]},
                },
                "edges": [["a", "b"]],
                "edge_overrides": {
                    "add": [["b", "c"], ["c", "d"]],
                    "remove": [["a", "d"]],
                },
                "warps": [["c", "d"]],
                "walls": [["a", "d"]],
            },
            "terminals": {"A": ["a", "d"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy.json"
            output = Path(directory) / "canonical.json"
            source.write_text(json.dumps(raw), encoding="utf-8")

            migrate_file(source, output)
            spec = PuzzleSpec.from_json(output.read_text(encoding="utf-8"))

        kinds = {
            frozenset((adjacency.a.channel, adjacency.b.channel)): adjacency.kind
            for adjacency in spec.topology.adjacencies
        }
        states = {
            frozenset((adjacency.a.channel, adjacency.b.channel)): adjacency.state
            for adjacency in spec.topology.adjacencies
        }
        self.assertEqual(kinds[frozenset(("a", "b"))], "local")
        self.assertEqual(kinds[frozenset(("b", "c"))], "custom")
        self.assertEqual(kinds[frozenset(("c", "d"))], "warp")
        self.assertEqual(states[frozenset(("a", "d"))], "blocked")
        self.assertIn("walls", spec.catalog.mechanics)
        self.assertEqual(
            set(spec.compile().graph.edges()),
            {("a", "b"), ("b", "c"), ("c", "d")},
        )

    def test_existing_v2_document_is_canonicalized_without_loss(self) -> None:
        original = Puzzle.from_flow_text(
            """\
# type: square
AA
"""
        )
        first = puzzle_to_spec(original)
        compiled = first.compile()
        second = puzzle_to_spec(compiled)
        self.assertEqual(second.to_json(), first.to_json())

    def test_circle_file_migration_recovers_ring_parameters_and_seams(self) -> None:
        source_text = """\
# type: circle
A..B
....
A..B
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "level.flow"
            output = Path(directory) / "canonical.json"
            source.write_text(source_text, encoding="utf-8")

            migrate_file(source, output)
            spec = PuzzleSpec.from_json(output.read_text(encoding="utf-8"))

        self.assertEqual(spec.topology.template.id, "ring")
        self.assertEqual(
            spec.topology.template.parameters,
            {"core": False, "rings": 3, "sectors": 4},
        )
        seams = [
            adjacency
            for adjacency in spec.topology.adjacencies
            if adjacency.kind == "seam"
        ]
        self.assertEqual(len(seams), 3)
        self.assertIn("seam", spec.catalog.mechanics)
        self.assertEqual(sum(1 for _edge in spec.compile().graph.edges()), 20)

    def test_hex_file_migration_recovers_template_without_filename_hint(self) -> None:
        source_text = """\
# type: hex
A.B
...
A.B
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "level.flow"
            output = Path(directory) / "canonical.json"
            source.write_text(source_text, encoding="utf-8")

            migrate_file(source, output)
            spec = PuzzleSpec.from_json(output.read_text(encoding="utf-8"))

        self.assertEqual(spec.topology.template.id, "hex-grid")
        self.assertEqual(
            spec.topology.template.parameters,
            {"height": 3, "width": 3},
        )
        self.assertEqual(spec.catalog.display_size.label, "3x3")


if __name__ == "__main__":
    unittest.main()
