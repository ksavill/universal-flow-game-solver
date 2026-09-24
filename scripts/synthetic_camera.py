"""Render synthetic photographs of a phone displaying a Flow screenshot.

Each photo is built from explicit, JSON-serializable scene parameters so it can
be regenerated exactly and analyzed by what went into it:

* a mock device (display aspect, bezels, cutout, frame, case, home button)
  showing the screenshot letterboxed to its display;
* an environment (procedural surface texture, light type, color temperature,
  clutter) with the phone's drop shadow and an optional hand;
* a light model in linear space: the display emits light independently of the
  room, its glass reflects the room and light sources (glare), and display
  pixels can be rendered as RGB subpixels so real moire appears when they
  alias against the camera grid;
* a camera: 3D pose, auto-exposure, white-balance error, vignetting, lens
  distortion, chromatic aberration, optical/defocus/motion blur, sensor noise,
  sharpening, optional messaging-app downscale, JPEG, and EXIF orientation.

Labels (screen and board corners) are carried through every geometric step,
including lens distortion and EXIF rotation, in the coordinates the importer
sees after applying EXIF orientation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import io
import math
from typing import Any, Optional, Sequence

import cv2
import numpy as np
from PIL import Image


RENDERER_VERSION = "scene-v1"


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    screen_aspect: float  # display height / width
    bezel: float  # side bezel, in display widths
    top_bezel: float
    bottom_bezel: float
    body_radius: float  # outer corner radius, in display widths
    screen_radius: float  # display corner radius, in display widths
    cutout: str  # none | notch | punch | island
    front_rgb: tuple[float, float, float]  # bezel glass color (sRGB 0-1)
    frame_rgb: tuple[float, float, float]
    case_rgb: Optional[tuple[float, float, float]]
    case_thickness: float
    home_button: bool = False
    holdout: bool = False  # only used for test-split sources


@dataclass(frozen=True)
class EnvironmentProfile:
    name: str
    texture: str
    ambient: tuple[float, float]  # linear illuminance multiplier
    color_temperature: tuple[float, float]  # kelvin
    light: str  # window | ceiling | lamp | overcast
    clutter: tuple[int, int]
    holdout: bool = False


DEVICES: tuple[DeviceProfile, ...] = (
    DeviceProfile("notch-black", 2.165, 0.035, 0.035, 0.035, 0.13, 0.10, "notch", (0.02, 0.02, 0.025), (0.12, 0.12, 0.13), None, 0.0),
    DeviceProfile("punch-silver", 2.22, 0.03, 0.04, 0.04, 0.10, 0.08, "punch", (0.02, 0.02, 0.02), (0.70, 0.71, 0.73), None, 0.0),
    DeviceProfile("island-blue-case", 2.165, 0.03, 0.03, 0.03, 0.14, 0.11, "island", (0.02, 0.02, 0.02), (0.30, 0.30, 0.32), (0.14, 0.24, 0.52), 0.06),
    DeviceProfile("home-white-gold", 1.78, 0.06, 0.24, 0.24, 0.16, 0.0, "none", (0.92, 0.92, 0.93), (0.80, 0.70, 0.55), None, 0.0, home_button=True),
    DeviceProfile("home-black-silver", 1.78, 0.06, 0.24, 0.24, 0.16, 0.0, "none", (0.03, 0.03, 0.03), (0.72, 0.72, 0.74), None, 0.0, home_button=True),
    DeviceProfile("rugged-red-case", 2.0, 0.05, 0.08, 0.08, 0.18, 0.06, "punch", (0.02, 0.02, 0.02), (0.20, 0.20, 0.20), (0.55, 0.08, 0.08), 0.12),
    DeviceProfile("compact-black", 2.0, 0.04, 0.09, 0.09, 0.10, 0.04, "none", (0.03, 0.03, 0.035), (0.15, 0.15, 0.16), None, 0.0),
    DeviceProfile("tablet-gray", 1.33, 0.07, 0.07, 0.07, 0.08, 0.03, "none", (0.03, 0.03, 0.03), (0.45, 0.45, 0.47), (0.25, 0.25, 0.28), 0.05, holdout=True),
    DeviceProfile("clear-case-notch", 2.165, 0.035, 0.035, 0.035, 0.14, 0.10, "notch", (0.02, 0.02, 0.025), (0.60, 0.60, 0.62), (0.80, 0.82, 0.85), 0.04, holdout=True),
)

ENVIRONMENTS: tuple[EnvironmentProfile, ...] = (
    EnvironmentProfile("wood-desk-daylight", "wood", (0.45, 0.9), (5000, 6500), "window", (0, 3)),
    EnvironmentProfile("white-desk-office", "plain-light", (0.6, 1.0), (4000, 5000), "ceiling", (1, 4)),
    EnvironmentProfile("dark-cloth-evening", "dark-cloth", (0.12, 0.3), (2700, 3500), "lamp", (0, 2)),
    EnvironmentProfile("marble-kitchen", "marble", (0.6, 1.0), (3500, 4500), "ceiling", (0, 3)),
    EnvironmentProfile("notebook-paper", "ruled-paper", (0.5, 0.9), (4000, 6000), "window", (0, 2)),
    EnvironmentProfile("carpet-floor", "carpet", (0.25, 0.55), (3000, 4000), "lamp", (0, 2)),
    EnvironmentProfile("concrete-outdoor", "concrete", (0.8, 1.2), (6000, 7500), "overcast", (0, 2)),
    EnvironmentProfile("sofa-fabric", "fabric", (0.2, 0.5), (2700, 3500), "lamp", (0, 1)),
    EnvironmentProfile("glass-table-night", "dark-glass", (0.1, 0.25), (2700, 4000), "lamp", (0, 3), holdout=True),
)

# Parameter ranges per difficulty.  (probability, range) pairs gate optional
# effects.  "hard" deliberately includes photos outside the supported envelope.
DIFFICULTIES: dict[str, dict[str, Any]] = {
    "easy": {
        "yaw": 12.0, "pitch": 12.0, "roll": 6.0, "landscape": 0.0,
        "distance": (2.2, 3.2), "fill": (0.6, 0.85), "crop": 0.0,
        "glare": (0.2, (0.15, 0.5), (0.10, 0.22)), "reflectance": (0.012, 0.028),
        "optical_blur": (0.4, 0.8), "defocus": (0.0, (0.0, 0.0)), "motion": (0.0, (0.0, 0.0)),
        "read_noise": (0.003, 0.007), "shot_noise": (0.002, 0.005), "jpeg": (85, 95),
        "moire": (0.1, (0.2, 0.35)), "pwm": (0.1, (0.015, 0.035)), "distortion": (-0.03, 0.02),
        "hand": (0.1, 0.0), "exposure_bias": (-0.2, 0.2), "white_balance": 0.05,
        "messaging": (0.1, (1600, 2000)), "exif_rotate": 0.2, "exif_metadata": 0.7,
        "screen_brightness": (0.7, 1.2), "sharpen": (0.0, 0.4), "chromatic": (0.0, 0.0008),
    },
    "medium": {
        "yaw": 25.0, "pitch": 25.0, "roll": 15.0, "landscape": 0.05,
        "distance": (1.6, 2.6), "fill": (0.45, 0.9), "crop": 0.0,
        "glare": (0.5, (0.3, 1.2), (0.12, 0.35)), "reflectance": (0.02, 0.045),
        "optical_blur": (0.6, 1.2), "defocus": (0.3, (1.0, 2.0)), "motion": (0.2, (2.0, 5.0)),
        "read_noise": (0.005, 0.012), "shot_noise": (0.004, 0.01), "jpeg": (75, 92),
        "moire": (0.3, (0.25, 0.5)), "pwm": (0.25, (0.02, 0.06)), "distortion": (-0.06, 0.03),
        "hand": (0.3, 0.0), "exposure_bias": (-0.5, 0.4), "white_balance": 0.10,
        "messaging": (0.25, (1280, 1800)), "exif_rotate": 0.3, "exif_metadata": 0.6,
        "screen_brightness": (0.5, 1.3), "sharpen": (0.2, 0.7), "chromatic": (0.0005, 0.0015),
    },
    "hard": {
        "yaw": 38.0, "pitch": 38.0, "roll": 30.0, "landscape": 0.1,
        "distance": (1.2, 2.0), "fill": (0.3, 0.95), "crop": 0.05,
        "glare": (0.8, (0.8, 3.0), (0.15, 0.5)), "reflectance": (0.035, 0.07),
        "optical_blur": (0.9, 1.8), "defocus": (0.5, (1.5, 3.5)), "motion": (0.4, (4.0, 10.0)),
        "read_noise": (0.008, 0.02), "shot_noise": (0.008, 0.02), "jpeg": (60, 85),
        "moire": (0.5, (0.4, 0.7)), "pwm": (0.5, (0.05, 0.1)), "distortion": (-0.10, 0.05),
        "hand": (0.5, 0.35), "exposure_bias": (-0.9, 0.6), "white_balance": 0.15,
        "messaging": (0.4, (1280, 1600)), "exif_rotate": 0.3, "exif_metadata": 0.4,
        "screen_brightness": (0.4, 1.4), "sharpen": (0.3, 1.0), "chromatic": (0.001, 0.0025),
    },
}

PHOTO_SIZES = ((1500, 2000), (1800, 2400), (1350, 2400))
SKIN_TONES = ((0.95, 0.76, 0.62), (0.87, 0.64, 0.49), (0.70, 0.49, 0.36), (0.50, 0.33, 0.24), (0.33, 0.22, 0.16))


# ---------------------------------------------------------------- sampling


def _uniform(rng: np.random.Generator, bounds: Sequence[float]) -> float:
    return float(rng.uniform(float(bounds[0]), float(bounds[1])))


def sample_scene(
    rng: np.random.Generator,
    *,
    difficulty: str,
    allow_holdout: bool,
) -> dict[str, Any]:
    """Draw every random choice for one photo as plain JSON data."""

    ranges = DIFFICULTIES[difficulty]
    devices = [device for device in DEVICES if allow_holdout or not device.holdout]
    environments = [environment for environment in ENVIRONMENTS if allow_holdout or not environment.holdout]
    device = devices[int(rng.integers(len(devices)))]
    environment = environments[int(rng.integers(len(environments)))]
    landscape = bool(rng.random() < ranges["landscape"])
    width, height = PHOTO_SIZES[int(rng.integers(len(PHOTO_SIZES)))]
    if landscape:
        width, height = height, width

    def gated(key: str) -> Optional[float]:
        probability, bounds = ranges[key]
        return _uniform(rng, bounds) if rng.random() < probability else None

    glare_probability, glare_intensity, glare_size = ranges["glare"]
    glare = None
    if rng.random() < glare_probability:
        glare = {
            "shape": environment.light if environment.light in {"window", "lamp"} else "ceiling",
            "u": _uniform(rng, (-0.1, 1.1)),
            "v": _uniform(rng, (0.0, 1.0)),
            "size": _uniform(rng, glare_size),
            "aspect": _uniform(rng, (0.35, 1.0)),
            "angle": _uniform(rng, (-60.0, 60.0)),
            "intensity": _uniform(rng, glare_intensity),
            "softness": _uniform(rng, (0.25, 0.6)),
        }
    hand_probability, intrusion_probability = ranges["hand"]
    hand = None
    if rng.random() < hand_probability:
        hand = {
            "side": "left" if rng.random() < 0.5 else "right",
            "v": _uniform(rng, (0.45, 0.9)),
            "tone": list(SKIN_TONES[int(rng.integers(len(SKIN_TONES)))]),
            "intrusion": _uniform(rng, (0.02, 0.12)) if rng.random() < intrusion_probability else 0.0,
        }
    messaging = gated("messaging")
    return {
        "renderer": RENDERER_VERSION,
        "difficulty": difficulty,
        "device": device.name,
        "environment": environment.name,
        "seed": int(rng.integers(0, 2**31 - 1)),
        "photo_size": [int(width), int(height)],
        "pose": {
            "yaw": _uniform(rng, (-ranges["yaw"], ranges["yaw"])),
            "pitch": _uniform(rng, (-ranges["pitch"], ranges["pitch"])),
            "roll": _uniform(rng, (-ranges["roll"], ranges["roll"])) + (90.0 * (1 if rng.random() < 0.5 else -1) if landscape else 0.0),
            "distance": _uniform(rng, ranges["distance"]),
            "fill": _uniform(rng, ranges["fill"]),
            "offset": [_uniform(rng, (0.0, 1.0)), _uniform(rng, (0.0, 1.0))],
            "crop": bool(rng.random() < ranges["crop"]),
        },
        "lighting": {
            "ambient": _uniform(rng, environment.ambient),
            "color_temperature": _uniform(rng, environment.color_temperature),
            "direction": _uniform(rng, (0.0, 360.0)),
            "gradient": _uniform(rng, (0.15, 0.5)),
            "shadow": _uniform(rng, (0.25, 0.6)),
            "clutter": int(rng.integers(environment.clutter[0], environment.clutter[1] + 1)),
            "photographer_silhouette": bool(rng.random() < 0.5),
        },
        "screen": {
            "brightness": _uniform(rng, ranges["screen_brightness"]),
            "reflectance": _uniform(rng, ranges["reflectance"]),
            "moire": gated("moire"),
            "moire_pitch": _uniform(rng, (0.9, 1.6)),
            "pwm": gated("pwm"),
            "pwm_period": _uniform(rng, (18.0, 120.0)),
            "pwm_phase": _uniform(rng, (0.0, 2.0 * math.pi)),
        },
        "glare": glare,
        "hand": hand,
        "camera": {
            "exposure_bias": _uniform(rng, ranges["exposure_bias"]),
            "white_balance": [1.0 + _uniform(rng, (-ranges["white_balance"], ranges["white_balance"])) for _ in range(3)],
            "vignette": _uniform(rng, (0.1, 0.35)),
            "distortion": _uniform(rng, ranges["distortion"]),
            "chromatic": _uniform(rng, ranges["chromatic"]),
            "optical_blur": _uniform(rng, ranges["optical_blur"]),
            "defocus": gated("defocus"),
            "motion": gated("motion"),
            "motion_angle": _uniform(rng, (0.0, 180.0)),
            "read_noise": _uniform(rng, ranges["read_noise"]),
            "shot_noise": _uniform(rng, ranges["shot_noise"]),
            "sharpen": _uniform(rng, ranges["sharpen"]),
            "jpeg_quality": int(round(_uniform(rng, ranges["jpeg"]))),
            "messaging_long_side": int(messaging) if messaging else None,
            "exif_orientation": int(rng.choice([6, 8, 3])) if rng.random() < ranges["exif_rotate"] else 1,
            "exif_metadata": bool(rng.random() < ranges["exif_metadata"]),
        },
    }


# ---------------------------------------------------------------- textures


def _smooth_noise(rng: np.random.Generator, height: int, width: int, scale: float, octaves: int = 1) -> np.ndarray:
    out = np.zeros((height, width), np.float32)
    amplitude = 1.0
    total = 0.0
    for _ in range(octaves):
        grid_h = max(2, int(height / max(1.0, scale)))
        grid_w = max(2, int(width / max(1.0, scale)))
        small = rng.random((grid_h, grid_w)).astype(np.float32)
        out += amplitude * cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)
        total += amplitude
        amplitude *= 0.5
        scale *= 0.5
    out /= total
    low, high = float(out.min()), float(out.max())
    return (out - low) / max(1e-6, high - low)


def _coordinates(height: int, width: int, angle_degrees: float) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    angle = math.radians(angle_degrees)
    return xx * math.cos(angle) + yy * math.sin(angle)


def _surface_texture(kind: str, rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    """Procedural surface in sRGB [0, 1]."""

    def color(*choices: tuple[float, float, float]) -> np.ndarray:
        base = np.asarray(choices[int(rng.integers(len(choices)))], np.float32)
        return np.clip(base + rng.uniform(-0.04, 0.04, 3).astype(np.float32), 0, 1)

    fine = _smooth_noise(rng, height, width, 3.0, 2)[:, :, None]
    if kind == "wood":
        base = color((0.55, 0.38, 0.22), (0.40, 0.26, 0.15), (0.70, 0.55, 0.38), (0.30, 0.20, 0.13))
        vertical = bool(rng.random() < 0.5)
        # Grain runs along one axis: noise stretched along it gives streaks,
        # and a gently warped sinusoid across it gives growth rings.
        long_side, short_side = (height, width) if vertical else (width, height)
        streak_grid = rng.random((max(2, short_side // 3), max(2, long_side // 90))).astype(np.float32)
        streaks = cv2.resize(streak_grid, (long_side, short_side), interpolation=cv2.INTER_CUBIC)
        streaks = streaks.T if vertical else streaks
        warp = _smooth_noise(rng, height, width, max(height, width) / 2.0, 2)
        across = _coordinates(height, width, 0.0 if vertical else 90.0)
        period = rng.uniform(40.0, 110.0)
        rings = 0.5 + 0.5 * np.sin(2 * np.pi * (across / period + warp * 0.8))
        plank = base * (0.78 + 0.14 * rings[:, :, None] + 0.10 * np.clip(streaks, 0, 1)[:, :, None])
        return np.clip(plank * (0.94 + 0.06 * fine), 0, 1)
    if kind == "plain-light":
        base = color((0.90, 0.90, 0.88), (0.80, 0.82, 0.84), (0.70, 0.72, 0.74))
        return np.clip(base * (0.95 + 0.05 * fine), 0, 1)
    if kind == "dark-cloth":
        base = color((0.06, 0.06, 0.07), (0.10, 0.08, 0.07), (0.05, 0.07, 0.10))
        folds = _smooth_noise(rng, height, width, width / 4.0, 3)[:, :, None]
        return np.clip(base * (0.7 + 0.6 * folds) * (0.85 + 0.15 * fine), 0, 1)
    if kind == "marble":
        base = color((0.90, 0.89, 0.87), (0.82, 0.82, 0.84))
        turbulence = _smooth_noise(rng, height, width, width / 5.0, 4)
        veins = np.abs(np.sin(2 * np.pi * _coordinates(height, width, rng.uniform(0, 180)) / rng.uniform(250, 500) + turbulence * 7.0))
        return np.clip(base - (np.exp(-veins * 9.0) * 0.35)[:, :, None] * np.asarray([1.0, 1.0, 0.95], np.float32), 0, 1)
    if kind == "ruled-paper":
        base = color((0.96, 0.95, 0.90), (0.93, 0.93, 0.93))
        angle = rng.uniform(-8, 8)
        across = _coordinates(height, width, angle + 90)
        spacing = rng.uniform(26.0, 42.0)
        lines = (np.abs(((across % spacing) - spacing / 2)) > spacing / 2 - 1.2).astype(np.float32)
        paper = base * (0.96 + 0.04 * fine)
        paper = paper * (1 - lines[:, :, None] * 0.35) + lines[:, :, None] * np.asarray([0.55, 0.65, 0.85], np.float32) * 0.35
        margin = (np.abs(_coordinates(height, width, angle) - rng.uniform(0.1, 0.3) * width) < 1.5).astype(np.float32)
        return np.clip(paper * (1 - margin[:, :, None] * 0.5) + margin[:, :, None] * np.asarray([0.85, 0.3, 0.3], np.float32) * 0.5, 0, 1)
    if kind == "carpet":
        base = color((0.45, 0.40, 0.35), (0.30, 0.32, 0.38), (0.50, 0.30, 0.28))
        mottled = _smooth_noise(rng, height, width, 60.0, 4)[:, :, None]
        return np.clip(base * (0.7 + 0.3 * mottled) * (0.8 + 0.2 * fine), 0, 1)
    if kind == "concrete":
        base = color((0.60, 0.60, 0.58), (0.50, 0.50, 0.50))
        mottled = _smooth_noise(rng, height, width, 200.0, 5)[:, :, None]
        speckle = (rng.random((height, width)) > 0.995).astype(np.float32)[:, :, None]
        return np.clip(base * (0.75 + 0.35 * mottled) * (0.9 + 0.1 * fine) - speckle * 0.25, 0, 1)
    if kind == "fabric":
        base = color((0.35, 0.30, 0.45), (0.25, 0.40, 0.35), (0.55, 0.45, 0.30))
        period = rng.uniform(4.0, 7.0)
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        weave = 0.5 + 0.25 * (np.sin(2 * np.pi * xx / period) + np.sin(2 * np.pi * yy / period))
        folds = _smooth_noise(rng, height, width, width / 3.0, 2)[:, :, None]
        return np.clip(base * (0.8 + 0.2 * weave[:, :, None]) * (0.7 + 0.5 * folds), 0, 1)
    if kind == "dark-glass":
        base = color((0.04, 0.05, 0.06))
        streaks = _smooth_noise(rng, height, width, width / 2.0, 2)[:, :, None]
        return np.clip(base + streaks * 0.08, 0, 1)
    raise ValueError(f"unknown texture {kind}")


def _draw_clutter(surface: np.ndarray, rng: np.random.Generator, count: int) -> None:
    height, width = surface.shape[:2]
    short = min(height, width)
    for _ in range(count):
        kind = rng.choice(["pen", "paper", "mug", "note", "keys"])
        center = (float(rng.uniform(0, width)), float(rng.uniform(0, height)))
        angle = float(rng.uniform(0, 180))
        tint = rng.uniform(0.1, 0.95, 3).astype(np.float32)
        if kind == "pen":
            size = (short * rng.uniform(0.25, 0.5), short * rng.uniform(0.015, 0.025))
        elif kind == "paper":
            size = (short * rng.uniform(0.35, 0.6), short * rng.uniform(0.45, 0.8))
            tint = np.asarray([0.93, 0.93, 0.9], np.float32)
        elif kind == "note":
            size = (short * 0.18, short * 0.18)
            tint = np.asarray([0.95, 0.88, 0.35], np.float32)
        elif kind == "keys":
            size = (short * rng.uniform(0.06, 0.1), short * rng.uniform(0.02, 0.04))
            tint = np.asarray([0.6, 0.6, 0.6], np.float32)
        else:
            radius = int(short * rng.uniform(0.08, 0.14))
            cv2.circle(surface, (int(center[0]), int(center[1])), radius, tint.tolist(), -1, cv2.LINE_AA)
            cv2.circle(surface, (int(center[0]), int(center[1])), int(radius * 0.8), (tint * 0.4).tolist(), -1, cv2.LINE_AA)
            continue
        box = cv2.boxPoints((center, size, angle)).astype(np.int32)
        cv2.fillPoly(surface, [box], tint.tolist(), cv2.LINE_AA)


# ---------------------------------------------------------------- geometry


@dataclass(frozen=True)
class _Layout:
    """Phone geometry in display-width units, origin at the display's top-left."""

    screen: tuple[float, float, float, float]
    front: tuple[float, float, float, float]
    frame: tuple[float, float, float, float]
    outer: tuple[float, float, float, float]


