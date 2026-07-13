from __future__ import annotations

import io
import json
import math

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app import app


def _star_region_image() -> Image.Image:
    image = Image.new("RGB", (401, 401), color="black")
    draw = ImageDraw.Draw(image)
    center = (200, 205)
    outer = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        radius = 175 if index % 2 == 0 else 82
        outer.append(
            (
                center[0] + radius * math.cos(angle),
                center[1] + radius * math.sin(angle),
            )
        )
    draw.line(outer + [outer[0]], fill=(255, 120, 145), width=7, joint="curve")
    for point in outer[::2]:
        draw.line((center[0], center[1], point[0], point[1]), fill=(150, 70, 85), width=3)
    return image


def test_image_generate_emits_exact_region_graph() -> None:
    image = Image.new("RGB", (201, 201), color="black")
    draw = ImageDraw.Draw(image)
    for coordinate in (10, 70, 130, 190):
        draw.line((coordinate, 10, coordinate, 190), fill="white", width=3)
        draw.line((10, coordinate, 190, coordinate), fill="white", width=3)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("regions.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "graph",
            "graph_layout": "regions",
            "output_schema_version": "2",
            "auto_terminals": "false",
            "auto_classify": "false",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": "201",
            "crop_height": "201",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    puzzle = json.loads(body["text"])
    assert body["name"] == "regions_regions_9.json"
    assert puzzle["format"] == "flow-solver-puzzle"
    assert puzzle["schema_version"] == 2
    assert puzzle["topology"]["template"]["id"] == "regions"
    assert len(puzzle["topology"]["cells"]) == 9
    assert len(puzzle["topology"]["adjacencies"]) == 12
    assert all(
        adjacency["kind"] == "local" and adjacency["state"] == "open"
        for adjacency in puzzle["topology"]["adjacencies"]
    )
    assert puzzle["terminals"] == {}
    assert puzzle["catalog"]["variant"] == "shapes"
    assert "irregular-regions" in puzzle["catalog"]["mechanics"]
    assert all(
        len(cell_display["polygon"]) >= 3
        for cell_display in puzzle["display"]["cells"].values()
    )
    assert body["detection"]["modifier_info"]["regions"]["max_degree"] == 4


def test_auto_classification_routes_freeform_cells_to_regions(tmp_path, monkeypatch) -> None:
    image = _star_region_image()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))

    response = TestClient(app).post(
        "/image/generate",
        files={"file": ("daily-shape.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "auto",
            "output_schema_version": "2",
            "auto_terminals": "false",
            "auto_classify": "true",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": str(image.width),
            "crop_height": str(image.height),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detection"]["target_type_used"] == "graph"
    assert body["detection"]["graph_layout_auto_selected"] == "regions"
    assert body["detection"]["level_type"]["signals"]["recommended_graph_layout"] == "regions"
    puzzle = json.loads(body["text"])
    assert puzzle["topology"]["template"]["id"] == "regions"
    assert len(puzzle["topology"]["cells"]) >= 5


def test_region_graph_preserves_detected_screenshot_colors(tmp_path, monkeypatch) -> None:
    image = Image.new("RGB", (201, 201), color="black")
    draw = ImageDraw.Draw(image)
    for coordinate in (10, 70, 130, 190):
        draw.line((coordinate, 10, coordinate, 190), fill="white", width=3)
        draw.line((10, coordinate, 190, coordinate), fill="white", width=3)
    screenshot_blue = (12, 40, 250)
    for x, y in ((40, 40), (160, 160)):
        draw.ellipse((x - 15, y - 15, x + 15, y + 15), fill=screenshot_blue)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    response = client.post(
        "/image/generate",
        files={"file": ("colored-regions.png", buffer.getvalue(), "image/png")},
        data={
            "target_type": "graph",
            "graph_layout": "regions",
            "output_schema_version": "2",
            "auto_terminals": "true",
            "auto_classify": "false",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": str(image.width),
            "crop_height": str(image.height),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    puzzle = json.loads(body["text"])
    assert len(body["detection"]["terminals"]) == 2
    assert puzzle["meta"]["terminal_colors"] == {"A": "#0c28fa"}
    assert puzzle["terminals"]["A"]["color"] == "#0c28fa"

    solved = client.post(
        "/solve",
        json={"name": body["name"], "text": body["text"], "fill": True, "timeout_ms": 2_000},
    )
    assert solved.status_code == 200, solved.text
    assert solved.json()["graph"]["terminal_colors"] == {"A": "#0c28fa"}
