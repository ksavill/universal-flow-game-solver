"""Build a deduplicated, diverse local screenshot regression corpus."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Iterable

try:
    from scripts.replay_image_imports import _generation_data
except ModuleNotFoundError:  # Running as `python scripts/build_reference_corpus.py`.
    from replay_image_imports import _generation_data


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "data" / "image_imports"
DEFAULT_CORPUS = ROOT / "reference_screenshot_corpus"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _timestamp(record: dict[str, Any]) -> float:
    return float(record.get("updated_at", record.get("created_at", 0)) or 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_detection(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    detection = result.get("detection") if isinstance(result.get("detection"), dict) else {}
    return detection


def _record_quality(record: dict[str, Any]) -> tuple[int, int, int, int, float]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    detection = _record_detection(record)
    completeness = (
        detection.get("terminal_completeness")
        if isinstance(detection.get("terminal_completeness"), dict)
        else {}
    )
    solve = record.get("solve") if isinstance(record.get("solve"), dict) else {}
    return (
        int(bool(result)),
        int(record.get("status") == "processed"),
        int(completeness.get("status") == "verified"),
        int(solve.get("status") == "solved"),
        _timestamp(record),
    )


def _candidate(record_path: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    image_path = record_path.parent / str(record.get("image_file") or "source.png")
    if not image_path.is_file():
        return None
    digest = _sha256(image_path)
    detection = _record_detection(record)
    level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
    grid = record.get("grid") if isinstance(record.get("grid"), dict) else detection.get("grid")
    if not isinstance(grid, dict):
        grid = {}
    solve = record.get("solve") if isinstance(record.get("solve"), dict) else {}
    processing = record.get("processing") if isinstance(record.get("processing"), dict) else {}
    expected_pairs = processing.get("expected_flow_count")
    if expected_pairs is None:
        completeness = detection.get("terminal_completeness")
        if isinstance(completeness, dict):
            expected_pairs = completeness.get("expected_pairs")
    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        suffix = ".bin"
    return {
        "sha256": digest,
        "image_path": image_path,
        "suffix": suffix,
        "bytes": image_path.stat().st_size,
        "record_path": record_path,
        "record": record,
        "source_import_id": str(record.get("id") or record_path.parent.name),
        "original_name": str(record.get("original_name") or image_path.name),
        "content_type": str(record.get("content_type") or "application/octet-stream"),
        "geometry": str(record.get("geometry") or level_type.get("geometry") or "unknown"),
        "modifiers": sorted(str(item) for item in (level_type.get("modifiers") or [])),
        "grid": {
            "rows": grid.get("rows"),
            "cols": grid.get("cols"),
        },
        "terminal_count": int(record.get("terminal_count") or 0),
        "expected_pairs": int(expected_pairs) if expected_pairs is not None else None,
        "archived_status": str(record.get("status") or "unknown"),
        "archived_solve_status": str(solve.get("status")) if solve.get("status") else None,
        "updated_at": _timestamp(record),
        "quality": _record_quality(record),
    }


def discover_unique_candidates(archive: Path) -> list[dict[str, Any]]:
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record_path in archive.glob("*/record.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        candidate = _candidate(record_path, record)
        if candidate is not None:
            by_hash[candidate["sha256"]].append(candidate)

    representatives: list[dict[str, Any]] = []
    for digest, candidates in by_hash.items():
        representative = max(candidates, key=lambda item: item["quality"])
        representative["source_import_ids"] = sorted(
            {str(item["source_import_id"]) for item in candidates}
        )
        representative["duplicate_record_count"] = len(candidates)
        representative["sha256"] = digest
        representatives.append(representative)
    return representatives


def _stratum(candidate: dict[str, Any]) -> tuple[Any, ...]:
    grid = candidate.get("grid") if isinstance(candidate.get("grid"), dict) else {}
    return (
        candidate.get("geometry") or "unknown",
        tuple(candidate.get("modifiers") or []),
        grid.get("cols"),
        grid.get("rows"),
        candidate.get("archived_solve_status") or "unattempted",
    )


def select_diverse_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Round-robin strata so common square boards cannot crowd out variants."""

    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        buckets[_stratum(candidate)].append(candidate)
    for values in buckets.values():
        values.sort(
            key=lambda item: (item["quality"], item["sha256"]),
            reverse=True,
        )

    selected: list[dict[str, Any]] = []
    strata = sorted(buckets, key=lambda key: (len(buckets[key]), repr(key)))
    while strata and len(selected) < limit:
        remaining: list[tuple[Any, ...]] = []
        for key in strata:
            values = buckets[key]
            if values and len(selected) < limit:
                selected.append(values.pop(0))
            if values:
                remaining.append(key)
        strata = remaining
    return selected


