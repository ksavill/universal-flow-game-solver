from __future__ import annotations

import math
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from backend.image_utils import classify_level_type, detect_circle_grid, detect_circle_terminals, load_image


class CircleDetectionTests(unittest.TestCase):
    def _load_reference_circle(self):
        repo_root = Path(__file__).resolve().parents[1]
        path = repo_root / "reference_puzzle_images" / "IMG_3244.PNG"
        if not path.exists():
            self.skipTest("IMG_3244.PNG not found in reference_puzzle_images")
        return load_image(path.read_bytes())

    def test_detect_circle_grid_on_reference(self) -> None:
        image = self._load_reference_circle()
        grid, info = detect_circle_grid(image, min_sectors=3, max_sectors=32)
        self.assertIsNotNone(grid, msg=f"circle grid not detected: {info}")
        assert grid is not None
        self.assertEqual(grid.rings, 2)
        self.assertEqual(grid.sectors, 8)

    def test_detect_circle_terminals_on_reference(self) -> None:
        image = self._load_reference_circle()
        grid, info = detect_circle_grid(image, min_sectors=3, max_sectors=32)
        self.assertIsNotNone(grid, msg=f"circle grid not detected: {info}")
        assert grid is not None

        placements, term_info = detect_circle_terminals(
            image,
            rings=grid.rings,
            sectors=grid.sectors,
            sat_threshold=30.0,
            brightness_min=30.0,
            brightness_max=230.0,
            margin_ratio=0.15,
            cluster_threshold=60.0,
            bg_threshold=40.0,
            circle_grid=grid,
        )
        self.assertGreaterEqual(len(placements), 8, msg=f"expected circle terminals, got: {term_info}")
        rows = Counter(int(p.row) for p in placements)
        self.assertIn(0, rows)
        self.assertIn(1, rows)
        letters = Counter(p.letter for p in placements)
        self.assertGreaterEqual(len(letters), 4)
        self.assertTrue(all(count == 2 for count in letters.values()))

    def test_classifier_prefers_concentric_grid_over_diagonal_line_signal(self) -> None:
        image = Image.new("RGB", (520, 520), color="black")
        draw = ImageDraw.Draw(image)
        center = 260
        for radius in (70, 130, 190, 245):
            draw.ellipse(
                (center - radius, center - radius, center + radius, center + radius),
                outline=(255, 130, 150),
                width=5,
            )
        for index in range(12):
            angle = 2.0 * math.pi * index / 12.0
            draw.line(
                (
                    center + 70 * math.cos(angle),
                    center + 70 * math.sin(angle),
                    center + 245 * math.cos(angle),
                    center + 245 * math.sin(angle),
                ),
                fill=(125, 60, 70),
                width=3,
            )

        detection = classify_level_type(image)

        self.assertEqual(detection.geometry, "circle")
        self.assertEqual(detection.signals["circle_grid"]["rings"], 4)


if __name__ == "__main__":
    unittest.main()
