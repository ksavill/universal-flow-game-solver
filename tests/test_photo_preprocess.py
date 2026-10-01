from __future__ import annotations

import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from fastapi.testclient import TestClient

import pytest

from backend.app import _camera_photo_review, app
from backend.image_utils import _sample_region_color, detect_terminals
from backend.photo_preprocess import (
    PHOTO_PREPROCESSING_VERSION,
    _dominant_lattice_run,
    _lattice_metrics,
    _order_points,
    _quality_and_views,
    _snap_board_to_expected_grid,
    clear_preparation_cache,
    load_camera_image,
    prepare_camera_photo,
    strip_photo_metadata,
)
from scripts.replay_camera_photo_corpus import _semantic_signature


def _photographed_screen(*, blur_radius: float = 1.1) -> tuple[Image.Image, list[list[float]]]:
    screen = Image.new("RGB", (640, 920), (10, 13, 22))
    draw = ImageDraw.Draw(screen)
    draw.rectangle((8, 8, 631, 911), outline=(245, 245, 248), width=8)
    board = (100, 210, 540, 650)
    draw.rectangle(board, fill=(17, 21, 31), outline=(210, 215, 225), width=6)
    for index in range(1, 8):
        position = 100 + index * 55
        draw.line((position, 210, position, 650), fill=(105, 112, 126), width=3)
        draw.line((100, position + 110, 540, position + 110), fill=(105, 112, 126), width=3)
    for x, y, color in (
        (128, 238, (240, 65, 65)),
        (512, 622, (240, 65, 65)),
        (238, 293, (60, 195, 245)),
        (403, 568, (60, 195, 245)),
    ):
        draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=color)

    source = np.asarray(screen)
    canvas = np.full((1180, 1420, 3), (44, 39, 36), dtype=np.uint8)
    src_points = np.float32([[0, 0], [639, 0], [639, 919], [0, 919]])
    destination = np.float32([[330, 130], [1060, 235], [1160, 1040], [205, 965]])
    matrix = cv2.getPerspectiveTransform(src_points, destination)
    warped = cv2.warpPerspective(source, matrix, (canvas.shape[1], canvas.shape[0]))
    mask = cv2.warpPerspective(
        np.full((screen.height, screen.width), 255, dtype=np.uint8),
        matrix,
        (canvas.shape[1], canvas.shape[0]),
    )
    canvas[mask > 0] = warped[mask > 0]
    image = Image.fromarray(canvas, mode="RGB")
    if blur_radius > 0:
        image = image.filter(ImageFilter.GaussianBlur(blur_radius))
    return image, destination.tolist()


def test_camera_loader_applies_exif_orientation() -> None:
    image = Image.new("RGB", (80, 40), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 39, 39), fill="red")
    exif = Image.Exif()
    exif[274] = 6
    payload = io.BytesIO()
    image.save(payload, format="JPEG", quality=95, exif=exif)

    loaded, info = load_camera_image(payload.getvalue())

    assert loaded.size == (40, 80)
    assert info["exif_orientation"] == 6
    assert info["orientation_applied"] is True


def test_terminal_color_sampling_excludes_glare_mask() -> None:
    region = Image.new("RGB", (40, 40), (215, 35, 45))
    draw = ImageDraw.Draw(region)
    draw.rectangle((0, 0, 15, 39), fill="white")
    invalid = Image.new("L", region.size, 0)
    ImageDraw.Draw(invalid).rectangle((0, 0, 15, 39), fill=255)

    color, _saturation, _brightness = _sample_region_color(region, invalid_mask=invalid)

    assert color[0] > 190
    assert color[1] < 70
    assert color[2] < 80


def test_diffuse_glare_requires_review() -> None:
    image = Image.new("RGB", (700, 700), (18, 22, 30))
    ImageDraw.Draw(image).ellipse((190, 220, 510, 460), fill=(135, 138, 142))
    corners = [[0.0, 0.0], [699.0, 0.0], [699.0, 699.0], [0.0, 699.0]]

    prepared = prepare_camera_photo(image, manual_corners=corners)
    prepared.info["prepared_board_size"] = {
        "width": prepared.color_image.width,
        "height": prepared.color_image.height,
    }

    assert prepared.info["quality"]["glare_fraction"] > 0.02
    assert _camera_photo_review(prepared.info)["required"] is True


def test_camera_corpus_signature_ignores_pair_labels_and_sampled_rgb() -> None:
    left = {
        "format": "flow-solver-puzzle",
        "terminals": {"D": {"color": "#ff0000", "endpoints": ["1,1", "0,0"]}},
        "topology": {"cells": {"0,0": {"kind": "ordinary"}}},
    }
    right = {
        "format": "flow-solver-puzzle",
        "terminals": {"A": {"color": "#fe0100", "endpoints": ["0,0", "1,1"]}},
        "topology": {"cells": {"0,0": {"kind": "ordinary"}}},
    }
    different = {
        **right,
        "terminals": {"A": {"color": "#fe0100", "endpoints": ["0,0", "1,2"]}},
    }

    assert _semantic_signature(json.dumps(left)) == _semantic_signature(json.dumps(right))
    assert _semantic_signature(json.dumps(left)) != _semantic_signature(json.dumps(different))


def test_prepare_camera_photo_rectifies_blurred_off_angle_display() -> None:
    photo, expected_corners = _photographed_screen()

    prepared = prepare_camera_photo(photo)

    assert prepared.info["selected"] is not None
    assert prepared.color_image.width >= 600
    assert prepared.color_image.height >= 780
    assert prepared.geometry_image.mode == "L"
    assert prepared.glare_mask.mode == "L"
    assert prepared.info["quality"]["laplacian_variance"] > 0
    actual = prepared.info["selected"]["corners"]
    mean_corner_error = sum(
        ((actual[index]["x"] - expected_corners[index][0]) ** 2 + (actual[index]["y"] - expected_corners[index][1]) ** 2) ** 0.5
        for index in range(4)
    ) / 4.0
    assert mean_corner_error < 55.0


