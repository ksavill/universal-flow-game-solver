from __future__ import annotations

import math
import unittest

from PIL import Image, ImageDraw

from backend.image_utils import detect_region_topology


class RegionTopologyDetectionTests(unittest.TestCase):
    @staticmethod
    def _draw_three_by_three_grid(stroke_width: int) -> Image.Image:
        image = Image.new("RGB", (201, 201), color="black")
        draw = ImageDraw.Draw(image)
        for coordinate in (10, 70, 130, 190):
            draw.line(
                (coordinate, 10, coordinate, 190),
                fill="white",
                width=stroke_width,
            )
            draw.line(
                (10, coordinate, 190, coordinate),
                fill="white",
                width=stroke_width,
            )
        return image

    def test_detects_rectangular_three_by_three_region_grid(self) -> None:
        image = self._draw_three_by_three_grid(stroke_width=3)

        nodes, edges, info = detect_region_topology(image)

        self.assertEqual(len(nodes), 9)
        self.assertEqual(len(edges), 12)
        self.assertEqual(info["regions"], 9)
        self.assertEqual(info["edges"], 12)
        self.assertEqual(info["max_degree"], 4)
        self.assertEqual(info["warnings"], [])

        degrees = {node_id: 0 for node_id in nodes}
        centers = {
            node_id: tuple(node["data"]["pixel_center"])
            for node_id, node in nodes.items()
        }
        for left, right in edges:
            degrees[left] += 1
            degrees[right] += 1
            x1, y1 = centers[left]
            x2, y2 = centers[right]
            # Shared sides are axis-aligned; corner-only contacts must not
            # become diagonal graph edges.
            self.assertTrue(
                math.isclose(x1, x2, abs_tol=1.0)
                or math.isclose(y1, y2, abs_tol=1.0)
            )

        self.assertEqual(sorted(degrees.values()), [2, 2, 2, 2, 3, 3, 3, 3, 4])
        self.assertEqual(
            sorted({round(center[0]) for center in centers.values()}),
            [40, 100, 160],
        )
        self.assertEqual(
            sorted({round(center[1]) for center in centers.values()}),
            [40, 100, 160],
        )
        self.assertTrue(
            all(len(node["data"]["polygon"]) >= 4 for node in nodes.values())
        )

    def test_adapts_adjacency_gap_to_barrier_width(self) -> None:
        detected = []
        for stroke_width in (1, 7):
            with self.subTest(stroke_width=stroke_width):
                nodes, edges, info = detect_region_topology(
                    self._draw_three_by_three_grid(stroke_width)
                )
                self.assertEqual(len(nodes), 9)
                self.assertEqual(len(edges), 12)
                self.assertEqual(info["max_degree"], 4)
                self.assertEqual(info["warnings"], [])
                detected.append(info)

        self.assertLess(detected[0]["adjacency_gap"], detected[1]["adjacency_gap"])
        self.assertLess(
            detected[0]["estimated_barrier_radius"],
            detected[1]["estimated_barrier_radius"],
        )

    def test_detects_two_irregular_regions_separated_by_diagonal(self) -> None:
        image = Image.new("RGB", (240, 220), color="black")
        draw = ImageDraw.Draw(image)
        outline = [(30, 30), (190, 20), (220, 105), (170, 190), (40, 180)]
        draw.line(outline + [outline[0]], fill="white", width=4, joint="curve")
        draw.line((30, 30, 170, 190), fill="white", width=4)

        nodes, edges, info = detect_region_topology(image)

        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual({endpoint for edge in edges for endpoint in edge}, set(nodes))
        self.assertEqual(info["max_degree"], 1)
        self.assertEqual(info["warnings"], [])

        # The centroids must lie on opposite sides of the diagonal, confirming
        # that the detector found the two enclosed polygons rather than the
        # surrounding background.
        side_values = []
        for node in nodes.values():
            x, y = node["data"]["pixel_center"]
            side_values.append(140.0 * (y - 30.0) - 160.0 * (x - 30.0))
            self.assertGreaterEqual(len(node["data"]["polygon"]), 3)
        self.assertLess(side_values[0] * side_values[1], 0.0)

    def test_adaptive_adjacency_ignores_isolated_enclosed_hole(self) -> None:
        image = Image.new("RGB", (320, 220), color="black")
        draw = ImageDraw.Draw(image)
        for coordinate in (10, 70, 130, 190):
            draw.line((coordinate, 10, coordinate, 190), fill="white", width=3)
            draw.line((10, coordinate, 190, coordinate), fill="white", width=3)
        # A closed cavity inside the silhouette/background is not a playable
        # cell because it never joins the dominant cell adjacency component.
        draw.rectangle((235, 75, 295, 135), outline="white", width=8)

        nodes, edges, info = detect_region_topology(image)

        self.assertEqual(len(nodes), 9)
        self.assertEqual(len(edges), 12)
        self.assertEqual(info["max_degree"], 4)
        self.assertEqual(len(info["dropped_enclosed_regions"]), 1)

    def test_adaptive_adjacency_handles_mixed_stroke_widths(self) -> None:
        image = Image.new("RGB", (201, 201), color="black")
        draw = ImageDraw.Draw(image)
        for index, coordinate in enumerate((10, 70, 130, 190)):
            width = 7 if index in {1, 3} else 2
            draw.line((coordinate, 10, coordinate, 190), fill="white", width=width)
            draw.line((10, coordinate, 190, coordinate), fill="white", width=width)

        nodes, edges, info = detect_region_topology(image)

        self.assertEqual(len(nodes), 9)
        self.assertEqual(len(edges), 12)
        self.assertEqual(info["max_degree"], 4)
        self.assertGreaterEqual(len(info["adjacency_search"]), 2)


if __name__ == "__main__":
    unittest.main()