def _layout(device: DeviceProfile) -> _Layout:
    screen = (0.0, 0.0, 1.0, device.screen_aspect)
    front = (-device.bezel, -device.top_bezel, 1.0 + device.bezel, device.screen_aspect + device.bottom_bezel)
    frame_width = 0.012
    frame = (front[0] - frame_width, front[1] - frame_width, front[2] + frame_width, front[3] + frame_width)
    case = device.case_thickness if device.case_rgb is not None else 0.0
    outer = (frame[0] - case, frame[1] - case, frame[2] + case, frame[3] + case)
    return _Layout(screen, front, frame, outer)


def _rect_corners(rect: Sequence[float]) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def _rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    y, p, r = (math.radians(value) for value in (yaw, pitch, roll))
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return rz @ rx @ ry


def _unit_to_photo_homography(layout: _Layout, pose: dict[str, Any], size: tuple[int, int]) -> np.ndarray:
    outer = _rect_corners(layout.outer)
    center = outer.mean(axis=0)
    body_height = layout.outer[3] - layout.outer[1]
    distance = float(pose["distance"]) * body_height
    rotation = _rotation(pose["yaw"], pose["pitch"], pose["roll"])
    projected = []
    for x, y in outer:
        point = rotation @ np.array([x - center[0], y - center[1], 0.0]) + np.array([0.0, 0.0, distance])
        projected.append([distance * point[0] / point[2], distance * point[1] / point[2]])
    projected = np.float32(projected)
    homography = cv2.getPerspectiveTransform(outer, projected)
    width, height = size
    low, high = projected.min(axis=0), projected.max(axis=0)
    box_w, box_h = float(high[0] - low[0]), float(high[1] - low[1])
    scale = float(pose["fill"]) * min(width / box_w, height / box_h)
    margin = 0.02 * min(width, height)
    slack_x = width - box_w * scale - 2 * margin
    slack_y = height - box_h * scale - 2 * margin
    offset_x, offset_y = pose["offset"]
    shift_x = margin + max(0.0, slack_x) * offset_x
    shift_y = margin + max(0.0, slack_y) * offset_y
    if pose.get("crop"):
        # Push the phone partly out of frame (outside the supported envelope).
        shift_x += (0.12 * box_w * scale) * (1 if offset_x > 0.5 else -1)
    placement = np.array(
        [[scale, 0, shift_x - low[0] * scale], [0, scale, shift_y - low[1] * scale], [0, 0, 1]],
        dtype=np.float64,
    )
    return placement @ homography


