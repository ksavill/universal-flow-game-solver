from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app
from backend.image_utils import classify_level_type, detect_grid, detect_wall_edges


def _barrier_board(*, dark_theme: bool = True) -> Image.Image:
    size = 660
    cells = 11
    step = size // cells
    background = (5, 7, 12) if dark_theme else (245, 245, 245)
    grid_color = (65, 80, 125) if dark_theme else (175, 175, 175)
    barrier_color = (150, 180, 240) if dark_theme else (25, 25, 25)
    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)

    # Two grid lines are only present as interrupted barrier segments. This is
    # the pattern that previously made an 11x11 screenshot look like 9x10.
    for index in range(cells + 1):
        coordinate = min(size - 2, index * step + 1)
        if index not in {1, 10}:
            draw.line((coordinate, 0, coordinate, size), fill=grid_color, width=2)
            draw.line((0, coordinate, size, coordinate), fill=grid_color, width=2)

    segments = ((step, step * 5), (step * 6, step * 10))
    for boundary in (step + 1, step * 10 + 1):
        for start, end in segments:
            draw.line((boundary, start, boundary, end), fill=barrier_color, width=10)
            draw.line((start, boundary, end, boundary), fill=barrier_color, width=10)
    return image


def _warp_barrier_board() -> Image.Image:
    """Boundless warp artwork with ordinary internal barrier segments."""

    size = 591
    cells = 9
    margin = 55
    board_size = 480
    step = board_size / cells
    image = Image.new("RGB", (size, size), (3, 5, 10))
    draw = ImageDraw.Draw(image)

    lines = [margin + step * index for index in range(cells + 1)]
    for coordinate in lines:
        draw.line((coordinate, margin, coordinate, margin + board_size), fill=(65, 80, 125), width=2)
        draw.line((margin, coordinate, margin + board_size, coordinate), fill=(65, 80, 125), width=2)

    barrier_color = (255, 160, 180)
    blocked_spans = ((1, 4), (5, 8))
    for boundary_index in (1, 8):
        boundary = lines[boundary_index]
        for start_index, end_index in blocked_spans:
            start = lines[start_index]
            end = lines[end_index]
            draw.line((boundary, start, boundary, end), fill=barrier_color, width=8)
            draw.line((start, boundary, end, boundary), fill=barrier_color, width=8)
    return image


def test_grid_lattice_recovers_lines_hidden_by_barriers() -> None:
    image = _barrier_board()

    grid = detect_grid(image, threshold=230, line_threshold=0.6, invert=False)

    assert grid is not None
    assert (grid.rows, grid.cols) == (11, 11)
    assert (grid.horizontal_lines, grid.vertical_lines) == (12, 12)


def test_wall_detection_supports_bright_and_dark_barriers() -> None:
    for dark_theme, expected_polarity in ((True, "bright"), (False, "dark")):
        image = _barrier_board(dark_theme=dark_theme)
        walls, info = detect_wall_edges(image, rows=11, cols=11)

        assert len(walls) >= 20
        assert info["polarity"] == expected_polarity
        assert ("0,1", "1,1") in walls
        assert ("1,0", "1,1") in walls


def test_classifier_routes_barrier_grid_to_graph_output(tmp_path: Path, monkeypatch) -> None:
    image = _barrier_board()
    detection = classify_level_type(
        image,
        threshold=230,
        line_threshold=0.6,
        invert=False,
        file_hint="barrier-level.png",
    )
    assert detection.geometry == "square"
    assert detection.modifiers == ("walls",)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("barrier-level.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "auto",
            "auto_classify": "true",
            "auto_terminals": "false",
            "output_schema_version": "2",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": str(image.width),
            "crop_height": str(image.height),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detection"]["target_type_used"] == "graph"
    assert body["detection"]["grid"]["rows"] == 11
    assert body["detection"]["grid"]["cols"] == 11
    wall_count = body["detection"]["modifier_info"]["walls"]["detected_walls"]
    assert wall_count >= 20

    puzzle = json.loads(body["text"])
    assert len(puzzle["topology"]["cells"]) == 121
    assert sum(
        adjacency["state"] == "blocked"
        for adjacency in puzzle["topology"]["adjacencies"]
    ) == wall_count


def test_warp_puzzle_keeps_detected_barriers(tmp_path: Path, monkeypatch) -> None:
    image = _warp_barrier_board()
    detection = classify_level_type(image, file_hint="warp-level.png")

    assert detection.geometry == "square"
    assert detection.modifiers == ("walls", "warps")
    assert detection.signals["warp_detection"]["count"] == 18
    assert detection.signals["wall_detection"]["detected_walls"] == 24

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("warp-level.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "auto",
            "auto_classify": "true",
            "auto_terminals": "false",
            "output_schema_version": "2",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": str(image.width),
            "crop_height": str(image.height),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detection"]["target_type_used"] == "graph"
    assert body["detection"]["modifier_info"]["warps"]["count"] == 18
    assert body["detection"]["modifier_info"]["walls"]["detected_walls"] == 24

    puzzle = json.loads(body["text"])
    adjacencies = puzzle["topology"]["adjacencies"]
    assert sum(edge["kind"] == "warp" for edge in adjacencies) == 18
    assert sum(edge["state"] == "blocked" for edge in adjacencies) == 24