def test_manual_corners_override_automatic_candidates() -> None:
    photo, corners = _photographed_screen(blur_radius=0.0)

    prepared = prepare_camera_photo(photo, manual_corners=corners)

    assert prepared.info["selected"]["source"] == "manual"
    assert prepared.info["homography"]
    assert prepared.color_image.width >= 700
    assert prepared.color_image.height >= 800


def test_missing_quad_requests_manual_corners() -> None:
    blank = Image.new("RGB", (900, 700), (32, 32, 32))

    prepared = prepare_camera_photo(blank)

    assert prepared.info["selected"] is None
    assert "manually" in prepared.info["quality"]["warnings"][0]


def test_camera_grid_endpoint_rectifies_once_and_recovers_grid() -> None:
    photo, corners = _photographed_screen()
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92)

    with TestClient(app) as client:
        response = client.post(
            "/image/grid/detect",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data={
                "source_mode": "camera",
                "photo_corners_json": json.dumps(
                    [{"x": point[0], "y": point[1]} for point in corners]
                ),
                "target_type": "square",
                "threshold": "230",
                "line_threshold": "0.6",
                "invert": "false",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_mode"] == "camera"
    assert body["grid"]["rows"] == 8
    assert body["grid"]["cols"] == 8
    assert body["photo"]["version"] == PHOTO_PREPROCESSING_VERSION
    assert body["photo"]["selected"]["source"] == "manual"
    assert body["photo"]["board"]["selected"] is not None


def test_invalid_image_source_mode_is_rejected() -> None:
    image = Image.new("RGB", (80, 80), "black")
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    with TestClient(app) as client:
        response = client.post(
            "/image/classify",
            files={"file": ("image.png", payload.getvalue(), "image/png")},
            data={"source_mode": "photograph"},
        )

    assert response.status_code == 400
    assert "source_mode" in response.json()["detail"]


def test_screenshot_mode_remains_the_api_default() -> None:
    photo, _corners = _photographed_screen(blur_radius=0.0)
    payload = io.BytesIO()
    photo.save(payload, format="PNG")
    request = {
        "files": {"file": ("image.png", payload.getvalue(), "image/png")},
        "data": {"target_type": "square"},
    }

    with TestClient(app) as client:
        omitted = client.post("/image/grid/detect", **request)
        explicit = client.post(
            "/image/grid/detect",
            files=request["files"],
            data={"target_type": "square", "source_mode": "screenshot"},
        )

    assert omitted.status_code == 200, omitted.text
    assert explicit.status_code == 200, explicit.text
    assert omitted.json() == explicit.json()


def test_auto_mode_uses_camera_path_only_with_camera_metadata() -> None:
    photo, _corners = _photographed_screen()
    exif = Image.Exif()
    exif[272] = "Synthetic Test Camera"
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92, exif=exif)

    with TestClient(app) as client:
        response = client.post(
            "/image/grid/detect",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data={"source_mode": "auto", "target_type": "square"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_mode"] == "auto"
    assert body["source_mode_effective"] == "camera"
    assert body["grid"]["rows"] == 8
    assert body["grid"]["cols"] == 8


def test_photo_prepare_endpoint_returns_rectified_preview() -> None:
    photo, corners = _photographed_screen()
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92)

    with TestClient(app) as client:
        response = client.post(
            "/image/photo/prepare",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data={
                "photo_corners_json": json.dumps(
                    [{"x": point[0], "y": point[1]} for point in corners]
                )
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["photo"]["board"]["selected"] is not None
    assert body["preview_data_url"].startswith("data:image/jpeg;base64,")


def test_camera_generate_recovers_terminals_and_persists_replay_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("FLOW_IMAGE_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("FLOW_IMAGE_IMPORT_INDEX_PATH", str(tmp_path / "index.sqlite3"))
    photo, corners = _photographed_screen()
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92)

    with TestClient(app) as client:
        response = client.post(
            "/image/generate",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data={
                "source_mode": "camera",
                "photo_corners_json": json.dumps(
                    [{"x": point[0], "y": point[1]} for point in corners]
                ),
                "target_type": "square",
                "grid_width": "8",
                "grid_height": "8",
                "auto_terminals": "true",
                "auto_classify": "false",
                "output_schema_version": "2",
                "expected_flow_count": "2",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["detection"]["terminals"]) == 4
    assert body["detection"]["terminal_completeness"]["status"] == "verified"
    record = json.loads(
        (tmp_path / "imports" / body["import_id"] / "record.json").read_text(encoding="utf-8")
    )
    assert record["processing"]["source_mode"] == "camera"
    assert record["processing"]["source_mode_effective"] == "camera"
    assert len(record["processing"]["photo_corners"]) == 4
    assert record["processing"]["photo_preprocessing_version"] == PHOTO_PREPROCESSING_VERSION


def test_server_image_job_uses_the_same_camera_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("FLOW_IMAGE_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("FLOW_IMAGE_IMPORT_INDEX_PATH", str(tmp_path / "index.sqlite3"))
    photo, corners = _photographed_screen()
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92)
    options = {
        "source_mode": "camera",
        "photo_corners_json": [{"x": point[0], "y": point[1]} for point in corners],
        "target_type": "square",
        "grid_width": 8,
        "grid_height": 8,
        "auto_terminals": True,
        "auto_classify": False,
        "expected_flow_count": 2,
        "output_schema_version": 2,
    }

    with TestClient(app) as client:
        created = client.post(
            "/image/jobs",
            files=[("files", ("camera.jpg", payload.getvalue(), "image/jpeg"))],
            data={"options_json": json.dumps(options)},
        )
        assert created.status_code == 200, created.text
        job = client.get(f"/image/jobs/{created.json()['id']}")

    assert job.status_code == 200, job.text
    assert job.json()["status"] == "completed"
    assert job.json()["failed"] == 0
    import_id = job.json()["items"][0]["import_id"]
    record = json.loads(
        (tmp_path / "imports" / import_id / "record.json").read_text(encoding="utf-8")
    )
    assert record["processing"]["source_mode"] == "camera"
    assert record["processing"]["photo_preprocessing_version"] == PHOTO_PREPROCESSING_VERSION


def _flow_board(
    *,
    size: int = 7,
    cell: int = 120,
    dots: dict[tuple[int, int], tuple[int, int, int]] | None = None,
) -> Image.Image:
    array = np.zeros((size * cell, size * cell, 3), np.uint8)
    for index in range(size + 1):
        position = min(index * cell, size * cell - 1)
        cv2.line(array, (position, 0), (position, size * cell - 1), (70, 70, 80), 2)
        cv2.line(array, (0, position), (size * cell - 1, position), (70, 70, 80), 2)
    for (row, col), color in (dots or {}).items():
        center = (col * cell + cell // 2, row * cell + cell // 2)
        cv2.circle(array, center, int(cell * 0.33), color, -1, lineType=cv2.LINE_AA)
    return Image.fromarray(array)


def test_white_and_gray_terminals_are_not_masked_as_glare() -> None:
    white, gray, red = (254, 254, 254), (159, 159, 189), (234, 51, 35)
    board = _flow_board(dots={(0, 0): white, (6, 6): white, (0, 6): gray, (6, 0): gray, (3, 3): red, (4, 1): red})

    _geometry, mask, quality = _quality_and_views(board, cv2=cv2, np=np)
    placements, _info = detect_terminals(
        board,
        rows=7,
        cols=7,
        sat_threshold=30.0,
        brightness_min=30.0,
        brightness_max=230.0,
        margin_ratio=0.15,
        cluster_threshold=60.0,
        invalid_mask=mask,
    )

    assert quality["glare_fraction"] < 0.005
    assert quality["terminal_like_regions_excluded"] >= 4
    assert {(0, 0), (6, 6), (0, 6), (6, 0)} <= {(item.row, item.col) for item in placements}


def test_broad_glare_is_still_masked_next_to_terminals() -> None:
    board = np.asarray(_flow_board(dots={(1, 1): (254, 254, 254), (5, 5): (254, 254, 254)})).copy()
    glare = np.zeros_like(board)
    cv2.ellipse(glare, (500, 330), (230, 150), 15, 0, 360, (255, 255, 255), -1)
    inside = glare[:, :, 0] > 0
    board[inside] = cv2.addWeighted(board, 0.5, glare, 0.5, 0)[inside]

    _geometry, mask, quality = _quality_and_views(Image.fromarray(board), cv2=cv2, np=np)

    assert quality["glare_fraction"] > 0.05
    assert np.asarray(mask)[330, 500] > 0
    assert np.asarray(mask)[180, 180] == 0  # white terminal at (1, 1)


def test_clean_board_has_no_exposure_or_banding_warnings() -> None:
    _geometry, _mask, quality = _quality_and_views(
        _flow_board(dots={(1, 1): (234, 51, 35), (5, 4): (234, 51, 35)}),
        cv2=cv2,
        np=np,
    )

    assert quality["warnings"] == []
    assert quality["banding_strength"] < 1.0


def test_display_banding_is_detected() -> None:
    board = np.asarray(_flow_board(), dtype=np.float32)
    rows = np.arange(board.shape[0], dtype=np.float32)
    banded = board + 30.0 + (np.sin(rows * 2.0 * np.pi / 48.0) * 10.0)[:, None, None]

    _geometry, _mask, quality = _quality_and_views(
        Image.fromarray(np.clip(banded, 0, 255).astype(np.uint8)),
        cv2=cv2,
        np=np,
    )

    assert quality["banding_strength"] > 2.0
    assert any("banding" in warning for warning in quality["warnings"])


def test_blur_is_measured_relative_to_cell_size() -> None:
    dots = {(1, 1): (234, 51, 35), (8, 9): (234, 51, 35), (4, 6): (60, 195, 245), (10, 2): (60, 195, 245)}
    sharp = _flow_board(size=12, cell=60, dots=dots)
    blurred = sharp.filter(ImageFilter.GaussianBlur(6))

    _geometry, _mask, sharp_quality = _quality_and_views(sharp, cv2=cv2, np=np)
    _geometry, _mask, blurred_quality = _quality_and_views(blurred, cv2=cv2, np=np)

    assert sharp_quality["cell_size_px"] == pytest.approx(60.0, rel=0.1)
    assert sharp_quality["blur_cell_ratio"] < 0.045
    assert blurred_quality["blur_cell_ratio"] > 0.075
    assert _camera_photo_review(
        {"selected": {"score": 0.9}, "quality": blurred_quality, "prepared_board_size": {"width": 720, "height": 720}}
    )["required"] is True


def test_corner_order_is_stable_for_a_display_rotated_45_degrees() -> None:
    diamond = [[200.0, 0.0], [400.0, 200.0], [200.0, 400.0], [0.0, 200.0]]

    ordered = _order_points(np.asarray(diamond, dtype=np.float32), np)

    assert len({tuple(point) for point in ordered.tolist()}) == 4
    destination = np.float32([[0, 0], [99, 0], [99, 99], [0, 99]])
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    assert abs(np.linalg.det(matrix)) > 1e-6


def test_lattice_ignores_a_header_line_that_matches_the_pitch() -> None:
    image = np.zeros((620, 560, 3), np.uint8)
    for index in range(7):
        cv2.line(image, (40, 100 + index * 80), (520, 100 + index * 80), (90, 90, 110), 2)
        cv2.line(image, (40 + index * 80, 100), (40 + index * 80, 580), (90, 90, 110), 2)
    # Header "text": fragmented blocks exactly one pitch above the board.
    # Without continuity trimming this line is accepted as an eighth row line.
    for x in range(40, 520, 150):
        cv2.rectangle(image, (x, 14), (x + 80, 26), (200, 200, 220), -1)

    lattice = _lattice_metrics(Image.fromarray(image), cv2=cv2, np=np)

    assert lattice["horizontal_lines"] == 7
    assert lattice["vertical_lines"] == 7


def test_lattice_run_search_collapses_near_duplicate_pitches() -> None:
    from backend.photo_preprocess import _candidate_pitches

    positions = sorted([12.0 + 31.0 * index for index in range(30)] + [5.0 + 17.3 * index for index in range(10)])
    naive = {
        round((right - left) / steps, 2)
        for index, left in enumerate(positions)
        for right in positions[index + 1 :]
        for steps in range(1, 16)
        if 960 / 36.0 <= (right - left) / steps <= 480.0
    }

    pitches = _candidate_pitches(positions, 960 / 36.0, 480.0)
    run = _dominant_lattice_run(positions, extent=960)

    # The old search tried every rounded pitch (quartic in the line count).
    assert len(pitches) * 3 < len(naive)
    assert len(run) >= 25


def _jpeg_with_location(orientation: int = 6) -> bytes:
    image = Image.new("RGB", (96, 48), "black")
    ImageDraw.Draw(image).rectangle((0, 0, 40, 40), fill=(220, 40, 40))
    exif = Image.Exif()
    exif[274] = orientation
    exif[271] = "PhoneMaker"
    exif[272] = "Model X"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (37.0, 46.0, 30.0)
    payload = io.BytesIO()
    image.save(payload, format="JPEG", quality=92, exif=exif)
    return payload.getvalue()


def test_metadata_stripping_keeps_pixels_and_orientation_only() -> None:
    original = _jpeg_with_location()

    sanitized = strip_photo_metadata(original)

    with Image.open(io.BytesIO(sanitized.data)) as opened:
        exif = opened.getexif()
        assert exif.get(274) == 6
        assert 271 not in exif and 272 not in exif
        assert not exif.get_ifd(0x8825)
    before, _ = load_camera_image(original)
    after, _ = load_camera_image(sanitized.data)
    assert np.array_equal(np.asarray(before), np.asarray(after))
    assert sanitized.suffix is None
    assert strip_photo_metadata(sanitized.data).data == sanitized.data


def test_png_metadata_stripping_removes_text_chunks() -> None:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Location", "37.7749,-122.4194")
    payload = io.BytesIO()
    Image.new("RGB", (32, 32), "navy").save(payload, format="PNG", pnginfo=info)

    sanitized = strip_photo_metadata(payload.getvalue())

    assert b"Location" not in sanitized.data
    with Image.open(io.BytesIO(sanitized.data)) as opened:
        assert opened.size == (32, 32)


def test_camera_archive_does_not_store_location_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("FLOW_IMAGE_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("FLOW_IMAGE_IMPORT_INDEX_PATH", str(tmp_path / "index.sqlite3"))
    photo, corners = _photographed_screen()
    exif = Image.Exif()
    exif[271] = "PhoneMaker"
    exif.get_ifd(0x8825)[1] = "N"
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92, exif=exif)

    with TestClient(app) as client:
        response = client.post(
            "/image/generate",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data={
                "source_mode": "camera",
                "photo_corners_json": json.dumps(corners),
                "target_type": "square",
                "grid_width": "8",
                "grid_height": "8",
                "auto_classify": "false",
                "expected_flow_count": "2",
            },
        )

    assert response.status_code == 200, response.text
    record_dir = tmp_path / "imports" / response.json()["import_id"]
    record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
    stored = (record_dir / record["image_file"]).read_bytes()
    with Image.open(io.BytesIO(stored)) as opened:
        assert 271 not in opened.getexif()
        assert not opened.getexif().get_ifd(0x8825)
    assert record["byte_size"] == len(stored)


def test_heic_without_decoder_returns_unsupported_media_type() -> None:
    from backend import photo_preprocess

    if photo_preprocess.HEIF_DECODER_AVAILABLE:
        pytest.skip("pillow-heif is installed")
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64

    with TestClient(app) as client:
        response = client.post(
            "/image/grid/detect",
            files={"file": ("photo.heic", heic, "image/heic")},
            data={"source_mode": "camera", "target_type": "square"},
        )

    assert response.status_code == 415
    assert "pillow-heif" in response.json()["detail"]


def test_invalid_mask_must_match_the_sampled_image() -> None:
    board = _flow_board()

    with pytest.raises(ValueError, match="invalid_mask"):
        detect_terminals(
            board,
            rows=7,
            cols=7,
            sat_threshold=30.0,
            brightness_min=30.0,
            brightness_max=230.0,
            margin_ratio=0.15,
            cluster_threshold=60.0,
            invalid_mask=Image.new("L", (10, 10), 0),
        )


def test_repeated_preparation_is_cached_and_returns_independent_copies() -> None:
    clear_preparation_cache()
    photo, corners = _photographed_screen()

    first = prepare_camera_photo(photo, manual_corners=corners, detect_board=True, cache_key="photo-a")
    first.color_image.paste((255, 0, 0), (0, 0, 40, 40))
    second = prepare_camera_photo(photo, manual_corners=corners, detect_board=True, cache_key="photo-a")
    hinted = prepare_camera_photo(
        photo,
        manual_corners=corners,
        detect_board=True,
        expected_grid=(8, 8),
        cache_key="photo-a",
    )

    assert first.info["cache_hit"] is False
    assert second.info["cache_hit"] is True
    assert second.color_image.getpixel((5, 5)) != (255, 0, 0)
    # The grid hint agrees with the unhinted lattice, so the board is the same.
    assert hinted.info["board"]["strategy"] == "lattice-matches-expected-grid"
    assert hinted.info["board"]["selected"] == second.info["board"]["selected"]
    assert hinted.display_image is not None


def test_camera_review_uses_terminal_and_detector_evidence() -> None:
    base = {
        "selected": {"score": 0.62, "source": "canny-24-72:contour"},
        "selected_corroboration": 2,
        "selection_margin": 0.2,
        "quality": {"blur_sigma_px": 0.8, "blur_cell_ratio": 0.01, "cell_size_px": 80.0, "glare_fraction": 0.0},
        "prepared_board_size": {"width": 900, "height": 900},
    }
    assert _camera_photo_review(base)["required"] is False

    near_miss = _camera_photo_review(base, {"status": "plausible", "near_miss_count": 2})
    ambiguous = _camera_photo_review({**base, "selection_margin": 0.01})
    uncorroborated = _camera_photo_review({**base, "selected_corroboration": 0})

    assert near_miss["required"] and "almost like terminals" in near_miss["reasons"][0]
    assert ambiguous["required"] and "equally well" in ambiguous["reasons"][0]
    assert uncorroborated["required"] and "Only one detector" in uncorroborated["reasons"][0]

    def terminals(*colors: tuple[int, int, int]) -> list[dict]:
        return [{"letter": chr(65 + index // 2), "color": list(color)} for index, color in enumerate(colors)]

    clean = {"terminals": terminals((240, 50, 45), (238, 52, 44), (40, 145, 30), (42, 147, 31)), "level_type": {"geometry": "square"}}
    # A shadowed finger over the screen reads as a dark brown "pair".
    thumb = {**clean, "terminals": clean["terminals"] + terminals((82, 67, 45), (76, 66, 45))}
    regions = {**clean, "graph_layout": "regions"}
    regions_signal = {**clean, "level_type": {"geometry": "square", "signals": {"recommended_graph_layout": "regions"}}}

    assert _camera_photo_review(base, detection=clean)["required"] is False
    assert any("darker than any Flow color" in reason for reason in _camera_photo_review(base, detection=thumb)["reasons"])
    for detection in (regions, regions_signal):
        assert any("region layout" in reason for reason in _camera_photo_review(base, detection=detection)["reasons"])


def test_auto_mode_without_camera_metadata_uses_pipeline_evidence() -> None:
    photo, _corners = _photographed_screen()
    photo_payload = io.BytesIO()
    photo.save(photo_payload, format="JPEG", quality=92)
    # The same board rendered flat, as a screenshot would be.
    screenshot = _flow_board(size=8, cell=90, dots={(0, 0): (240, 65, 65), (7, 7): (240, 65, 65)})
    screenshot_payload = io.BytesIO()
    screenshot.save(screenshot_payload, format="PNG")

    with TestClient(app) as client:
        from_photo = client.post(
            "/image/grid/detect",
            files={"file": ("camera.jpg", photo_payload.getvalue(), "image/jpeg")},
            data={"source_mode": "auto", "target_type": "square"},
        )
        from_screenshot = client.post(
            "/image/grid/detect",
            files={"file": ("screen.png", screenshot_payload.getvalue(), "image/png")},
            data={"source_mode": "auto", "target_type": "square"},
        )

    assert from_photo.status_code == 200, from_photo.text
    assert from_photo.json()["source_mode_effective"] == "camera"
    assert from_photo.json()["input"]["auto_selection_reason"] == "pipeline_evidence"
    assert from_photo.json()["grid"]["rows"] == 8
    assert from_screenshot.status_code == 200, from_screenshot.text
    assert from_screenshot.json()["source_mode_effective"] == "screenshot"


def test_upload_store_round_trip_and_expiry(tmp_path: Path) -> None:
    import os

    from backend.upload_store import UploadStore

    store = UploadStore(tmp_path, ttl_seconds=60.0, max_total_bytes=10_000)
    stored = store.put(b"image-bytes", filename="photo.jpg", content_type="image/jpeg")

    fetched = store.get(stored.upload_id)
    assert fetched is not None and fetched.data == b"image-bytes" and fetched.filename == "photo.jpg"
    old = os.stat(tmp_path / f"{stored.upload_id}.bin").st_mtime - 120
    os.utime(tmp_path / f"{stored.upload_id}.bin", (old, old))
    assert store.get(stored.upload_id) is None
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")


def test_pipeline_stages_accept_an_upload_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_UPLOADS_DIR", str(tmp_path / "uploads"))
    photo, corners = _photographed_screen()
    payload = io.BytesIO()
    photo.save(payload, format="JPEG", quality=92)
    form = {
        "source_mode": "camera",
        "photo_corners_json": json.dumps(corners),
        "target_type": "square",
    }

    with TestClient(app) as client:
        uploaded = client.post(
            "/image/uploads",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
        )
        assert uploaded.status_code == 200, uploaded.text
        upload_id = uploaded.json()["upload_id"]
        by_id = client.post("/image/grid/detect", data={**form, "upload_id": upload_id})
        by_file = client.post(
            "/image/grid/detect",
            files={"file": ("camera.jpg", payload.getvalue(), "image/jpeg")},
            data=form,
        )
        expired = client.post("/image/grid/detect", data={**form, "upload_id": "0" * 64})
        missing = client.post("/image/grid/detect", data=form)

    assert by_id.status_code == 200, by_id.text
    assert by_id.json()["grid"] == by_file.json()["grid"]
    assert expired.status_code == 410
    assert missing.status_code == 400


def test_clearing_a_review_flag_records_a_human_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("FLOW_IMAGE_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("FLOW_IMAGE_IMPORT_INDEX_PATH", str(tmp_path / "index.sqlite3"))
    from backend.app import _set_image_import_flag, _store_image_import

    record = _store_image_import(
        data=_jpeg_with_location(),
        original_name="camera.jpg",
        content_type="image/jpeg",
        image_size=(48, 96),
        result=None,
        processing={"source_mode": "camera", "source_mode_effective": "camera"},
    )

    flagged = _set_image_import_flag(record["id"], flagged=True, reason="check dots")
    cleared = _set_image_import_flag(record["id"], flagged=False)

    assert "reviewed_at" not in flagged
    assert cleared["reviewed_at"] >= flagged["flagged_at"]


def test_camera_loader_reports_a_pseudonymous_device_id() -> None:
    _image, info = load_camera_image(_jpeg_with_location())
    _image, same = load_camera_image(_jpeg_with_location(orientation=1))
    plain = io.BytesIO()
    Image.new("RGB", (8, 8)).save(plain, format="PNG")

    assert info["device_id"] and len(info["device_id"]) == 12
    assert "PhoneMaker" not in json.dumps(info)
    assert info["device_id"] == same["device_id"]
    assert load_camera_image(plain.getvalue())[1]["device_id"] is None


def test_heic_photos_decode_and_are_archived_without_metadata() -> None:
    from backend import photo_preprocess

    if not photo_preprocess.HEIF_DECODER_AVAILABLE:
        pytest.skip("pillow-heif is not installed")
    image = Image.new("RGB", (64, 32), (200, 30, 30))
    exif = Image.Exif()
    exif[271] = "PhoneMaker"
    payload = io.BytesIO()
    image.save(payload, format="HEIF", exif=exif.tobytes())

    loaded, info = load_camera_image(payload.getvalue())
    sanitized = strip_photo_metadata(payload.getvalue())

    assert loaded.size == (64, 32) and info["format"] in {"heif", "heic"}
    assert sanitized.suffix == ".png"
    with Image.open(io.BytesIO(sanitized.data)) as opened:
        assert 271 not in opened.getexif()


def test_display_beam_scores_shortlisted_outlines_by_their_board() -> None:
    from backend import photo_preprocess

    photo, _corners = _photographed_screen()
    cv2_module, np_module = photo_preprocess._imports()
    display = photo_preprocess._compute_display_stage(
        photo.convert("RGB"),
        roi=None,
        manual_corners=None,
        max_detection_dim=1600,
        max_output_dim=2600,
        candidate_limit=5,
        cv2=cv2_module,
        np=np_module,
    )

    chosen, beam = photo_preprocess._select_display_by_board_evidence(
        photo.convert("RGB"),
        display,
        display_key=None,
        max_output_dim=2600,
        cv2=cv2_module,
        np=np_module,
    )

    assert 1 <= len(beam) <= photo_preprocess.DISPLAY_BEAM_WIDTH + photo_preprocess.BOARD_OUTLINE_BEAM_EXTRA
    # The chosen outline is the one whose board most other outlines agree on.
    best = max(beam, key=lambda entry: (entry["votes"], entry["board_score"], -entry["index"]))
    assert chosen.selected == chosen.candidates[best["index"]]
    assert chosen.selected.source == best["source"]
    assert chosen.selected_index == best["index"]
    assert all(len(entry["board_quad"]) == 4 for entry in beam)
    assert best["board_score"] > 0.0


def test_board_outline_is_traced_from_the_cells() -> None:
    from backend import photo_preprocess

    photo, screen_corners = _photographed_screen()
    cv2_module, np_module = photo_preprocess._imports()
    outlines = photo_preprocess._board_outline_candidates(
        np.asarray(photo),
        offset_x=0.0,
        offset_y=0.0,
        source_scale=1.0,
        cv2=cv2_module,
        np=np_module,
    )

    to_photo = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [639, 0], [639, 919], [0, 919]]), np.float32(screen_corners)
    )
    board = cv2.perspectiveTransform(
        np.float32([[100, 210], [540, 210], [540, 650], [100, 650]]).reshape(-1, 1, 2), to_photo
    ).reshape(-1, 2)
    assert outlines and all(outline.source.startswith("board-cells-") for outline in outlines)
    best = photo_preprocess._rank_board_outlines(outlines)[0]
    error = float(np.linalg.norm(_order_points(np.float32(best.corners), np) - _order_points(board, np), axis=1).mean())
    assert error < 0.02 * float(np.hypot(*photo.size))


def _photographed_hex_screen() -> tuple[Image.Image, list[list[float]], tuple[float, float, float, float]]:
    """A phone showing a hex board (no square lattice) on a busy desk photo."""

    screen = Image.new("RGB", (640, 1180), (12, 12, 18))
    draw = ImageDraw.Draw(screen)
    radius = 34.0
    step_x = radius * 3 ** 0.5
    left_edge, top_edge, right_edge, bottom_edge = 1e9, 1e9, 0.0, 0.0
    colors = [(240, 65, 65), (60, 195, 245), (250, 210, 40), (80, 200, 90)]
    for row in range(9):
        for col in range(6):
            cx = 70 + step_x * (col + (0.5 if row % 2 else 0.0))
            cy = 300 + row * radius * 1.5
            points = [
                (cx + radius * np.cos(np.radians(90 + 60 * k)), cy + radius * np.sin(np.radians(90 + 60 * k)))
                for k in range(6)
            ]
            draw.polygon(points, outline=(150, 95, 95), width=3)
            left_edge, right_edge = min(left_edge, cx - step_x / 2), max(right_edge, cx + step_x / 2)
            top_edge, bottom_edge = min(top_edge, cy - radius), max(bottom_edge, cy + radius)
            if (row * 6 + col) % 7 == 0:
                color = colors[(row + col) % len(colors)]
                draw.ellipse((cx - 17, cy - 17, cx + 17, cy + 17), fill=color)
    draw.text((60, 120), "level 4/5", fill=(230, 60, 60))

    source = np.asarray(screen)
    canvas = np.full((1700, 1300, 3), (196, 190, 180), dtype=np.uint8)
    for y in range(0, canvas.shape[0], 90):
        cv2.line(canvas, (0, y), (canvas.shape[1], y + 40), (120, 116, 110), 4)
    phone = np.float32([[300, 170], [1040, 230], [1010, 1560], [250, 1500]])
    cv2.fillConvexPoly(canvas, phone.astype(np.int32), (35, 35, 38))
    src_points = np.float32([[0, 0], [639, 0], [639, 1179], [0, 1179]])
    destination = np.float32([[330, 205], [1005, 262], [978, 1522], [282, 1468]])
    matrix = cv2.getPerspectiveTransform(src_points, destination)
    warped = cv2.warpPerspective(source, matrix, (canvas.shape[1], canvas.shape[0]))
    mask = cv2.warpPerspective(np.full((screen.height, screen.width), 255, dtype=np.uint8), matrix, (canvas.shape[1], canvas.shape[0]))
    canvas[mask > 0] = warped[mask > 0]
    image = Image.fromarray(canvas, mode="RGB").filter(ImageFilter.GaussianBlur(0.8))
    return image, destination.tolist(), (left_edge / 640, top_edge / 1180, right_edge / 640, bottom_edge / 1180)


def test_cell_mesh_box_finds_a_hex_board_without_a_square_lattice() -> None:
    from backend import photo_preprocess

    photo, corners, board = _photographed_hex_screen()
    rectified = prepare_camera_photo(photo, manual_corners=corners).color_image
    cv2_module, np_module = photo_preprocess._imports()

    mesh = photo_preprocess._cell_mesh_box(rectified, cv2=cv2_module, np=np_module)

    assert mesh is not None and mesh["cells"] >= 45
    left, top, right, bottom = mesh["box"]
    width, height = rectified.size
    expected = (board[0] * width, board[1] * height, board[2] * width, board[3] * height)
    assert max(abs(a - b) for a, b in zip((left, top, right, bottom), expected)) < 0.5 * mesh["pitch"]
    assert photo_preprocess._cell_mesh_box(Image.new("RGB", (400, 700), (20, 20, 20)), cv2=cv2_module, np=np_module) is None


def test_non_square_boards_choose_the_display_by_cell_clusters() -> None:
    photo, corners, _board = _photographed_hex_screen()

    prepared = prepare_camera_photo(photo, detect_board=True, square_board=False)

    assert prepared.info["board"] is None
    assert prepared.info["board_mesh"] is not None and prepared.info["board_mesh"]["cells"] >= 45
    selected = prepared.info["selected"]["corners"]
    ordered = _order_points(np.float32(corners), np)
    error = float(np.mean([np.hypot(selected[i]["x"] - ordered[i][0], selected[i]["y"] - ordered[i][1]) for i in range(4)]))
    assert error < 60.0
    assert prepared.info["selected"]["metrics"]["touches_frame"] < 3


def test_camera_review_flags_outlines_that_disagree_about_the_board() -> None:
    agreeing = {**_clean_photo_info(), "consensus_votes": 2, "selected_corroboration": 0}
    disagreeing = {**_clean_photo_info(), "consensus_votes": 0}

    assert _camera_photo_review(agreeing)["required"] is False
    review = _camera_photo_review(disagreeing)
    assert review["required"] and "disagree" in review["reasons"][0]


def test_real_corpus_harvest_uses_reviewed_camera_imports(tmp_path: Path) -> None:
    from scripts.build_real_camera_corpus import build

    archive = tmp_path / "archive"
    for index, (device, reviewed) in enumerate((("aaa", True), ("bbb", True), ("ccc", False))):
        record_dir = archive / f"rec{index}"
        record_dir.mkdir(parents=True)
        (record_dir / "source.jpg").write_bytes(f"photo-{index}".encode())
        record = {
            "id": f"rec{index}",
            "created_at": 1_790_000_000 + index,
            "status": "processed",
            "image_file": "source.jpg",
            "flagged": False,
            "processing": {"source_mode": "camera", "source_mode_effective": "camera"},
            "result": {"text": json.dumps({"terminals": {"A": {"endpoints": [f"{index},0", "1,1"]}}}),
                       "detection": {"input": {"device_id": device}, "terminals": []}},
        }
        if reviewed:
            record["reviewed_at"] = record["created_at"] + 5
        (record_dir / "record.json").write_text(json.dumps(record), encoding="utf-8")

    manifest = build(
        archive=archive,
        output=tmp_path / "real",
        include_unreviewed=False,
        holdout_fraction=0.3,
        overwrite=False,
    )

    entries = manifest["entries"]
    assert {entry["source_import_id"] for entry in entries} == {"rec0", "rec1"}
    assert {entry["split"] for entry in entries} == {"tuning", "holdout"}
    assert all(entry["split_group"] == "device_id" for entry in entries)
    assert all((tmp_path / "real" / entry["image"]).is_file() for entry in entries)


def test_review_calibration_counts_unflagged_errors() -> None:
    from scripts.calibrate_camera_review import calibrate

    def result(match: bool, blur: float) -> dict:
        return {
            "camera_status": 200,
            "match": match,
            "review_inputs": {
                "photo": {
                    "selected": {"score": 0.9},
                    "selected_corroboration": 2,
                    "selection_margin": 0.3,
                    "quality": {"blur_sigma_px": 1.0, "blur_cell_ratio": blur, "cell_size_px": 80.0, "glare_fraction": 0.0},
                    "prepared_board_size": {"width": 900, "height": 900},
                },
                "terminal_completeness": {"status": "verified"},
                "completeness_review": False,
            },
        }

    results = [result(True, 0.01), result(True, 0.05), result(False, 0.09)]
    report = calibrate(results, max_unflagged_wrong_rate=0.0)

    assert report["current"]["unflagged_wrong"] == 0
    blur_rows = {row["value"]: row for row in report["sweeps"]["blur_cell_ratio"]["rows"]}
    assert blur_rows[max(blur_rows)]["unflagged_wrong"] == 1
    assert report["sweeps"]["blur_cell_ratio"]["suggested"] is not None


def _clean_photo_info() -> dict:
    return {
        "selected": {"score": 0.62, "source": "canny-24-72:contour"},
        "selected_corroboration": 2,
        "selection_margin": 0.2,
        "quality": {"blur_sigma_px": 0.8, "blur_cell_ratio": 0.01, "cell_size_px": 80.0, "glare_fraction": 0.0},
        "prepared_board_size": {"width": 900, "height": 900},
    }


def test_camera_review_flags_colors_that_do_not_form_exact_pairs() -> None:
    red, orange, yellow = [240, 40, 40], [243, 215, 88], [242, 245, 93]
    paired = {
        "terminals": [{"letter": "A", "color": red}, {"letter": "A", "color": red}],
        "terminal_info": {"clusters": [{"color": red, "count": 2}]},
    }
    merged = {
        # Orange shifted toward yellow merged into one four-terminal cluster.
        "terminals": paired["terminals"],
        "terminal_info": {"clusters": [{"color": red, "count": 2}, {"color": yellow, "count": 4}]},
    }
    lookalike = {
        "terminals": [
            {"letter": "A", "color": orange}, {"letter": "A", "color": orange},
            {"letter": "B", "color": yellow}, {"letter": "B", "color": yellow},
        ],
        "terminal_info": {"clusters": [{"color": orange, "count": 2}, {"color": yellow, "count": 2}]},
    }

    assert _camera_photo_review(_clean_photo_info(), detection=paired)["required"] is False
    merged_review = _camera_photo_review(_clean_photo_info(), detection=merged)
    lookalike_review = _camera_photo_review(_clean_photo_info(), detection=lookalike)
    assert merged_review["required"] and "exactly one pair" in merged_review["reasons"][0]
    assert lookalike_review["required"] and "nearly identical" in lookalike_review["reasons"][0]


def test_camera_review_flags_unsupported_photo_boards() -> None:
    for level_type in ({"geometry": "hex", "modifiers": []}, {"geometry": "square", "modifiers": ["warps"]}):
        review = _camera_photo_review(_clean_photo_info(), detection={"level_type": level_type})
        assert review["required"] and "not reliable yet" in review["reasons"][0]
    plain = _camera_photo_review(_clean_photo_info(), detection={"level_type": {"geometry": "square", "modifiers": ["bridges"]}})
    assert plain["required"] is False


def test_region_graph_replay_matches_renamed_cells() -> None:
    from scripts.replay_camera_photo_corpus import _region_graphs_equivalent

    def region_graph(names: list[str], scale: float) -> str:
        centers = [(10, 10), (30, 10), (20, 30)]
        return json.dumps(
            {
                "topology": {
                    "channels": {
                        name: {"data": {"pixel_center": [x * scale, y * scale]}} for name, (x, y) in zip(names, centers)
                    },
                    "adjacencies": [
                        {"a": {"channel": names[0]}, "b": {"channel": names[1]}},
                        {"a": {"channel": names[1]}, "b": {"channel": names[2]}},
                    ],
                },
                "terminals": {"A": {"endpoints": [names[0], names[2]]}},
            }
        )

    screenshot = region_graph(["r0", "r1", "r2"], 1.0)
    photo = region_graph(["r7", "r3", "r9"], 2.5)
    rewired = json.loads(photo)
    rewired["terminals"]["A"]["endpoints"] = ["r7", "r3"]

    assert _region_graphs_equivalent(screenshot, photo)
    assert not _region_graphs_equivalent(screenshot, json.dumps(rewired))


def test_expected_grid_fit_finds_the_board_below_a_header_line() -> None:
    from backend.photo_preprocess import _fit_expected_lattice

    image = np.zeros((900, 640, 3), np.uint8)
    left, top, pitch, size = 60, 220, 86, 6
    for index in range(size + 1):
        cv2.line(image, (left + index * pitch, top), (left + index * pitch, top + size * pitch), (95, 95, 110), 1)
        cv2.line(image, (left, top + index * pitch), (left + size * pitch, top + index * pitch), (95, 95, 110), 1)
    # Header text one pitch above the board (fragmented, like words) must not
    # become a row: every grid line runs the full width, text does not.
    for x in range(left, left + size * pitch, 90):
        cv2.line(image, (x, top - pitch), (x + 50, top - pitch), (200, 200, 210), 1)

    fit = _fit_expected_lattice(Image.fromarray(image), (size, size), np=np)

    assert fit is not None
    assert abs(fit["x_min"] - left) <= 3 and abs(fit["x_max"] - (left + size * pitch)) <= 3
    assert abs(fit["y_min"] - top) <= 3 and abs(fit["y_max"] - (top + size * pitch)) <= 3


def _dashed(draw, start, end, fill, width=3, dash=10, gap=8):
    (x0, y0), (x1, y1) = start, end
    length = max(abs(x1 - x0), abs(y1 - y0))
    position = 0
    while position < length:
        stop = min(length, position + dash)
        if x0 == x1:
            draw.line([(x0, y0 + position), (x0, y0 + stop)], fill=fill, width=width)
        else:
            draw.line([(x0 + position, y0), (x0 + stop, y0)], fill=fill, width=width)
        position += dash + gap


def test_grid_snap_prefers_solid_interior_over_dashed_warp_shadow_lines() -> None:
    rows = cols = 7
    pitch, left, top = 70, 150, 200
    image = Image.new("RGB", (900, 1000), (12, 12, 16))
    draw = ImageDraw.Draw(image)
    line = (150, 110, 110)
    right, bottom = left + cols * pitch, top + rows * pitch
    for index in range(1, cols):
        x = left + index * pitch
        draw.line([(x, top), (x, bottom)], fill=line, width=3)
        _dashed(draw, (x, top - pitch), (x, top), line)
        _dashed(draw, (x, bottom), (x, bottom + pitch), line)
    for index in range(1, rows):
        y = top + index * pitch
        draw.line([(left, y), (right, y)], fill=line, width=3)
        _dashed(draw, (left - pitch, y), (left, y), line)
        _dashed(draw, (right, y), (right + pitch, y), line)
    # Warp boards draw their border dashed, and shadow lines one cell outside
    # it run the full board length too.
    for x in (left - pitch, left, right, right + pitch):
        _dashed(draw, (x, top - pitch), (x, bottom + pitch), line)
    for y in (top - pitch, top, bottom, bottom + pitch):
        _dashed(draw, (left - pitch, y), (right + pitch, y), line)
    for index, (cx, cy) in enumerate(((1, 1), (4, 2), (2, 5), (6, 6))):
        center = (left + cx * pitch + pitch // 2, top + cy * pitch + pitch // 2)
        draw.ellipse([center[0] - 24, center[1] - 24, center[0] + 24, center[1] + 24], fill=((230, 40, 40), (40, 200, 60), (40, 90, 230), (240, 220, 30))[index])

    # The lattice run was shifted one column into the left shadow ring.
    shifted = [(left - pitch, top), (right - pitch, top), (right - pitch, bottom), (left - pitch, bottom)]
    lattice = {"pitch_x_fraction": 1.0 / cols, "pitch_y_fraction": 1.0 / rows}
    result = _snap_board_to_expected_grid(image, shifted, lattice, (rows, cols), cv2=cv2, np=np)

    assert result is not None
    corners, _info = result
    expected = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for (x, y), (ex, ey) in zip(corners, expected):
        assert abs(x - ex) < 0.25 * pitch and abs(y - ey) < 0.25 * pitch


def test_accelerated_gray_accepts_single_channel_images() -> None:
    import cv2 as cv2_module

    from backend.image_utils import _accelerated_gray

    gray_image = Image.new("L", (40, 30), 120)

    gray, _backend = _accelerated_gray(gray_image, cv2=cv2_module, np=np, rgb=np.asarray(gray_image))

    assert gray.shape == (30, 40) and int(gray[0, 0]) == 120
