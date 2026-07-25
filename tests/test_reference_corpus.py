from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_reference_corpus import build_corpus
from scripts.replay_reference_corpus import _canonical_puzzle_sha256, _signature


def _write_import(
    archive: Path,
    import_id: str,
    image_bytes: bytes,
    *,
    geometry: str,
    modifiers: list[str] | None = None,
    rows: int = 5,
    cols: int = 5,
    solved: bool = False,
    updated_at: float = 1.0,
) -> None:
    directory = archive / import_id
    directory.mkdir(parents=True)
    (directory / "source.png").write_bytes(image_bytes)
    record = {
        "id": import_id,
        "image_file": "source.png",
        "original_name": f"{import_id}.png",
        "content_type": "image/png",
        "status": "processed",
        "updated_at": updated_at,
        "geometry": geometry,
        "grid": {"rows": rows, "cols": cols},
        "terminal_count": 4,
        "processing": {
            "target_type": "grid",
            "auto_classify": True,
            "auto_terminals": True,
            "expected_flow_count": 2,
        },
        "result": {
            "detection": {
                "level_type": {
                    "id": geometry,
                    "geometry": geometry,
                    "modifiers": modifiers or [],
                },
                "terminal_completeness": {
                    "status": "verified",
                    "expected_pairs": 2,
                },
            }
        },
    }
    if solved:
        record["solve"] = {"status": "solved"}
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")


def test_build_corpus_deduplicates_sources_and_preserves_baselines(tmp_path: Path) -> None:
    archive = tmp_path / "image_imports"
    corpus = tmp_path / "reference_screenshot_corpus"
    shared = b"same screenshot pixels"
    _write_import(archive, "square-old", shared, geometry="square", solved=True)
    _write_import(
        archive,
        "square-duplicate",
        shared,
        geometry="square",
        updated_at=2.0,
    )
    _write_import(archive, "hex", b"hex pixels", geometry="hex", rows=7, cols=6)
    _write_import(
        archive,
        "warps",
        b"warp pixels",
        geometry="graph",
        modifiers=["warps"],
        rows=8,
        cols=8,
    )

    first = build_corpus(archive=archive, corpus=corpus, limit=500)

    assert first["discovered_unique_sources"] == 3
    assert first["selected"] == 3
    assert first["coverage"]["geometries"] == {"graph": 1, "hex": 1, "square": 1}
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    square_entry = next(entry for entry in manifest["entries"] if entry["geometry"] == "square")
    assert square_entry["source_import_id"] == "square-old"
    assert square_entry["duplicate_record_count"] == 2
    assert square_entry["source_import_ids"] == ["square-duplicate", "square-old"]
    image_path = corpus / square_entry["image"]
    assert image_path.read_bytes() == shared
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == square_entry["sha256"]

    square_entry["baselines"]["recorded"] = {"generation_status": 200}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    second = build_corpus(archive=archive, corpus=corpus, limit=500)
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
    rebuilt_square = next(entry for entry in rebuilt["entries"] if entry["geometry"] == "square")

    assert second["copied"] == 0
    assert rebuilt_square["baselines"]["recorded"] == {"generation_status": 200}


def test_replay_signature_hashes_generated_puzzle_and_excludes_timing() -> None:
    puzzle_text = '{"schema_version":2,"format":"flow-solver-puzzle"}'
    raw = {
        "generation_status": 200,
        "generation_seconds": 9.9,
        "puzzle_sha256": _canonical_puzzle_sha256(puzzle_text),
        "solve_status": 400,
        "solve_seconds": 8.8,
        "solve_error": "Puzzle is UNSAT",
        "geometry": "square",
        "modifiers": ["walls", "bridges"],
        "target": "grid",
        "grid": {"rows": 5, "cols": 6, "mode": "detected", "confidence": 0.5},
        "terminal_count": 8,
        "terminal_completeness": {
            "status": "verified",
            "expected_pairs": 4,
            "detected_pairs": 4,
            "detected_endpoints": 8,
            "pairing_complete": True,
            "review_required": False,
            "diagnostics": ["not stable"],
        },
    }

    assert _signature(raw) == {
        "generation_status": 200,
        "puzzle_sha256": _canonical_puzzle_sha256(
            '{ "format": "flow-solver-puzzle", "schema_version": 2 }'
        ),
        "solve_status": 400,
        "geometry": "square",
        "modifiers": ["bridges", "walls"],
        "target": "grid",
        "grid": {"rows": 5, "cols": 6, "mode": "detected"},
        "terminal_count": 8,
        "terminal_completeness": {
            "status": "verified",
            "expected_pairs": 4,
            "detected_pairs": 4,
            "detected_endpoints": 8,
            "pairing_complete": True,
            "review_required": False,
        },
        "solve_error": "Puzzle is UNSAT",
    }


def test_failed_generation_signature_retains_failure_reason() -> None:
    assert _signature(
        {
            "generation_status": 400,
            "generation_error": "Grid size auto-detection failed.",
            "generation_seconds": 12.3,
        }
    ) == {
        "generation_status": 400,
        "generation_error": "Grid size auto-detection failed.",
    }