def _apply_h(homography: np.ndarray, points: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(np.asarray(points, np.float64).reshape(-1, 1, 2), homography).reshape(-1, 2)


# ---------------------------------------------------------------- phone texture


def _rounded_rect_mask(height: int, width: int, rect: Sequence[float], radius: float) -> np.ndarray:
    mask = np.zeros((height, width), np.uint8)
    x0, y0, x1, y1 = (int(round(value)) for value in rect)
    r = int(max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2)))
    if r <= 1:
        cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
        return mask
    cv2.rectangle(mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return mask


def _letterbox(screenshot: Image.Image, width: int, height: int) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Fit the screenshot into a display of another aspect with black bars.

    Returns the display image and (scale, offset_x, offset_y) mapping
    screenshot pixels to display pixels.
    """

    scale = min(width / screenshot.width, height / screenshot.height)
    fitted_w = max(1, int(round(screenshot.width * scale)))
    fitted_h = max(1, int(round(screenshot.height * scale)))
    fitted = np.asarray(screenshot.convert("RGB").resize((fitted_w, fitted_h), Image.Resampling.LANCZOS))
    display = np.zeros((height, width, 3), np.uint8)
    offset_x = (width - fitted_w) // 2
    offset_y = (height - fitted_h) // 2
    display[offset_y : offset_y + fitted_h, offset_x : offset_x + fitted_w] = fitted
    return display, (scale, float(offset_x), float(offset_y))


def _subpixel_display(display: np.ndarray, strength: float) -> np.ndarray:
    """Upsample 3x with an RGB-stripe subpixel mask so aliasing creates moire."""

    upsampled = cv2.resize(display, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST).astype(np.float32)
    mask = np.zeros((1, upsampled.shape[1], 3), np.float32)
    for channel in range(3):
        mask[0, channel::3, channel] = 3.0
    striped = upsampled * mask
    return np.clip(upsampled * (1.0 - strength) + striped * strength, 0, 255).astype(np.uint8)


def _phone_layers(
    device: DeviceProfile,
    layout: _Layout,
    screenshot: Image.Image,
    *,
    pixels_per_unit: float,
    moire: Optional[float],
) -> tuple[dict[str, np.ndarray], tuple[float, float, float]]:
    """Draw the phone front in texture space (texture px = units * ppu)."""

    ox, oy = layout.outer[0], layout.outer[1]
    tex_w = int(math.ceil((layout.outer[2] - layout.outer[0]) * pixels_per_unit)) + 2
    tex_h = int(math.ceil((layout.outer[3] - layout.outer[1]) * pixels_per_unit)) + 2

    def px(rect: Sequence[float]) -> tuple[float, float, float, float]:
        return ((rect[0] - ox) * pixels_per_unit, (rect[1] - oy) * pixels_per_unit,
                (rect[2] - ox) * pixels_per_unit, (rect[3] - oy) * pixels_per_unit)

    radius = device.body_radius * pixels_per_unit
    outer_mask = _rounded_rect_mask(tex_h, tex_w, px(layout.outer), radius)
    frame_mask = _rounded_rect_mask(tex_h, tex_w, px(layout.frame), radius * 0.92)
    front_mask = _rounded_rect_mask(tex_h, tex_w, px(layout.front), radius * 0.85)
    color = np.zeros((tex_h, tex_w, 3), np.float32)
    case_rgb = device.case_rgb or device.frame_rgb
    color[outer_mask > 0] = case_rgb
    color[frame_mask > 0] = device.frame_rgb
    color[front_mask > 0] = device.front_rgb
    # Rounded edges catch light: a soft highlight ring along the outline.
    edge = cv2.morphologyEx(outer_mask, cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)).astype(np.float32) / 255.0
    edge = cv2.GaussianBlur(edge, (0, 0), max(1.0, pixels_per_unit * 0.004))
    color = np.clip(color + edge[:, :, None] * 0.18, 0, 1)
    if device.home_button:
        cx = int((0.5 - ox) * pixels_per_unit)
        cy = int((layout.screen[3] + device.bottom_bezel / 2 - oy) * pixels_per_unit)
        r = int(device.bottom_bezel * 0.32 * pixels_per_unit)
        ring = tuple(float(value) * 0.8 for value in device.frame_rgb)
        cv2.circle(color, (cx, cy), r, ring, max(1, int(r * 0.08)), cv2.LINE_AA)

    sx0, sy0, sx1, sy1 = px(layout.screen)
    screen_w = int(round(sx1 - sx0))
    screen_h = int(round(sy1 - sy0))
    if moire:
        # Native display resolution chosen so a display pixel spans roughly
        # moire_pitch photo pixels; subpixels are 1/3 of that.
        native_w = max(16, int(round(screen_w / 3.0)))
        native_h = max(16, int(round(screen_h / 3.0)))
        native, fit = _letterbox(screenshot, native_w, native_h)
        display = _subpixel_display(native, moire)
        display = cv2.resize(display, (screen_w, screen_h), interpolation=cv2.INTER_NEAREST)
        fit = (fit[0] * 3.0, fit[1] * 3.0, fit[2] * 3.0)
    else:
        display, fit = _letterbox(screenshot, screen_w, screen_h)
    screen_mask_local = _rounded_rect_mask(screen_h, screen_w, (0, 0, screen_w - 1, screen_h - 1), device.screen_radius * pixels_per_unit)
    cut = np.zeros((screen_h, screen_w), np.uint8)
    if device.cutout == "notch":
        cut = _rounded_rect_mask(screen_h, screen_w, (screen_w * 0.27, -screen_w * 0.05, screen_w * 0.73, screen_w * 0.075), screen_w * 0.04)
    elif device.cutout == "punch":
        cv2.circle(cut, (screen_w // 2, int(screen_w * 0.05)), int(screen_w * 0.022), 255, -1, cv2.LINE_AA)
    elif device.cutout == "island":
        cut = _rounded_rect_mask(screen_h, screen_w, (screen_w * 0.36, screen_w * 0.025, screen_w * 0.64, screen_w * 0.085), screen_w * 0.03)
    emissive = ((screen_mask_local > 0) & (cut == 0)).astype(np.uint8) * 255
    display = display.copy()
    display[emissive == 0] = 0
    screen_layer = np.zeros((tex_h, tex_w, 3), np.uint8)
    screen_mask = np.zeros((tex_h, tex_w), np.uint8)
    top, left = int(round(sy0)), int(round(sx0))
    screen_layer[top : top + screen_h, left : left + screen_w] = display
    screen_mask[top : top + screen_h, left : left + screen_w] = emissive
    glass_mask = front_mask
    layers = {
        "color": (color * 255).astype(np.uint8),
        "alpha": outer_mask,
        "screen": screen_layer,
        "screen_mask": screen_mask,
        "glass": glass_mask,
    }
    # Screenshot pixel -> unit coordinates: display pixel / ppu + screen origin.
    unit_fit = (fit[0] / pixels_per_unit, layout.screen[0] + fit[1] / pixels_per_unit, layout.screen[1] + fit[2] / pixels_per_unit)
    return layers, unit_fit


# ---------------------------------------------------------------- light and camera


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 0, 1)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * values ** (1 / 2.4) - 0.055).astype(np.float32)


def _temperature_tint(kelvin: float) -> np.ndarray:
    anchors = ((2700, (1.0, 0.78, 0.52)), (4000, (1.0, 0.91, 0.80)), (5500, (1.0, 1.0, 1.0)), (6500, (0.93, 0.97, 1.05)), (7500, (0.88, 0.94, 1.08)))
    for (k0, c0), (k1, c1) in zip(anchors, anchors[1:]):
        if kelvin <= k1:
            weight = max(0.0, min(1.0, (kelvin - k0) / (k1 - k0)))
            return np.asarray([a + (b - a) * weight for a, b in zip(c0, c1)], np.float32)
    return np.asarray(anchors[-1][1], np.float32)


def _light_field(height: int, width: int, lighting: dict[str, Any], light: str) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx, ny = (xx / width - 0.5) * 2, (yy / height - 0.5) * 2
    angle = math.radians(lighting["direction"])
    if light == "lamp":
        cx, cy = math.cos(angle) * 1.1, math.sin(angle) * 1.1
        falloff = 1.0 / (1.0 + ((nx - cx) ** 2 + (ny - cy) ** 2) * 0.9)
        field = falloff / float(falloff.max())
    else:
        directional = nx * math.cos(angle) + ny * math.sin(angle)
        field = 1.0 + lighting["gradient"] * directional * (0.5 if light == "overcast" else 1.0)
    tint = _temperature_tint(lighting["color_temperature"])
    return (field[:, :, None] * lighting["ambient"] * tint).astype(np.float32)


def _distort_points(points: np.ndarray, k1: float, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    center = np.array([width / 2.0, height / 2.0])
    norm = math.hypot(width / 2.0, height / 2.0)
    out = []
    for point in np.asarray(points, np.float64):
        undistorted = point - center
        guess = undistorted.copy()
        for _ in range(12):
            r2 = float((guess / norm) @ (guess / norm))
            guess = undistorted / (1.0 + k1 * r2)
        out.append(guess + center)
    return np.asarray(out)


def _distort_image(image: np.ndarray, k1: float) -> np.ndarray:
    height, width = image.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = width / 2.0, height / 2.0
    norm = math.hypot(cx, cy)
    dx, dy = xx - cx, yy - cy
    r2 = (dx * dx + dy * dy) / (norm * norm)
    factor = 1.0 + k1 * r2
    return cv2.remap(image, (cx + dx * factor).astype(np.float32), (cy + dy * factor).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _chromatic(image: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return image
    height, width = image.shape[:2]
    out = image.copy()
    for channel, scale in ((0, 1.0 + amount), (2, 1.0 - amount)):
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), 0.0, scale)
        out[:, :, channel] = cv2.warpAffine(image[:, :, channel], matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return out


def _disk_kernel(radius: float) -> np.ndarray:
    size = int(math.ceil(radius)) * 2 + 1
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) - size // 2
    kernel = (xx * xx + yy * yy <= radius * radius).astype(np.float32)
    return kernel / kernel.sum()


def _motion_kernel(length: float, angle: float) -> np.ndarray:
    size = int(math.ceil(length)) | 1
    kernel = np.zeros((size, size), np.float32)
    center = size // 2
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    cv2.line(kernel, (int(round(center - dx * length / 2)), int(round(center - dy * length / 2))),
             (int(round(center + dx * length / 2)), int(round(center + dy * length / 2))), 1.0, 1, cv2.LINE_AA)
    return kernel / max(1e-6, kernel.sum())


def _polygon_mask(points: np.ndarray, size: tuple[int, int], blur: float) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), np.float32)
    cv2.fillPoly(mask, [np.round(points).astype(np.int32)], 1.0, cv2.LINE_AA)
    return cv2.GaussianBlur(mask, (0, 0), blur) if blur > 0.3 else mask


def _ellipse_points(center: Sequence[float], radii: Sequence[float], angle: float, count: int = 48) -> np.ndarray:
    theta = np.linspace(0, 2 * np.pi, count, endpoint=False)
    local = np.stack([np.cos(theta) * radii[0], np.sin(theta) * radii[1]], axis=1)
    rotation = np.array([[math.cos(math.radians(angle)), -math.sin(math.radians(angle))], [math.sin(math.radians(angle)), math.cos(math.radians(angle))]])
    return local @ rotation.T + np.asarray(center)


# ---------------------------------------------------------------- render


@dataclass
class RenderedPhoto:
    payload: bytes
    screen_corners: list[dict[str, float]]
    board_corners: Optional[list[dict[str, float]]]
    image_size: tuple[int, int]  # oriented width/height as the importer sees it
    envelope: dict[str, Any]


def render_scene(
    screenshot: Image.Image,
    scene: dict[str, Any],
    *,
    board_box: Optional[Sequence[float]] = None,
    board_grid: Optional[tuple[int, int]] = None,
) -> RenderedPhoto:
    """Render one photo. ``board_box`` is (x, y, w, h) in screenshot pixels."""

    rng = np.random.default_rng(int(scene["seed"]))
    device = next(item for item in DEVICES if item.name == scene["device"])
    environment = next(item for item in ENVIRONMENTS if item.name == scene["environment"])
    width, height = (int(value) for value in scene["photo_size"])
    size = (width, height)
    layout = _layout(device)
    unit_h = _unit_to_photo_homography(layout, scene["pose"], size)

    # Texture resolution: slightly oversample the projected display, or place
    # native display pixels near the camera pixel pitch for moire.
    screen_quad = _apply_h(unit_h, _rect_corners(layout.screen))
    projected_width = float(np.mean([np.linalg.norm(screen_quad[1] - screen_quad[0]), np.linalg.norm(screen_quad[2] - screen_quad[3])]))
    moire = scene["screen"]["moire"]
    if moire:
        pixels_per_unit = min(3.0 * projected_width / float(scene["screen"]["moire_pitch"]), 5200.0 / device.screen_aspect)
    else:
        pixels_per_unit = min(projected_width * 1.15, 4200.0 / device.screen_aspect)
    layers, unit_fit = _phone_layers(
        device,
        layout,
        screenshot,
        pixels_per_unit=pixels_per_unit,
        moire=moire,
    )
    texture_to_unit = np.array(
        [[1.0 / pixels_per_unit, 0, layout.outer[0]], [0, 1.0 / pixels_per_unit, layout.outer[1]], [0, 0, 1]]
    )
    texture_h = unit_h @ texture_to_unit

    def warp(layer: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(layer, texture_h, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    alpha = warp(layers["alpha"]).astype(np.float32) / 255.0
    screen_mask = warp(layers["screen_mask"]).astype(np.float32) / 255.0
    glass = warp(layers["glass"]).astype(np.float32) / 255.0
    body = _srgb_to_linear(warp(layers["color"]).astype(np.float32) / 255.0)
    emission = _srgb_to_linear(warp(layers["screen"]).astype(np.float32) / 255.0)
    del layers

    # Environment and lighting.
    lighting = scene["lighting"]
    surface = _surface_texture(environment.texture, rng, height, width)
    _draw_clutter(surface, rng, int(lighting["clutter"]))
    light = _light_field(height, width, lighting, environment.light)
    shadow_offset = np.array([math.cos(math.radians(lighting["direction"] + 180)), math.sin(math.radians(lighting["direction"] + 180))]) * 0.02 * min(size)
    shadow = cv2.warpAffine(alpha, np.float32([[1, 0, shadow_offset[0]], [0, 1, shadow_offset[1]]]), size)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 0.025 * min(size))
    scene_linear = _srgb_to_linear(surface) * light * (1.0 - lighting["shadow"] * shadow[:, :, None])

    screen = scene["screen"]
    emission *= float(screen["brightness"])
    if screen["pwm"]:
        rows = np.arange(height, dtype=np.float32)
        emission *= (1.0 + float(screen["pwm"]) * np.sin(2 * np.pi * rows / float(screen["pwm_period"]) + float(screen["pwm_phase"])))[:, None, None]
    tint = _temperature_tint(lighting["color_temperature"])
    room = _smooth_noise(rng, height, width, min(size) / 2.0, 2)[:, :, None] * 0.8 + 0.6
    # Glass reflects a few percent of the room in front of the phone.
    reflection = room * float(lighting["ambient"]) * tint * float(screen["reflectance"])
    if lighting["photographer_silhouette"]:
        center = _apply_h(unit_h, np.float32([[0.5, device.screen_aspect * rng.uniform(0.2, 0.6)]]))[0]
        silhouette = _polygon_mask(_ellipse_points(center, (projected_width * 0.45, projected_width * 0.7), 0.0), size, projected_width * 0.12)
        reflection *= (1.0 - 0.85 * silhouette[:, :, None])
    glare_field = np.zeros((height, width), np.float32)
    glare = scene["glare"]
    if glare:
        center_unit = (float(glare["u"]), float(glare["v"]) * device.screen_aspect)
        radii = (float(glare["size"]), float(glare["size"]) * float(glare["aspect"]))
        if glare["shape"] == "window":
            half_w, half_h = radii
            local = np.float32([[-half_w, -half_h], [half_w, -half_h], [half_w, half_h], [-half_w, half_h]])
            rotation = np.array([[math.cos(math.radians(glare["angle"])), -math.sin(math.radians(glare["angle"]))], [math.sin(math.radians(glare["angle"])), math.cos(math.radians(glare["angle"]))]])
            outline_units = local @ rotation.T + np.asarray(center_unit)
        else:
            outline_units = _ellipse_points(center_unit, radii, float(glare["angle"]))
        outline = _apply_h(unit_h, outline_units)
        glare_field = _polygon_mask(outline, size, projected_width * float(glare["size"]) * float(glare["softness"]))
        glare_field *= float(glare["intensity"])
    specular = glass[:, :, None] * (reflection + glare_field[:, :, None] * tint)

    hand = scene["hand"]
    if hand:
        # Palm and fingers sit behind the phone; only what sticks out shows.
        side_x = layout.outer[0] if hand["side"] == "left" else layout.outer[2]
        other_x = layout.outer[2] if hand["side"] == "left" else layout.outer[0]
        direction = 1.0 if hand["side"] == "left" else -1.0
        grip_y = layout.screen[3] * float(hand["v"])
        behind = _polygon_mask(
            _apply_h(unit_h, _ellipse_points((side_x - direction * 0.3, grip_y + 0.25), (0.55, 0.85), -12.0 * direction)),
            size,
            projected_width * 0.012,
        )
        for finger in range(3):
            finger_center = (other_x + direction * 0.02, grip_y - 0.35 + finger * 0.24)
            behind = np.maximum(
                behind,
                _polygon_mask(_apply_h(unit_h, _ellipse_points(finger_center, (0.13, 0.085), 0.0)), size, projected_width * 0.008),
            )
        skin_behind = _srgb_to_linear(np.asarray(hand["tone"], np.float32)) * light * 0.85
        scene_linear = scene_linear * (1.0 - behind[:, :, None]) + skin_behind * behind[:, :, None]

    phone = body * light * (1.0 - screen_mask[:, :, None]) + emission * screen_mask[:, :, None]
    image = scene_linear * (1.0 - alpha[:, :, None]) + phone * alpha[:, :, None] + specular
    del scene_linear, phone, emission, body, specular, reflection

    hand_mask = np.zeros((height, width), np.float32)
    if hand:
        # The thumb rests on the front, over the bezel (or the display when
        # the scene asks for an intrusion).
        side_x = layout.outer[0] if hand["side"] == "left" else layout.outer[2]
        side_width = (layout.screen[0] - layout.outer[0])
        reach = side_width * 0.85 + float(hand["intrusion"])
        direction = 1.0 if hand["side"] == "left" else -1.0
        thumb_center = (side_x + direction * (reach - 0.16), layout.screen[3] * float(hand["v"]))
        outline = _apply_h(unit_h, _ellipse_points(thumb_center, (0.16, 0.34), 8.0 * direction))
        hand_mask = _polygon_mask(outline, size, projected_width * 0.01)
        skin = _srgb_to_linear(np.asarray(hand["tone"], np.float32)) * light
        image = image * (1.0 - hand_mask[:, :, None]) + skin * hand_mask[:, :, None]

    camera = scene["camera"]
    luminance = image @ np.asarray([0.2126, 0.7152, 0.0722], np.float32)
    key = float(np.percentile(luminance, 99.0))
    gain = 0.85 / max(1e-4, key) * (2.0 ** float(camera["exposure_bias"]))
    image *= gain * np.asarray(camera["white_balance"], np.float32)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    radius2 = ((xx - width / 2) / (width / 2)) ** 2 + ((yy - height / 2) / (height / 2)) ** 2
    image *= (1.0 - float(camera["vignette"]) * radius2 * 0.5)[:, :, None]
    del yy, xx, radius2, luminance
    image = image / (1.0 + (np.maximum(image, 0) / 1.2) ** 6) ** (1.0 / 6.0)
    image = _linear_to_srgb(image)

    image = _chromatic(image, float(camera["chromatic"]))
    image = _distort_image(image, float(camera["distortion"]))
    image = cv2.GaussianBlur(image, (0, 0), float(camera["optical_blur"]))
    if camera["defocus"]:
        image = cv2.filter2D(image, -1, _disk_kernel(float(camera["defocus"])))
    if camera["motion"]:
        image = cv2.filter2D(image, -1, _motion_kernel(float(camera["motion"]), float(camera["motion_angle"])))
    sigma = np.sqrt(float(camera["read_noise"]) ** 2 + float(camera["shot_noise"]) * np.clip(image, 0, 1))
    image = image + rng.standard_normal(image.shape).astype(np.float32) * sigma
    if camera["sharpen"] > 0:
        image = image + float(camera["sharpen"]) * (image - cv2.GaussianBlur(image, (0, 0), 1.0))
    output = (np.clip(image, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    del image, sigma

    # Labels through distortion and optional downscale.
    screen_points = _distort_points(_apply_h(unit_h, _rect_corners(layout.screen)), float(camera["distortion"]), size)
    board_points = None
    if board_box is not None:
        bx, by, bw, bh = (float(value) for value in board_box)
        scale, off_x, off_y = unit_fit
        board_units = np.float32([[bx, by], [bx + bw, by], [bx + bw, by + bh], [bx, by + bh]]) * scale + np.float32([off_x, off_y])
        board_points = _distort_points(_apply_h(unit_h, board_units), float(camera["distortion"]), size)
    final_scale = 1.0
    if camera["messaging_long_side"] and max(size) > int(camera["messaging_long_side"]):
        final_scale = int(camera["messaging_long_side"]) / float(max(size))
        output = cv2.resize(output, (int(round(width * final_scale)), int(round(height * final_scale))), interpolation=cv2.INTER_AREA)
        screen_points = screen_points * final_scale
        board_points = board_points * final_scale if board_points is not None else None
    out_h, out_w = output.shape[:2]

    envelope = _envelope(
        screen_points=screen_points,
        board_points=board_points,
        board_grid=board_grid,
        size=(out_w, out_h),
        glare_field=glare_field * gain,
        screen_mask=screen_mask,
        hand_mask=hand_mask,
        camera=camera,
        final_scale=final_scale,
    )
    payload = _encode(output, camera, device)
    return RenderedPhoto(
        payload=payload,
        screen_corners=[{"x": round(float(x), 3), "y": round(float(y), 3)} for x, y in screen_points],
        board_corners=(
            [{"x": round(float(x), 3), "y": round(float(y), 3)} for x, y in board_points]
            if board_points is not None
            else None
        ),
        image_size=(out_w, out_h),
        envelope=envelope,
    )


def _envelope(
    *,
    screen_points: np.ndarray,
    board_points: Optional[np.ndarray],
    board_grid: Optional[tuple[int, int]],
    size: tuple[int, int],
    glare_field: np.ndarray,
    screen_mask: np.ndarray,
    hand_mask: np.ndarray,
    camera: dict[str, Any],
    final_scale: float,
) -> dict[str, Any]:
    """Objective capture metrics and the plan's supported-envelope decision."""

    width, height = size
    sides = [float(np.linalg.norm(screen_points[(index + 1) % 4] - screen_points[index])) for index in range(4)]
    margin = 2.0
    corners_in_frame = bool(
        np.all(screen_points[:, 0] >= margin) and np.all(screen_points[:, 0] <= width - margin)
        and np.all(screen_points[:, 1] >= margin) and np.all(screen_points[:, 1] <= height - margin)
    )
    on_screen = screen_mask > 0.5
    screen_area = max(1, int(np.count_nonzero(on_screen)))
    glare_fraction = float(np.count_nonzero((glare_field > 0.2) & on_screen)) / screen_area
    occlusion = float(np.count_nonzero((hand_mask > 0.5) & on_screen)) / screen_area
    blur_px = math.sqrt(
        float(camera["optical_blur"]) ** 2
        + (float(camera["defocus"] or 0.0) / math.sqrt(2.0)) ** 2
        + (float(camera["motion"] or 0.0) / math.sqrt(12.0)) ** 2
    ) * final_scale
    cell_px = None
    board_short_side = None
    if board_points is not None:
        board_sides = [float(np.linalg.norm(board_points[(index + 1) % 4] - board_points[index])) for index in range(4)]
        board_short_side = min(board_sides)
        if board_grid:
            cell_px = min(min(board_sides[0], board_sides[2]) / max(1, board_grid[1]), min(board_sides[1], board_sides[3]) / max(1, board_grid[0]))
    foreshortening = min(min(sides[0], sides[2]) / max(sides[0], sides[2]), min(sides[1], sides[3]) / max(sides[1], sides[3]))
    display_short_side = min(sides)
    reasons = []
    if not corners_in_frame:
        reasons.append("screen corner out of frame")
    if occlusion > 0.002:
        reasons.append("screen partly covered")
    # Same resolution floors the importer's review uses (prepared board side
    # and cell size), so "in envelope" means "should import without review".
    if board_short_side is not None and board_short_side < 400:
        reasons.append("board short side below 400 px")
    elif board_short_side is None and display_short_side < 600:
        reasons.append("display short side below 600 px")
    if glare_fraction > 0.10:
        reasons.append("glare over 10% of the display")
    if foreshortening < 0.6:
        reasons.append("extreme perspective")
    if cell_px is not None and cell_px < 26:
        reasons.append("cells below 26 px")
    if blur_px > 0.12 * (cell_px or 80.0):
        reasons.append("blur large relative to cells")
    return {
        "in_envelope": not reasons,
        "reasons": reasons,
        "display_short_side_px": round(display_short_side, 1),
        "board_short_side_px": round(board_short_side, 1) if board_short_side is not None else None,
        "foreshortening": round(foreshortening, 4),
        "glare_fraction": round(glare_fraction, 5),
        "occlusion_fraction": round(occlusion, 5),
        "blur_px": round(blur_px, 3),
        "cell_px": round(cell_px, 2) if cell_px is not None else None,
    }


_EXIF_STORAGE_TRANSPOSE = {
    # Stored pixels are pre-rotated so that applying the EXIF orientation
    # (as the importer does) restores the rendered image.
    6: Image.Transpose.ROTATE_90,
    8: Image.Transpose.ROTATE_270,
    3: Image.Transpose.ROTATE_180,
}


def _encode(output: np.ndarray, camera: dict[str, Any], device: DeviceProfile) -> bytes:
    image = Image.fromarray(output, mode="RGB")
    orientation = int(camera["exif_orientation"])
    exif = Image.Exif()
    if orientation in _EXIF_STORAGE_TRANSPOSE:
        image = image.transpose(_EXIF_STORAGE_TRANSPOSE[orientation])
        exif[274] = orientation
    if camera["exif_metadata"]:
        exif[271] = "SyntheticCamera"
        exif[272] = f"mock-{device.name}"
    payload = io.BytesIO()
    image.save(
        payload,
        format="JPEG",
        quality=int(camera["jpeg_quality"]),
        subsampling=2,
        exif=exif.tobytes() if len(exif) else b"",
    )
    return payload.getvalue()


def scene_summary(scene: dict[str, Any]) -> dict[str, Any]:
    """Compact description for manifests and grouped reports."""

    return {
        "difficulty": scene["difficulty"],
        "device": scene["device"],
        "environment": scene["environment"],
        "yaw": round(scene["pose"]["yaw"], 2),
        "pitch": round(scene["pose"]["pitch"], 2),
        "roll": round(scene["pose"]["roll"], 2),
        "glare": bool(scene["glare"]),
        "hand": bool(scene["hand"]),
        "moire": bool(scene["screen"]["moire"]),
        "pwm_banding": bool(scene["screen"]["pwm"]),
        "defocus": bool(scene["camera"]["defocus"]),
        "motion_blur": bool(scene["camera"]["motion"]),
        "messaging_downscale": bool(scene["camera"]["messaging_long_side"]),
        "exif_orientation": scene["camera"]["exif_orientation"],
        "exif_metadata": scene["camera"]["exif_metadata"],
    }


def device_profiles() -> list[dict[str, Any]]:
    return [asdict(device) for device in DEVICES]