def _coverage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    geometries = Counter(str(entry.get("geometry") or "unknown") for entry in entries)
    modifiers = Counter(
        str(modifier)
        for entry in entries
        for modifier in entry.get("modifiers", [])
    )
    # Written without relying on tuple JSON keys.
    grid_sizes = {
        (
            (entry.get("grid") or {}).get("cols"),
            (entry.get("grid") or {}).get("rows"),
        )
        for entry in entries
        if (entry.get("grid") or {}).get("cols") and (entry.get("grid") or {}).get("rows")
    }
    return {
        "geometries": dict(sorted(geometries.items())),
        "modifiers": dict(sorted(modifiers.items())),
        "grid_size_count": len(grid_sizes),
        "solved_baseline_sources": sum(
            entry.get("archived_solve_status") == "solved" for entry in entries
        ),
    }


def build_corpus(
    *,
    archive: Path,
    corpus: Path,
    limit: int,
    prune: bool = False,
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    screenshots = corpus / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    manifest_path = corpus / "manifest.json"
    existing_entries: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing_entries = {
                str(entry.get("sha256")): entry
                for entry in existing.get("entries", [])
                if isinstance(entry, dict) and entry.get("sha256")
            }
        except Exception:
            existing_entries = {}

    discovered = discover_unique_candidates(archive)
    selected = select_diverse_candidates(discovered, limit=limit)
    entries: list[dict[str, Any]] = []
    selected_files: set[str] = set()
    copied = 0
    for candidate in selected:
        digest = str(candidate["sha256"])
        filename = f"{digest}{candidate['suffix']}"
        relative_image = f"screenshots/{filename}"
        destination = screenshots / filename
        if not destination.is_file() or destination.stat().st_size != candidate["bytes"]:
            shutil.copy2(candidate["image_path"], destination)
            copied += 1
        selected_files.add(filename)
        record = candidate["record"]
        previous = existing_entries.get(digest, {})
        entry = {
            "id": digest[:16],
            "sha256": digest,
            "image": relative_image,
            "bytes": candidate["bytes"],
            "original_name": candidate["original_name"],
            "content_type": candidate["content_type"],
            "source_import_id": candidate["source_import_id"],
            "source_import_ids": candidate["source_import_ids"],
            "duplicate_record_count": candidate["duplicate_record_count"],
            "updated_at": candidate["updated_at"],
            "geometry": candidate["geometry"],
            "modifiers": candidate["modifiers"],
            "grid": candidate["grid"],
            "terminal_count": candidate["terminal_count"],
            "expected_pairs": candidate["expected_pairs"],
            "archived_status": candidate["archived_status"],
            "archived_solve_status": candidate["archived_solve_status"],
            "generation_data": _generation_data(
                record,
                expected_flow_count=candidate["expected_pairs"],
            ),
            "baselines": previous.get("baselines", {}),
        }
        entries.append(entry)

    pruned = 0
    if prune:
        for path in screenshots.iterdir():
            if path.is_file() and path.name not in selected_files:
                path.unlink()
                pruned += 1

    entries.sort(
        key=lambda entry: (
            str(entry.get("geometry") or ""),
            (entry.get("grid") or {}).get("cols") or 0,
            (entry.get("grid") or {}).get("rows") or 0,
            entry["sha256"],
        )
    )
    manifest = {
        "schema_version": 1,
        "created_at": time.time(),
        "capacity": 500,
        "selection_limit": limit,
        "source_archive": str(archive.resolve()),
        "discovered_unique_sources": len(discovered),
        "selected": len(entries),
        "total_bytes": sum(int(entry["bytes"]) for entry in entries),
        "coverage": _coverage(entries),
        "entries": entries,
    }
    corpus.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(manifest_path)
    manifest["copied"] = copied
    manifest["pruned"] = pruned
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove screenshot files no longer selected by the manifest",
    )
    args = parser.parse_args()
    archive = args.archive if args.archive.is_absolute() else ROOT / args.archive
    corpus = args.corpus if args.corpus.is_absolute() else ROOT / args.corpus
    manifest = build_corpus(
        archive=archive,
        corpus=corpus,
        limit=args.limit,
        prune=args.prune,
    )
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in (
                    "capacity",
                    "discovered_unique_sources",
                    "selected",
                    "total_bytes",
                    "coverage",
                    "copied",
                    "pruned",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
