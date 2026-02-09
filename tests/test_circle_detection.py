from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from backend.image_utils import detect_circle_grid, detect_circle_terminals, load_image


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


if __name__ == "__main__":
    unittest.main()
