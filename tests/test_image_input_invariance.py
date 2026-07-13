from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from backend.image_utils import (
    apply_crop,
    auto_crop,
    detect_circle_grid,
    detect_grid,
    detect_region_topology,
    load_image,
)


def _reference_board(name: str) -> Image.Image:
    path = Path(__file__).resolve().parents[1] / "reference_puzzle_images" / name
    if not path.exists():
        pytest.skip(f"{name} is not available")
    image = load_image(path.read_bytes())
    crop = auto_crop(image, threshold=230, invert=False, padding=8)
    assert crop is not None
    return apply_crop(image, crop)


def _scaled_canvas(board: Image.Image, *, board_max: int, canvas: tuple[int, int]) -> Image.Image:
    scale = float(board_max) / float(max(board.size))
    resized = board.resize(
        (
            max(1, int(round(board.width * scale))),
            max(1, int(round(board.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    screenshot = Image.new("RGB", canvas, color=(0, 0, 0))
    screenshot.paste(
        resized,
        ((canvas[0] - resized.width) // 2, (canvas[1] - resized.height) // 2),
    )
    return screenshot


@pytest.mark.parametrize(
    ("board_max", "canvas"),
    [
        (280, (4096, 420)),
        (700, (900, 1800)),
        (1200, (1900, 1350)),
    ],
)
def test_square_reference_is_resolution_and_aspect_invariant(
    board_max: int,
    canvas: tuple[int, int],
) -> None:
    screenshot = _scaled_canvas(_reference_board("IMG_3202.PNG"), board_max=board_max, canvas=canvas)
    crop = auto_crop(screenshot, threshold=230, invert=False, padding=8)
    assert crop is not None

    grid = detect_grid(
        apply_crop(screenshot, crop),
        threshold=230,
        line_threshold=0.6,
        invert=False,
    )

    assert grid is not None
    assert (grid.rows, grid.cols) == (5, 5)


@pytest.mark.parametrize(
    ("board_max", "canvas"),
    [
        (280, (4096, 420)),
        (700, (900, 1800)),
        (1200, (1900, 1350)),
    ],
)
def test_circle_reference_is_resolution_and_aspect_invariant(
    board_max: int,
    canvas: tuple[int, int],
) -> None:
    screenshot = _scaled_canvas(_reference_board("IMG_3244.PNG"), board_max=board_max, canvas=canvas)
    crop = auto_crop(screenshot, threshold=230, invert=False, padding=8)
    assert crop is not None

    circle, info = detect_circle_grid(
        apply_crop(screenshot, crop),
        min_sectors=3,
        max_sectors=32,
    )

    assert circle is not None, info
    assert (circle.rings, circle.sectors) == (2, 8)


@pytest.mark.parametrize(
    ("board_max", "canvas"),
    [
        (280, (4096, 420)),
        (700, (900, 1800)),
        (1200, (1900, 1350)),
    ],
)
def test_freeform_reference_is_resolution_and_aspect_invariant(
    board_max: int,
    canvas: tuple[int, int],
) -> None:
    screenshot = _scaled_canvas(_reference_board("IMG_3241.PNG"), board_max=board_max, canvas=canvas)
    crop = auto_crop(screenshot, threshold=230, invert=False, padding=8)
    assert crop is not None

    nodes, edges, info = detect_region_topology(apply_crop(screenshot, crop))

    assert (len(nodes), len(edges)) == (54, 90), info
    assert info["max_degree"] == 4
    assert info["warnings"] == []
