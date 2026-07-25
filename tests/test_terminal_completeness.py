from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app
from backend.image_utils import detect_terminals, parse_expected_flow_count


def _grid_image(*, gray: tuple[int, int, int] = (159, 159, 189)) -> Image.Image:
    size = 400
    cell = size // 4
    image = Image.new("RGB", (size, size), color=(18, 18, 28))
    draw = ImageDraw.Draw(image)
    for offset in range(0, size + 1, cell):
        draw.line((offset, 0, offset, size), fill=(66, 66, 82), width=3)
        draw.line((0, offset, size, offset), fill=(66, 66, 82), width=3)

    def dot(row: int, col: int, color: tuple[int, int, int]) -> None:
        center_x = col * cell + cell // 2
        center_y = row * cell + cell // 2
        radius = 24
        draw.ellipse(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            fill=color,
        )

    # The two same-side pairs balance the square board's bipartite parity.
    dot(0, 0, (225, 35, 35))
    dot(3, 3, (225, 35, 35))
    dot(1, 2, gray)
    dot(2, 1, gray)
    return image


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _detect(image: Image.Image, *, expected_pairs: int | None = None):
    return detect_terminals(
        image,
        rows=4,
        cols=4,
        sat_threshold=30.0,
        brightness_min=30.0,
        brightness_max=230.0,
        margin_ratio=0.15,
        cluster_threshold=60.0,
        bg_threshold=40.0,
        expected_pairs=expected_pairs,
        enforce_square_parity=True,
    )


def test_flow_count_parser_understands_progress_banner() -> None:
    assert parse_expected_flow_count("Level 99   Flows: 0/13   0%") == 13
    assert parse_expected_flow_count("FLOW 7 / 11") == 11
    assert parse_expected_flow_count("No progress banner here") is None


def test_dark_grid_detects_exact_gray_terminal_pair() -> None:
    placements, info = _detect(_grid_image(), expected_pairs=2)

    assert len(placements) == 4, info
    assert {(item.row, item.col) for item in placements} == {
        (0, 0),
        (3, 3),
        (1, 2),
        (2, 1),
    }
    assert len(info["clusters"]) == 2
    assert info["recovered_pairs"] == []


def test_expected_count_recovers_conservative_near_miss_pair() -> None:
    # This deliberately falls just below the neutral brightness cutoff. It is
    # retained as a near miss and may only be promoted because the advertised
    # flow count says exactly one pair is missing.
    placements, info = _detect(_grid_image(gray=(130, 130, 150)), expected_pairs=2)

    assert len(placements) == 4, info
    assert len(info["recovered_pairs"]) == 1
    assert info["recovered_pairs"][0]["method"] == "expected_flow_count"
    assert info["near_misses"] == []


def test_incomplete_generation_is_flagged_and_exact_artifact_cannot_solve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes(_grid_image())

    generated = client.post(
        "/image/generate",
        files={"file": ("gray-regression.png", source, "image/png")},
        data={
            "target_type": "square",
            "grid_width": "4",
            "grid_height": "4",
            "auto_terminals": "true",
            "auto_classify": "false",
            "expected_flow_count": "3",
            "output_schema_version": "2",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": "400",
            "crop_height": "400",
        },
    )

    assert generated.status_code == 200, generated.text
    body = generated.json()
    completeness = body["detection"]["terminal_completeness"]
    assert completeness["status"] == "incomplete"
    assert completeness["detected_pairs"] == 2
    assert completeness["expected_pairs"] == 3
    assert body["review_flagged"] is True

    record = client.get(f"/image-imports/{body['import_id']}").json()
    assert record["flagged"] is True
    assert "Detected 2 of 3" in record["flag_reason"]

    solved = client.post(
        "/solve",
        json={
            "name": body["name"],
            "text": body["text"],
            "timeout_ms": 2_000,
            "import_id": body["import_id"],
        },
    )
    assert solved.status_code == 400
    assert "terminal detection is incomplete" in solved.json()["detail"].lower()
    after_solve = client.get(f"/image-imports/{body['import_id']}").json()
    assert after_solve["solve"]["status"] == "failed"
    assert after_solve["flagged"] is True


def test_matching_expected_count_is_verified_and_not_auto_flagged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes(_grid_image())

    generated = client.post(
        "/image/generate",
        files={"file": ("gray-complete.png", source, "image/png")},
        data={
            "target_type": "square",
            "grid_width": "4",
            "grid_height": "4",
            "auto_terminals": "true",
            "auto_classify": "false",
            "expected_flow_count": "2",
            "output_schema_version": "2",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": "400",
            "crop_height": "400",
        },
    )

    assert generated.status_code == 200, generated.text
    body = generated.json()
    completeness = body["detection"]["terminal_completeness"]
    assert completeness["status"] == "verified"
    assert completeness["detected_endpoints"] == 4
    assert body.get("review_flagged") is not True
    assert client.get(f"/image-imports/{body['import_id']}").json()["flagged"] is False
