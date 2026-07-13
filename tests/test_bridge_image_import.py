from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app
from backend.image_utils import classify_level_type, detect_bridge_cells, detect_grid


def _bridge_grid_image() -> Image.Image:
    image = Image.new("RGB", (301, 301), color=(4, 6, 12))
    draw = ImageDraw.Draw(image)
    coordinates = (10, 66, 122, 178, 234, 290)
    for coordinate in coordinates:
        draw.line((coordinate, 10, coordinate, 290), fill=(75, 55, 90), width=3)
        draw.line((10, coordinate, 290, coordinate), fill=(75, 55, 90), width=3)
    center_x = (coordinates[2] + coordinates[3]) // 2
    center_y = (coordinates[2] + coordinates[3]) // 2
    draw.line((center_x - 19, center_y, center_x + 19, center_y), fill="white", width=6)
    draw.line((center_x, center_y - 19, center_x, center_y + 19), fill="white", width=6)
    centers = [int((coordinates[index] + coordinates[index + 1]) / 2) for index in range(5)]
    for x, y, color in (
        (centers[0], centers[2], (255, 30, 30)),
        (centers[4], centers[2], (255, 30, 30)),
        (centers[2], centers[0], (20, 70, 255)),
        (centers[2], centers[4], (20, 70, 255)),
    ):
        draw.ellipse((x - 15, y - 15, x + 15, y + 15), fill=color)
    return image


def _official_double_rail_bridge_image() -> Image.Image:
    image = _bridge_grid_image()
    draw = ImageDraw.Draw(image)
    coordinates = (10, 66, 122, 178, 234, 290)
    center_x = (coordinates[2] + coordinates[3]) // 2
    center_y = (coordinates[2] + coordinates[3]) // 2
    # Cover the synthetic plus, then draw the production-style pair of
    # separated horizontal bridge rails.
    draw.rectangle((center_x - 24, center_y - 24, center_x + 24, center_y + 24), fill=(4, 6, 12))
    draw.line((center_x - 23, center_y - 12, center_x + 23, center_y - 12), fill="white", width=5)
    draw.line((center_x - 23, center_y + 12, center_x + 23, center_y + 12), fill="white", width=5)
    return image


def test_detects_bridge_marker_without_confusing_grid_lines() -> None:
    image = _bridge_grid_image()
    grid = detect_grid(image, threshold=230, line_threshold=0.6, invert=False)
    assert grid is not None
    assert (grid.rows, grid.cols) == (5, 5)

    bridges, info = detect_bridge_cells(image, rows=5, cols=5)
    assert bridges == [(2, 2)]
    assert info["detected_bridges"] == 1

    level_type = classify_level_type(
        image,
        threshold=230,
        line_threshold=0.6,
        invert=False,
        file_hint="bridge-level.png",
    )
    assert level_type.geometry == "square"
    assert level_type.modifiers == ("bridges",)


def test_detects_official_double_rail_bridge_glyph() -> None:
    image = _official_double_rail_bridge_image()
    bridges, info = detect_bridge_cells(image, rows=5, cols=5)

    assert bridges == [(2, 2)]
    assert info["cells"][0]["glyph"] == "double-arch"


def test_image_import_emits_real_two_channel_bridge(tmp_path, monkeypatch) -> None:
    image = _bridge_grid_image()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))

    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("bridge-level.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "auto",
            "output_schema_version": "2",
            "auto_classify": "true",
            "auto_terminals": "true",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": str(image.width),
            "crop_height": str(image.height),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detection"]["level_type"]["modifiers"] == ["bridges"]
    assert body["detection"]["bridge_info"]["detected_bridges"] == 1

    puzzle = json.loads(body["text"])
    bridge_cells = [
        cell_id
        for cell_id, cell in puzzle["topology"]["cells"].items()
        if cell["kind"] == "bridge"
    ]
    assert len(bridge_cells) == 1
    bridge_id = bridge_cells[0]
    bridge_channels = [
        channel_id
        for channel_id, channel in puzzle["topology"]["channels"].items()
        if channel["cell"] == bridge_id
    ]
    assert len(bridge_channels) == 2
    assert "bridges" in puzzle["catalog"]["mechanics"]

    solved = TestClient(app).post(
        "/solve",
        json={"name": body["name"], "text": body["text"], "timeout_ms": 2_000},
    )
    assert solved.status_code == 200, solved.text
    paths = solved.json()["paths"]
    bridge_owners = {
        color
        for channel_id in bridge_channels
        for color, path in paths.items()
        if channel_id in path
    }
    assert len(bridge_owners) == 2
