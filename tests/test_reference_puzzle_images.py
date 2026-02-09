from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from backend.image_utils import auto_crop, classify_level_type, detect_grid, detect_terminals, load_image


def _expected_geometry_from_name(name: str) -> str | None:
    lowered = name.lower()
    if "figure8" in lowered or "figure_8" in lowered or "infinity" in lowered:
        return "figure8"
    if "cube" in lowered:
        return "cube"
    if "star" in lowered:
        return "star"
    if "hex" in lowered:
        return "hex"
    if "circle" in lowered or "ring" in lowered:
        return "circle"
    if "graph" in lowered or "freeform" in lowered:
        return "graph"
    if "square" in lowered or "grid" in lowered:
        return "square"
    return None


class ReferencePuzzleImageClassifierTests(unittest.TestCase):
    def test_special_shape_references_rank_expected_geometries(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        ref_dir = repo_root / "reference_puzzle_images"
        expected = {
            "IMG_3241.PNG": "star",
            "IMG_3244.PNG": "circle",
            "IMG_3243.PNG": "cube",
            "IMG_3245.PNG": "figure8",
            "IMG_3246.PNG": "figure8",
        }
        available = [(name, geom) for name, geom in expected.items() if (ref_dir / name).exists()]
        if not available:
            self.skipTest("No special-shape reference images found for classification regression checks")

        for name, geometry in available:
            image = load_image((ref_dir / name).read_bytes())
            detection = classify_level_type(image, file_hint=name)
            top2 = [cand.geometry for cand in detection.candidates[:2]]
            self.assertIn(
                geometry,
                top2,
                msg=f"{name}: expected {geometry} in top2, got {top2}",
            )

    def test_special_shape_references_auto_generate_topology_targets(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        ref_dir = repo_root / "reference_puzzle_images"
        expected = {
            "IMG_3241.PNG": "star",
            "IMG_3244.PNG": "circle",
            "IMG_3243.PNG": "cube",
            "IMG_3245.PNG": "figure8",
            "IMG_3246.PNG": "figure8",
        }
        available = [(name, geom) for name, geom in expected.items() if (ref_dir / name).exists()]
        if not available:
            self.skipTest("No special-shape reference images found for auto-generate regression checks")

        client = TestClient(app)
        for name, geometry in available:
            payload = (ref_dir / name).read_bytes()
            resp = client.post(
                "/image/generate",
                files={"file": (name, payload, "image/png")},
                data={
                    "target_type": "auto",
                    "auto_classify": "true",
                    "auto_terminals": "true",
                },
            )
            self.assertEqual(resp.status_code, 200, msg=f"{name}: {resp.text}")
            body = resp.json()
            target_used = str(body.get("detection", {}).get("target_type_used", ""))
            self.assertEqual(target_used, geometry, msg=f"{name}: expected target {geometry}, got {target_used}")

    def test_auto_crop_finds_board_region_on_reference(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        ref_dir = repo_root / "reference_puzzle_images"
        image_path = ref_dir / "IMG_3241.PNG"
        if not image_path.exists():
            self.skipTest("IMG_3241.PNG not found for auto-crop regression check")

        image = load_image(image_path.read_bytes())
        crop = auto_crop(image, threshold=230, invert=False, padding=8)
        self.assertIsNotNone(crop, "Auto-crop should find a board region on the reference image.")
        assert crop is not None
        ratio = float(crop.width * crop.height) / float(max(1, image.width * image.height))
        self.assertGreater(ratio, 0.20, "Auto-crop region is unexpectedly tiny.")
        self.assertLess(ratio, 0.90, "Auto-crop should not return near full-frame for this reference.")

        square_path = ref_dir / "IMG_3202.PNG"
        if square_path.exists():
            square_image = load_image(square_path.read_bytes())
            square_crop = auto_crop(square_image, threshold=230, invert=False, padding=8)
            self.assertIsNotNone(square_crop, "Auto-crop should find a board region on square reference.")
            assert square_crop is not None
            square_ratio = float(square_crop.width * square_crop.height) / float(
                max(1, square_image.width * square_image.height)
            )
            self.assertLess(
                square_ratio,
                0.70,
                "Auto-crop should tighten around the board and avoid near full-screen square crops.",
            )

    def test_reference_images_run_classifier_and_detectors(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        ref_dir = repo_root / "reference_puzzle_images"
        if not ref_dir.exists():
            self.skipTest("reference_puzzle_images folder is not present")

        images = [
            path
            for path in ref_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        if not images:
            self.skipTest("reference_puzzle_images does not contain image files")

        non_square = 0
        grid_hits = 0
        terminal_hits = 0
        named_expectations = 0
        name_mismatches: list[str] = []
        cv_warnings = 0

        for image_path in sorted(images):
            image = load_image(image_path.read_bytes())
            detection = classify_level_type(image, file_hint=image_path.name)
            if detection.geometry != "square":
                non_square += 1

            if any("OpenCV is unavailable" in warning for warning in detection.warnings):
                cv_warnings += 1

            expected = _expected_geometry_from_name(image_path.name)
            if expected is not None:
                named_expectations += 1
                top2 = [cand.geometry for cand in detection.candidates[:2]]
                if expected not in top2:
                    name_mismatches.append(f"{image_path.name}: expected {expected}, got {top2}")

            grid = detect_grid(image, threshold=230, line_threshold=0.6, invert=False)
            if grid is None:
                continue
            grid_hits += 1
            terminals, _info = detect_terminals(
                image,
                rows=int(grid.rows),
                cols=int(grid.cols),
                sat_threshold=30.0,
                brightness_min=30.0,
                brightness_max=230.0,
                margin_ratio=0.15,
                cluster_threshold=60.0,
                bg_threshold=40.0,
            )
            if terminals:
                terminal_hits += 1

        self.assertEqual(cv_warnings, 0, "Classifier ran without OpenCV support on reference-image validation.")
        self.assertGreaterEqual(non_square, 1, "Expected at least one non-square geometry across reference images.")
        self.assertGreaterEqual(grid_hits, max(1, len(images) // 3), "Grid detection hit rate is too low.")
        self.assertGreaterEqual(
            terminal_hits,
            max(1, grid_hits // 2),
            "Terminal detection hit rate is too low on grid-detected images.",
        )
        if named_expectations > 0:
            self.assertFalse(
                name_mismatches,
                msg="Classifier mismatches on filename-labeled references:\n" + "\n".join(name_mismatches),
            )


if __name__ == "__main__":
    unittest.main()
