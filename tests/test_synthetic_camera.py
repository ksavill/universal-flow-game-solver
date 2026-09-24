from __future__ import annotations

import copy

import numpy as np
from PIL import Image, ImageDraw

from backend.photo_preprocess import load_camera_image
from scripts import synthetic_camera
from scripts.build_camera_photo_corpus import _source_split


def _screenshot() -> Image.Image:
    # Magenta display with a green "board", sized like the reference captures.
    image = Image.new("RGB", (400, 866), (230, 20, 230))
    ImageDraw.Draw(image).rectangle((40, 200, 360, 640), fill=(20, 220, 40))
    return image


def _scene(**overrides: object) -> dict:
    scene = synthetic_camera.sample_scene(np.random.default_rng(7), difficulty="easy", allow_holdout=False)
    scene.update(device="notch-black", environment="wood-desk-daylight", glare=None, hand=None)
    scene["screen"].update(moire=None, pwm=None)
    scene["camera"].update(defocus=None, motion=None, messaging_long_side=None, exif_orientation=1)
    scene["pose"].update(yaw=8.0, pitch=-6.0, roll=4.0, fill=0.75, crop=False, offset=[0.5, 0.5])
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        if field:
            scene[section][field] = value
        else:
            scene[section] = value
    return scene


def _inset(points: list[dict[str, float]], fraction: float) -> np.ndarray:
    corners = np.asarray([[point["x"], point["y"]] for point in points])
    return corners + (corners.mean(axis=0) - corners) * fraction


def _is_magenta(pixel: np.ndarray) -> bool:
    red, green, blue = (int(value) for value in pixel)
    return red > green + 50 and blue > green + 50


def _is_green(pixel: np.ndarray) -> bool:
    red, green, blue = (int(value) for value in pixel)
    return green > red + 50 and green > blue + 50


def test_render_is_deterministic_for_a_scene() -> None:
    scene = _scene()

    first = synthetic_camera.render_scene(_screenshot(), scene, board_box=(40, 200, 320, 440))
    second = synthetic_camera.render_scene(_screenshot(), copy.deepcopy(scene), board_box=(40, 200, 320, 440))

    assert first.payload == second.payload
    assert first.screen_corners == second.screen_corners


def test_labels_land_on_the_screen_and_board_through_distortion_and_exif() -> None:
    scene = _scene(camera__distortion=-0.08, camera__exif_orientation=6, camera__exif_metadata=True)

    rendered = synthetic_camera.render_scene(_screenshot(), scene, board_box=(40, 200, 320, 440))
    image, info = load_camera_image(rendered.payload)
    pixels = np.asarray(image)

    assert image.size == rendered.image_size
    assert info["exif_orientation"] == 6 and info["camera_metadata_present"]
    # Just inside each labeled corner is display (magenta or board green),
    # avoiding the top edge where the notch sits.
    for x, y in _inset(rendered.screen_corners, 0.06)[2:]:
        assert _is_magenta(pixels[int(round(y)), int(round(x))])
    board_center = _inset(rendered.board_corners, 1.0)[0]
    assert _is_green(pixels[int(round(board_center[1])), int(round(board_center[0]))])
    for x, y in _inset(rendered.board_corners, 0.08):
        assert _is_green(pixels[int(round(y)), int(round(x))])


def test_envelope_flags_cropped_and_occluded_photos() -> None:
    clean = synthetic_camera.render_scene(_screenshot(), _scene(), board_box=(40, 200, 320, 440), board_grid=(8, 7))
    cropped = synthetic_camera.render_scene(
        _screenshot(),
        _scene(pose__crop=True, pose__fill=0.95, pose__offset=[1.0, 0.5]),
        board_box=(40, 200, 320, 440),
    )
    covered = synthetic_camera.render_scene(
        _screenshot(),
        _scene(hand={"side": "left", "v": 0.6, "tone": [0.87, 0.64, 0.49], "intrusion": 0.2}),
        board_box=(40, 200, 320, 440),
    )

    assert "screen corner out of frame" not in clean.envelope["reasons"]
    assert "screen corner out of frame" in cropped.envelope["reasons"]
    assert "screen partly covered" in covered.envelope["reasons"]
    assert covered.envelope["in_envelope"] is False


def test_holdout_devices_and_environments_only_appear_when_allowed() -> None:
    rng = np.random.default_rng(3)
    holdout_devices = {device.name for device in synthetic_camera.DEVICES if device.holdout}
    holdout_rooms = {room.name for room in synthetic_camera.ENVIRONMENTS if room.holdout}

    scenes = [synthetic_camera.sample_scene(rng, difficulty="medium", allow_holdout=False) for _ in range(200)]

    assert not {scene["device"] for scene in scenes} & holdout_devices
    assert not {scene["environment"] for scene in scenes} & holdout_rooms


def test_splits_are_stable_per_source_screenshot() -> None:
    fractions = {"train": 0.7, "val": 0.15, "test": 0.15}
    splits = [_source_split(f"source-{index}", fractions) for index in range(300)]

    assert [_source_split(f"source-{index}", fractions) for index in range(300)] == splits
    assert 0.55 < splits.count("train") / 300 < 0.85
    assert splits.count("test") > 0 and splits.count("val") > 0
