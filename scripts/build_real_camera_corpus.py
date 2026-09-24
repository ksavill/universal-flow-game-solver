"""Collect real camera-photo imports from the archive into a labeled corpus.

Only imports a person reviewed (the review flag was cleared, which records
``reviewed_at``) become labels by default; the archived result at that point is
the expected puzzle.  ``--include-unreviewed`` also adds solved imports that
were never flagged, marked ``label_source=solved-unreviewed`` so reports can
keep them out of accuracy claims.

Photos are split into ``tuning`` and ``holdout`` by camera device (a hashed
make/model recorded at upload), falling back to capture day when only one
device is present, so thresholds are never tuned on the photos that judge them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.replay_camera_photo_corpus import _semantic_signature  # noqa: E402
from scripts.replay_image_imports import _generation_data  # noqa: E402

DEFAULT_ARCHIVE = ROOT / "data" / "image_imports"
DEFAULT_OUTPUT = ROOT / "reference_camera_corpus" / "real"


def _detection(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    return result.get("detection") if isinstance(result.get("detection"), dict) else {}


def _label_source(record: dict[str, Any], *, include_unreviewed: bool) -> str | None:
    if record.get("reviewed_at") and not record.get("flagged"):
        return "human-reviewed"
    solve = record.get("solve") if isinstance(record.get("solve"), dict) else {}
    if include_unreviewed and not record.get("flagged") and solve.get("status") == "solved":
        return "solved-unreviewed"
    return None


def _fraction(value: str) -> float:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16) / float(0xFFFFFFFF)


def _assign_splits(entries: list[dict[str, Any]], holdout_fraction: float) -> None:
    devices = sorted({entry["device_id"] for entry in entries})
    key = "device_id" if len(devices) >= 2 else "session"
    groups = sorted({entry[key] for entry in entries})
    holdout = {group for group in groups if _fraction(group) < holdout_fraction}
    if len(groups) >= 2 and not holdout:
        holdout = {min(groups, key=_fraction)}
    if len(groups) >= 2 and holdout == set(groups):
        holdout.discard(max(groups, key=_fraction))
    for entry in entries:
        entry["split"] = "holdout" if entry[key] in holdout else "tuning"
        entry["split_group"] = key


def build(
    *,
    archive: Path,
    output: Path,
    include_unreviewed: bool,
    holdout_fraction: float,
    overwrite: bool,
) -> dict[str, Any]:
    if output.exists() and (output / "manifest.json").exists() and not overwrite:
        raise SystemExit(f"Real camera corpus already exists: {output}; pass --overwrite to rebuild")
    photos = output / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for record_path in sorted(archive.glob("*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        processing = record.get("processing") if isinstance(record.get("processing"), dict) else {}
        if processing.get("source_mode_effective") != "camera" or record.get("status") == "failed":
            continue
        label_source = _label_source(record, include_unreviewed=include_unreviewed)
        text = (record.get("result") or {}).get("text") if isinstance(record.get("result"), dict) else None
        if label_source is None or not isinstance(text, str):
            continue
        image_path = record_path.parent / str(record.get("image_file") or "")
        if not image_path.is_file():
            continue
        data = image_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{digest[:16]}{image_path.suffix.lower()}"
        shutil.copyfile(image_path, photos / filename)
        detection = _detection(record)
        input_info = detection.get("input") if isinstance(detection.get("input"), dict) else {}
        device_id = str(input_info.get("device_id") or "unknown-device")
        day = time.strftime("%Y-%m-%d", time.gmtime(float(record.get("created_at") or 0)))
        generation = _generation_data(record)
        generation.pop("photo_corners_json", None)
        generation.pop("source_mode", None)
        corners = processing.get("photo_corners")
        terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
        entries.append(
            {
                "id": digest[:16],
                "sha256": digest,
                "image": f"photos/{filename}",
                "source_import_id": record.get("id"),
                "label_source": label_source,
                "device_id": device_id,
                "session": f"{device_id}:{day}",
                "screen_corners": (
                    [{"x": float(point[0]), "y": float(point[1])} for point in corners]
                    if isinstance(corners, list) and len(corners) == 4
                    else None
                ),
                "source_generation_data": generation,
                "expected_signature": _semantic_signature(text),
                "expected_summary": {
                    "grid": detection.get("grid"),
                    "terminal_count": len(terminals),
                    "terminals": terminals,
                },
            }
        )
    _assign_splits(entries, holdout_fraction)
    manifest = {
        "schema_version": 1,
        "created_at": time.time(),
        "source": "archive",
        "archive": str(archive),
        "holdout_fraction": holdout_fraction,
        "entries": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-unreviewed", action="store_true")
    parser.add_argument("--holdout-fraction", type=float, default=0.3)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    manifest = build(
        archive=args.archive.resolve(),
        output=args.output.resolve(),
        include_unreviewed=args.include_unreviewed,
        holdout_fraction=args.holdout_fraction,
        overwrite=args.overwrite,
    )
    entries = manifest["entries"]
    print(
        json.dumps(
            {
                "entries": len(entries),
                "human_reviewed": sum(entry["label_source"] == "human-reviewed" for entry in entries),
                "holdout": sum(entry["split"] == "holdout" for entry in entries),
                "devices": len({entry["device_id"] for entry in entries}),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
