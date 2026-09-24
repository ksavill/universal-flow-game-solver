"""Build deterministic camera-like photographs from the reference screenshot corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import time
from typing import Any

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "reference_screenshot_corpus"
DEFAULT_OUTPUT = ROOT / "reference_camera_corpus"
DEFAULT_DIFFICULTY_MIX = {"easy": 0.4, "medium": 0.4, "hard": 0.2}
DEFAULT_SPLITS = {"train": 0.7, "val": 0.15, "test": 0.15}

try:
    from scripts import synthetic_camera
except ModuleNotFoundError:  # Running as `python scripts/build_camera_photo_corpus.py`.
    import synthetic_camera  # type: ignore[no-redef]


def _source_split(source_id: str, fractions: dict[str, float]) -> str:
    """Split by source screenshot so no puzzle appears in two splits."""

    position = int(hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8], 16) / float(0xFFFFFFFF)
    total = sum(fractions.values())
    cumulative = 0.0
    for name, fraction in fractions.items():
        cumulative += fraction / total
        if position < cumulative:
            return name
    return list(fractions)[-1]


def _board_box(generation_data: dict[str, Any]) -> tuple[float, float, float, float] | None:
    try:
        return tuple(float(generation_data[key]) for key in ("crop_x", "crop_y", "crop_width", "crop_height"))  # type: ignore[return-value]
    except (KeyError, TypeError, ValueError):
        return None


def _board_grid(source_entry: dict[str, Any]) -> tuple[int, int] | None:
    grid = source_entry.get("grid") if isinstance(source_entry.get("grid"), dict) else {}
    data = source_entry.get("generation_data") or {}
    try:
        rows = int(grid.get("rows") or data.get("grid_height"))
        cols = int(grid.get("cols") or data.get("grid_width"))
    except (TypeError, ValueError):
        return None
    return (rows, cols) if rows > 0 and cols > 0 else None


def _scene_entries(
    screenshot: Image.Image,
    source_entry: dict[str, Any],
    *,
    relative_source: str,
    photos: Path,
    seed: int,
    source_index: int,
    scenes: int,
    difficulty_mix: dict[str, float],
    split_fractions: dict[str, float],
) -> list[dict[str, Any]]:
    source_id = str(source_entry.get("id") or source_index)
    split = _source_split(source_id, split_fractions)
    rng = np.random.default_rng([seed, int(hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8], 16)])
    names = list(difficulty_mix)
    weights = np.asarray([difficulty_mix[name] for name in names], np.float64)
    generation_data = source_entry.get("generation_data") or {}
    entries = []
    for scene_index in range(scenes):
        difficulty = str(rng.choice(names, p=weights / weights.sum()))
        # Held-out devices and environments only appear in the test split, so
        # test results also measure generalization to unseen hardware/rooms.
        scene = synthetic_camera.sample_scene(rng, difficulty=difficulty, allow_holdout=split == "test")
        rendered = synthetic_camera.render_scene(
            screenshot,
            scene,
            board_box=_board_box(generation_data),
            board_grid=_board_grid(source_entry),
        )
        digest = _sha256(rendered.payload)
        filename = f"{source_id}-s{scene_index}-{digest[:12]}.jpg"
        (photos / filename).write_bytes(rendered.payload)
        entries.append(
            {
                "id": digest[:16],
                "sha256": digest,
                "image": f"photos/{filename}",
                "source_id": source_entry.get("id"),
                "source_sha256": source_entry.get("sha256"),
                "source_image": relative_source,
                "source_generation_data": generation_data,
                "geometry": source_entry.get("geometry"),
                "variant_index": scene_index,
                "expected_pairs": source_entry.get("expected_pairs"),
                "split": split,
                "difficulty": difficulty,
                "device": scene["device"],
                "environment": scene["environment"],
                "in_envelope": rendered.envelope["in_envelope"],
                "envelope": rendered.envelope,
                "image_size": list(rendered.image_size),
                "screen_corners": rendered.screen_corners,
                "board_corners": rendered.board_corners,
                "degradation": synthetic_camera.scene_summary(scene),
                "scene": scene,
            }
        )
    return entries


def write_preview(manifest: dict[str, Any], corpus: Path, output: Path, *, count: int = 24) -> Path:
    """Contact sheet with labeled corners, to eyeball realism and label accuracy."""

    from PIL import ImageDraw, ImageOps

    tiles = []
    for entry in manifest["entries"][:count]:
        with Image.open(corpus / entry["image"]) as opened:
            photo = ImageOps.exif_transpose(opened).convert("RGB")
        draw = ImageDraw.Draw(photo)
        for key, color in (("screen_corners", (40, 255, 90)), ("board_corners", (255, 220, 0))):
            points = entry.get(key)
            if points:
                outline = [(point["x"], point["y"]) for point in points]
                draw.line(outline + outline[:1], fill=color, width=max(2, photo.width // 300))
        photo.thumbnail((360, 480))
        tile = Image.new("RGB", (360, 520), (24, 24, 24))
        tile.paste(photo, ((360 - photo.width) // 2, 0))
        caption = f"{entry.get('difficulty')} | {entry.get('device')}\n{entry.get('environment')} | {'in' if entry.get('in_envelope') else 'out'}"
        ImageDraw.Draw(tile).multiline_text((6, 484), caption, fill=(230, 230, 230))
        tiles.append(tile)
    columns = 6
    rows = max(1, math.ceil(len(tiles) / columns))
    sheet = Image.new("RGB", (columns * 360, rows * 520), (10, 10, 10))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * 360, (index // columns) * 520))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _camera_variant(
    image: Image.Image,
    *,
    seed: int,
    variant_index: int,
) -> tuple[bytes, list[dict[str, float]], dict[str, Any]]:
    rng = random.Random(seed * 1009 + variant_index * 9176)
    screen = image.convert("RGB")
    screen.thumbnail((1700, 1700), Image.Resampling.LANCZOS)
    source = np.asarray(screen)
    screen_h, screen_w = source.shape[:2]

    canvas_w = max(900, int(screen_w * 1.62))
    canvas_h = max(900, int(screen_h * 1.46))
    base = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
    background = rng.randint(28, 64)
    vertical_gradient = np.linspace(-9.0, 10.0, canvas_h, dtype=np.float32).reshape(-1, 1, 1)
    base[:] = np.asarray(
        [background + rng.randint(-5, 5), background + rng.randint(-5, 5), background + rng.randint(-5, 5)],
        dtype=np.float32,
    )
    base += vertical_gradient
    noise = np.random.default_rng(seed + variant_index).normal(0.0, 2.2, base.shape).astype(np.float32)
    base = np.clip(base + noise, 0, 255).astype(np.uint8)

    left = (canvas_w - screen_w) * 0.5
    top = (canvas_h - screen_h) * 0.5
    horizontal_skew = screen_w * rng.uniform(0.045, 0.13)
    vertical_skew = screen_h * rng.uniform(0.035, 0.11)
    lean = rng.choice((-1.0, 1.0))
    corners = np.float32(
        [
            [left + horizontal_skew * (0.25 if lean > 0 else 0.95), top + vertical_skew * 0.10],
            [left + screen_w - horizontal_skew * (0.95 if lean > 0 else 0.20), top + vertical_skew],
            [left + screen_w + horizontal_skew * (0.30 if lean > 0 else 0.80), top + screen_h - vertical_skew * 0.25],
            [left - horizontal_skew * (0.75 if lean > 0 else 0.25), top + screen_h - vertical_skew],
        ]
    )
    source_corners = np.float32(
        [[0, 0], [screen_w - 1, 0], [screen_w - 1, screen_h - 1], [0, screen_h - 1]]
    )
    matrix = cv2.getPerspectiveTransform(source_corners, corners)
    warped = cv2.warpPerspective(source, matrix, (canvas_w, canvas_h), flags=cv2.INTER_CUBIC)
    mask = cv2.warpPerspective(
        np.full((screen_h, screen_w), 255, dtype=np.uint8),
        matrix,
        (canvas_w, canvas_h),
    )
    base[mask > 0] = warped[mask > 0]

    banding_amplitude = rng.uniform(0.0, 8.0) if variant_index == 2 else 0.0
    if banding_amplitude:
        rows = np.arange(canvas_h, dtype=np.float32)
        banding = np.sin(rows * math.pi * 2.0 / rng.uniform(34.0, 72.0)) * banding_amplitude
        inside = mask > 0
        for row in range(canvas_h):
            base[row][inside[row]] = np.clip(
                base[row][inside[row]].astype(np.float32) + banding[row],
                0,
                255,
            ).astype(np.uint8)

    glare_fraction = 0.0
    if variant_index == 1:
        glare = np.zeros_like(base)
        center = (int(canvas_w * rng.uniform(0.62, 0.78)), int(canvas_h * rng.uniform(0.24, 0.42)))
        axes = (max(40, int(screen_w * 0.13)), max(28, int(screen_h * 0.07)))
        cv2.ellipse(glare, center, axes, rng.uniform(-24, 24), 0, 360, (255, 255, 255), -1)
        glare_mask = (cv2.cvtColor(glare, cv2.COLOR_RGB2GRAY) > 0) & (mask > 0)
        glare_fraction = float(np.count_nonzero(glare_mask)) / float(max(1, np.count_nonzero(mask)))
        base[glare_mask] = cv2.addWeighted(base, 0.54, glare, 0.46, 0)[glare_mask]

    # Variants 3-5 model what a real phone camera adds on top of variant 0:
    # 3 = sensor noise, color cast and vignetting; 4 = in-plane rotation;
    # 5 = moire from the photographed pixel grid.
    moire_amplitude = 0.0
    if variant_index == 5:
        moire_amplitude = rng.uniform(5.0, 9.0)
        yy, xx = np.mgrid[0:canvas_h, 0:canvas_w].astype(np.float32)
        period_x, period_y = rng.uniform(3.1, 4.7), rng.uniform(3.3, 5.1)
        pattern = np.sin(2.0 * math.pi * (xx / period_x + yy / period_y)) * np.sin(
            2.0 * math.pi * xx / (period_x * 1.07)
        )
        inside = mask > 0
        shaded = base.astype(np.float32)
        shaded[inside] += pattern[inside][:, None] * moire_amplitude
        base = np.clip(shaded, 0, 255).astype(np.uint8)

    rotation_degrees = 0.0
    if variant_index == 4:
        rotation_degrees = rng.choice((-1.0, 1.0)) * rng.uniform(8.0, 25.0)
        center = (canvas_w * 0.5, canvas_h * 0.5)
        rotation = cv2.getRotationMatrix2D(center, rotation_degrees, 1.0)
        cosine, sine = abs(rotation[0, 0]), abs(rotation[0, 1])
        rotated_w = int(canvas_h * sine + canvas_w * cosine)
        rotated_h = int(canvas_h * cosine + canvas_w * sine)
        rotation[0, 2] += rotated_w * 0.5 - center[0]
        rotation[1, 2] += rotated_h * 0.5 - center[1]
        base = cv2.warpAffine(
            base,
            rotation,
            (rotated_w, rotated_h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REFLECT,
        )
        corners = (np.hstack([corners, np.ones((4, 1), np.float32)]) @ rotation.T).astype(np.float32)
        canvas_w, canvas_h = rotated_w, rotated_h

    realistic = variant_index >= 3
    blur_sigma = 0.9 if realistic else (0.75, 1.15, 1.55)[variant_index]
    blurred = cv2.GaussianBlur(base, (0, 0), blur_sigma)
    noise_sigma = 0.0
    color_gains = (1.0, 1.0, 1.0)
    if realistic:
        # Sensor effects happen after the lens blur.
        noise_rng = np.random.default_rng(seed * 7919 + variant_index)
        noise_sigma = rng.uniform(2.5, 5.0)
        color_gains = tuple(rng.uniform(0.9, 1.1) for _ in range(3))
        yy, xx = np.mgrid[0:canvas_h, 0:canvas_w].astype(np.float32)
        radius = ((xx - canvas_w * 0.5) / (canvas_w * 0.5)) ** 2 + ((yy - canvas_h * 0.5) / (canvas_h * 0.5)) ** 2
        vignette = 1.0 - 0.22 * radius
        exposed = blurred.astype(np.float32) * np.asarray(color_gains, np.float32) * vignette[:, :, None]
        exposed += noise_rng.normal(0.0, noise_sigma, exposed.shape).astype(np.float32)
        blurred = np.clip(exposed, 0, 255).astype(np.uint8)
    jpeg_quality = 86 if realistic else (92, 84, 78)[variant_index]
    ok, encoded = cv2.imencode(
        ".jpg",
        cv2.cvtColor(blurred, cv2.COLOR_RGB2BGR),
        [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
    )
    if not ok:
        raise RuntimeError("OpenCV could not encode a synthetic camera image")
    payload = encoded.tobytes()
    return (
        payload,
        [{"x": round(float(x), 3), "y": round(float(y), 3)} for x, y in corners],
        {
            "blur_sigma": blur_sigma,
            "jpeg_quality": jpeg_quality,
            "banding_amplitude": round(float(banding_amplitude), 4),
            "glare_fraction": round(glare_fraction, 6),
            "noise_sigma": round(float(noise_sigma), 4),
            "color_gains": [round(float(value), 4) for value in color_gains],
            "rotation_degrees": round(float(rotation_degrees), 4),
            "moire_amplitude": round(float(moire_amplitude), 4),
            "canvas_width": canvas_w,
            "canvas_height": canvas_h,
        },
    )


def build_corpus(
    *,
    source_corpus: Path,
    output: Path,
    limit: int,
    variants: int,
    seed: int,
    overwrite: bool,
    geometry: str | None = None,
    target_type: str | None = None,
    min_terminals: int = 0,
    ids: list[str] | None = None,
    renderer: str = "variants",
    scenes_per_source: int = 4,
    difficulty_mix: dict[str, float] | None = None,
    split_fractions: dict[str, float] | None = None,
) -> dict[str, Any]:
    difficulty_mix = difficulty_mix or dict(DEFAULT_DIFFICULTY_MIX)
    split_fractions = split_fractions or dict(DEFAULT_SPLITS)
    source_manifest_path = source_corpus / "manifest.json"
    if not source_manifest_path.is_file():
        raise SystemExit(f"Screenshot corpus manifest not found: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_entries = [entry for entry in source_manifest.get("entries", []) if isinstance(entry, dict)]
    if geometry:
        source_entries = [entry for entry in source_entries if str(entry.get("geometry")) == geometry]
    if target_type:
        source_entries = [
            entry
            for entry in source_entries
            if str((entry.get("generation_data") or {}).get("target_type")) == target_type
        ]
    if min_terminals > 0:
        # Levels with ten or more flows introduce the game's white and gray
        # terminals, which exercise the neutral-color and glare-mask paths.
        source_entries = [
            entry for entry in source_entries if int(entry.get("terminal_count") or 0) >= min_terminals
        ]
    if ids:
        wanted = set(ids)
        source_entries = [entry for entry in source_entries if str(entry.get("id")) in wanted]
    if not source_entries:
        raise SystemExit("Screenshot corpus has no entries")
    if not 1 <= limit <= len(source_entries):
        raise SystemExit(f"--limit must be between 1 and {len(source_entries)}")
    if renderer == "variants" and not 1 <= variants <= 6:
        raise SystemExit("--variants must be between 1 and 6")
    if renderer == "scene" and not 1 <= scenes_per_source <= 50:
        raise SystemExit("--scenes-per-source must be between 1 and 50")

    photos = output / "photos"
    if output.exists() and not overwrite and (output / "manifest.json").exists():
        raise SystemExit(f"Camera corpus already exists: {output}; pass --overwrite to rebuild")
    photos.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for source_index, source_entry in enumerate(source_entries[:limit]):
        relative_source = str(source_entry.get("image") or "")
        source_path = source_corpus / relative_source
        if not source_path.is_file():
            continue
        with Image.open(source_path) as opened:
            screenshot = opened.convert("RGB")
        if renderer == "scene":
            if min(screenshot.size) < 400:
                continue  # icons and fragments, not phone screenshots
            entries.extend(
                _scene_entries(
                    screenshot,
                    source_entry,
                    relative_source=relative_source,
                    photos=photos,
                    seed=seed,
                    source_index=source_index,
                    scenes=scenes_per_source,
                    difficulty_mix=difficulty_mix,
                    split_fractions=split_fractions,
                )
            )
            continue
        for variant_index in range(variants):
            payload, corners, degradation = _camera_variant(
                screenshot,
                seed=seed + source_index,
                variant_index=variant_index,
            )
            digest = _sha256(payload)
            filename = f"{str(source_entry.get('id') or source_index)}-v{variant_index}-{digest[:12]}.jpg"
            (photos / filename).write_bytes(payload)
            entries.append(
                {
                    "id": digest[:16],
                    "sha256": digest,
                    "image": f"photos/{filename}",
                    "source_id": source_entry.get("id"),
                    "source_sha256": source_entry.get("sha256"),
                    "source_image": relative_source,
                    "source_generation_data": source_entry.get("generation_data", {}),
                    "geometry": source_entry.get("geometry"),
                    "variant_index": variant_index,
                    "expected_pairs": source_entry.get("expected_pairs"),
                    "screen_corners": corners,
                    "degradation": degradation,
                }
            )
    manifest = {
        "schema_version": 1,
        "created_at": time.time(),
        "source_corpus": str(source_corpus),
        "seed": seed,
        "renderer": renderer if renderer == "variants" else synthetic_camera.RENDERER_VERSION,
        "variants_per_source": variants if renderer == "variants" else None,
        "scenes_per_source": scenes_per_source if renderer == "scene" else None,
        "difficulty_mix": difficulty_mix if renderer == "scene" else None,
        "split_fractions": split_fractions if renderer == "scene" else None,
        "devices": synthetic_camera.device_profiles() if renderer == "scene" else None,
        "entries": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-corpus", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--variants", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260803)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--geometry")
    parser.add_argument("--target-type")
    parser.add_argument("--min-terminals", type=int, default=0)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument(
        "--renderer",
        choices=("variants", "scene"),
        default="variants",
        help="variants: fixed degradation variants 0-5; scene: sampled devices, environments, and cameras",
    )
    parser.add_argument("--scenes-per-source", type=int, default=4)
    parser.add_argument(
        "--difficulty-mix",
        default="easy=0.4,medium=0.4,hard=0.2",
        help="Scene difficulty weights, e.g. easy=0.4,medium=0.4,hard=0.2",
    )
    parser.add_argument("--splits", default="train=0.7,val=0.15,test=0.15")
    parser.add_argument("--preview", type=Path, help="Write a labeled contact sheet PNG of the first photos")
    args = parser.parse_args()

    def weights(text: str) -> dict[str, float]:
        pairs = [item.split("=", 1) for item in text.split(",") if item.strip()]
        return {name.strip(): float(value) for name, value in pairs}

    difficulty_mix = weights(args.difficulty_mix)
    unknown = set(difficulty_mix) - set(synthetic_camera.DIFFICULTIES)
    if unknown:
        raise SystemExit(f"Unknown difficulty: {', '.join(sorted(unknown))}")
    manifest = build_corpus(
        source_corpus=args.source_corpus.resolve(),
        output=args.output.resolve(),
        limit=args.limit,
        variants=args.variants,
        seed=args.seed,
        overwrite=args.overwrite,
        geometry=args.geometry,
        target_type=args.target_type,
        min_terminals=args.min_terminals,
        ids=args.ids,
        renderer=args.renderer,
        scenes_per_source=args.scenes_per_source,
        difficulty_mix=difficulty_mix,
        split_fractions=weights(args.splits),
    )
    summary: dict[str, Any] = {"entries": len(manifest["entries"]), "output": str(args.output)}
    if args.renderer == "scene":
        from collections import Counter

        entries = manifest["entries"]
        summary.update(
            splits=dict(Counter(entry["split"] for entry in entries)),
            difficulties=dict(Counter(entry["difficulty"] for entry in entries)),
            in_envelope=sum(bool(entry["in_envelope"]) for entry in entries),
            devices=len({entry["device"] for entry in entries}),
            environments=len({entry["environment"] for entry in entries}),
        )
    if args.preview:
        summary["preview"] = str(write_preview(manifest, args.output.resolve(), args.preview.resolve()))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
