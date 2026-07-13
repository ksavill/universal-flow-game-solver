from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app
from backend.image_utils import (
    apply_crop,
    auto_crop,
    classify_level_type,
    detect_grid,
    detect_warp_edges,
)


def _warp_board(*, size: int = 500) -> Image.Image:
    image = Image.new("RGB", (size, size), color=(3, 5, 10))
    draw = ImageDraw.Draw(image)
    cells = 5
    margin = int(round(size * 0.10))
    board_size = size - margin * 2
    step = board_size / cells
    inner = (65, 82, 125)
    border = (205, 220, 255)
    inner_width = max(1, int(round(size * 0.004)))
    border_width = max(3, int(round(size * 0.012)))

    x_lines = [margin + step * index for index in range(cells + 1)]
    y_lines = [margin + step * index for index in range(cells + 1)]
    for coordinate in x_lines[1:-1]:
        draw.line((coordinate, margin, coordinate, size - margin), fill=inner, width=inner_width)
    for coordinate in y_lines[1:-1]:
        draw.line((margin, coordinate, size - margin, coordinate), fill=inner, width=inner_width)

    horizontal_warp_row = 2
    vertical_warp_col = 1
    for row in range(cells):
        if row == horizontal_warp_row:
            continue
        y0 = y_lines[row]
        y1 = y_lines[row + 1]
        draw.line((margin, y0, margin, y1), fill=border, width=border_width)
        draw.line((size - margin, y0, size - margin, y1), fill=border, width=border_width)
    for col in range(cells):
        if col == vertical_warp_col:
            continue
        x0 = x_lines[col]
        x1 = x_lines[col + 1]
        draw.line((x0, margin, x1, margin), fill=border, width=border_width)
        draw.line((x0, size - margin, x1, size - margin), fill=border, width=border_width)

    dash = max(2, int(round(size * 0.012)))
    row_center = (y_lines[horizontal_warp_row] + y_lines[horizontal_warp_row + 1]) * 0.5
    col_center = (x_lines[vertical_warp_col] + x_lines[vertical_warp_col + 1]) * 0.5
    for offset in range(5, margin - 4, dash * 2):
        draw.line((margin - offset - dash, row_center, margin - offset, row_center), fill=inner, width=inner_width)
        draw.line((size - margin + offset, row_center, size - margin + offset + dash, row_center), fill=inner, width=inner_width)
        draw.line((col_center, margin - offset - dash, col_center, margin - offset), fill=inner, width=inner_width)
        draw.line((col_center, size - margin + offset, col_center, size - margin + offset + dash), fill=inner, width=inner_width)
    return image


def _place_on_canvas(board: Image.Image, *, width: int, height: int) -> Image.Image:
    canvas = Image.new("RGB", (width, height), color=(3, 5, 10))
    x = (width - board.width) // 2
    y = (height - board.height) // 2
    canvas.paste(board, (x, y))
    return canvas


def test_detects_only_paired_official_style_warp_ports() -> None:
    board = _warp_board()

    grid = detect_grid(board, threshold=230, line_threshold=0.6, invert=False)
    assert grid is not None
    assert (grid.rows, grid.cols) == (5, 5)
    warps, info = detect_warp_edges(board, rows=5, cols=5)

    assert set(warps) == {("0,2", "4,2"), ("1,0", "1,4")}
    assert info["horizontal_rows"] == [2]
    assert info["vertical_columns"] == [1]


@pytest.mark.parametrize(
    ("board_size", "canvas_size"),
    [
        (250, (1200, 360)),
        (250, (8000, 400)),
        (250, (400, 8000)),
        (500, (620, 1200)),
        (1000, (1800, 1100)),
    ],
)
def test_warp_detection_is_resolution_and_canvas_aspect_invariant(
    board_size: int,
    canvas_size: tuple[int, int],
) -> None:
    screenshot = _place_on_canvas(
        _warp_board(size=board_size),
        width=canvas_size[0],
        height=canvas_size[1],
    )

    crop = auto_crop(screenshot, threshold=230, invert=False, padding=6)
    assert crop is not None
    board = apply_crop(screenshot, crop)
    grid = detect_grid(board, threshold=230, line_threshold=0.6, invert=False)
    assert grid is not None
    assert (grid.rows, grid.cols) == (5, 5)
    warps, _info = detect_warp_edges(board, rows=grid.rows, cols=grid.cols)
    assert set(warps) == {("0,2", "4,2"), ("1,0", "1,4")}


def test_warp_import_emits_selective_typed_edges_without_changing_local_grid(
    tmp_path,
    monkeypatch,
) -> None:
    image = _warp_board()
    detection = classify_level_type(image, file_hint="screenshot.png")
    assert detection.geometry == "square"
    assert "warps" in detection.modifiers

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("screenshot.png", buffer.getvalue(), "image/png")},
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
    assert body["detection"]["modifier_info"]["warps"]["count"] == 2
    puzzle = json.loads(body["text"])
    open_edges = [
        adjacency
        for adjacency in puzzle["topology"]["adjacencies"]
        if adjacency["state"] == "open"
    ]
    assert sum(edge["kind"] == "warp" for edge in open_edges) == 2
    assert sum(edge["kind"] == "local" for edge in open_edges) == 40
