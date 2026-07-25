"""Replay the persistent reference screenshot corpus through generation and solve."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "reference_screenshot_corpus"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(corpus: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = corpus / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"Corpus manifest not found: {manifest_path}. "
            "Run scripts/build_reference_corpus.py first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise SystemExit(f"Invalid corpus manifest: {manifest_path}")
    return manifest_path, manifest


def _generation_data(entry: dict[str, Any], mode: str) -> dict[str, str]:
    if mode == "recorded":
        raw = entry.get("generation_data")
        return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}
    data = {
        "target_type": "auto",
        "auto_classify": "true",
        "auto_terminals": "true",
        "output_schema_version": "2",
    }
    expected_pairs = entry.get("expected_pairs")
    if expected_pairs is not None:
        data["expected_flow_count"] = str(expected_pairs)
    return data


def _normalized_grid(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        key: raw.get(key)
        for key in ("rows", "cols", "mode")
        if raw.get(key) is not None
    }


def _canonical_puzzle_sha256(text: str) -> str:
    """Hash generated puzzle meaning rather than JSON whitespace/key order."""

    try:
        normalized = json.dumps(
            json.loads(text),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, json.JSONDecodeError):
        normalized = text
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _signature(result: dict[str, Any]) -> dict[str, Any]:
    signature: dict[str, Any] = {
        "generation_status": int(result.get("generation_status", 0)),
    }
    if signature["generation_status"] != 200:
        signature["generation_error"] = result.get("generation_error")
        return signature
    signature.update(
        puzzle_sha256=result.get("puzzle_sha256"),
        solve_status=int(result.get("solve_status", 0)),
        geometry=result.get("geometry"),
        modifiers=sorted(str(value) for value in result.get("modifiers", [])),
        target=result.get("target"),
        grid=_normalized_grid(result.get("grid")),
        terminal_count=int(result.get("terminal_count", 0)),
    )
    completeness = result.get("terminal_completeness")
    if isinstance(completeness, dict):
        signature["terminal_completeness"] = {
            key: completeness.get(key)
            for key in (
                "status",
                "expected_pairs",
                "detected_pairs",
                "detected_endpoints",
                "pairing_complete",
                "review_required",
            )
        }
    else:
        signature["terminal_completeness"] = None
    if signature["solve_status"] != 200:
        signature["solve_error"] = result.get("solve_error")
    return signature


def _replay_one(
    client: Any,
    *,
    corpus: Path,
    entry: dict[str, Any],
    mode: str,
    timeout_ms: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": entry.get("id"),
        "sha256": entry.get("sha256"),
        "original_name": entry.get("original_name"),
        "mode": mode,
    }
    relative = Path(str(entry.get("image") or ""))
    image_path = corpus / relative
    if not image_path.is_file():
        result.update(generation_status=0, worker_error="source image is missing")
        result["signature"] = _signature(result)
        return result
    actual_hash = _sha256(image_path)
    if actual_hash != entry.get("sha256"):
        result.update(
            generation_status=0,
            worker_error=f"SHA-256 mismatch: expected {entry.get('sha256')}, got {actual_hash}",
        )
        result["signature"] = _signature(result)
        return result

    started = time.perf_counter()
    generated = client.post(
        "/image/generate",
        files={
            "file": (
                str(entry.get("original_name") or image_path.name),
                image_path.read_bytes(),
                str(entry.get("content_type") or "application/octet-stream"),
            )
        },
        data=_generation_data(entry, mode),
    )
    result["generation_status"] = generated.status_code
    result["generation_seconds"] = round(time.perf_counter() - started, 3)
    if generated.status_code != 200:
        try:
            result["generation_error"] = generated.json().get("detail", generated.text[:1000])
        except Exception:
            result["generation_error"] = generated.text[:1000]
        result["signature"] = _signature(result)
        return result

    payload = generated.json()
    detection = payload.get("detection") if isinstance(payload.get("detection"), dict) else {}
    level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
    terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    result.update(
        puzzle_sha256=_canonical_puzzle_sha256(str(payload.get("text") or "")),
        geometry=level_type.get("geometry"),
        modifiers=level_type.get("modifiers", []),
        target=detection.get("target_type_used"),
        grid=detection.get("grid"),
        terminal_count=len(terminals),
        terminal_completeness=detection.get("terminal_completeness"),
    )

    solve_started = time.perf_counter()
    solved = client.post(
        "/solve",
        json={
            "name": payload.get("name") or "reference.json",
            "text": payload.get("text") or "",
            "timeout_ms": timeout_ms,
            "import_id": payload.get("import_id"),
        },
    )
    result["solve_status"] = solved.status_code
    result["solve_seconds"] = round(time.perf_counter() - solve_started, 3)
    if solved.status_code != 200:
        try:
            result["solve_error"] = solved.json().get("detail", solved.text[:1000])
        except Exception:
            result["solve_error"] = solved.text[:1000]
    else:
        result["solver"] = solved.json().get("stats", {}).get("solver")
    result["signature"] = _signature(result)
    return result


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--mode", choices=("recorded", "auto"), default="recorded")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--geometry")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--accept-baseline-update", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.write_baseline and not args.accept_baseline_update:
        parser.error("--write-baseline requires --accept-baseline-update")
    if args.accept_baseline_update and not args.write_baseline:
        parser.error("--accept-baseline-update requires --write-baseline")

    corpus = args.corpus if args.corpus.is_absolute() else ROOT / args.corpus
    manifest_path, manifest = _load_manifest(corpus)
    entries = [entry for entry in manifest["entries"] if isinstance(entry, dict)]
    if args.ids:
        requested = set(args.ids)
        entries = [
            entry
            for entry in entries
            if str(entry.get("id")) in requested or str(entry.get("sha256")) in requested
        ]
        found = {
            value
            for entry in entries
            for value in (str(entry.get("id")), str(entry.get("sha256")))
        }
        missing = sorted(value for value in requested if value not in found)
        if missing:
            raise SystemExit(f"Corpus id(s) not found: {', '.join(missing)}")
    if args.geometry:
        entries = [
            entry for entry in entries if str(entry.get("geometry")) == args.geometry
        ]
    if args.limit is not None:
        entries = entries[: args.limit]
    if not entries:
        raise SystemExit("No corpus entries selected")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    temporary = tempfile.TemporaryDirectory(prefix="flow-reference-replay-")
    replay_root = Path(temporary.name)
    imports_dir = replay_root / "image_imports"
    jobs_dir = replay_root / "image_jobs"
    imports_dir.mkdir()
    jobs_dir.mkdir()
    os.environ["FLOW_IMAGE_IMPORTS_DIR"] = str(imports_dir)
    os.environ["FLOW_IMAGE_JOBS_DIR"] = str(jobs_dir)
    os.environ["FLOW_IMAGE_IMPORT_INDEX_PATH"] = str(replay_root / "index.sqlite3")
    from fastapi.testclient import TestClient
    from backend.app import app

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(
                _replay_one,
                client,
                corpus=corpus,
                entry=entry,
                mode=args.mode,
                timeout_ms=args.timeout_ms,
            ): entry
            for entry in entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "id": entry.get("id"),
                    "sha256": entry.get("sha256"),
                    "original_name": entry.get("original_name"),
                    "mode": args.mode,
                    "generation_status": 0,
                    "worker_error": f"{type(exc).__name__}: {exc}",
                }
                result["signature"] = _signature(result)
            results.append(result)
            if not args.quiet:
                print(json.dumps(result, separators=(",", ":")), flush=True)

    results.sort(key=lambda result: str(result.get("id")))
    entries_by_id = {str(entry.get("id")): entry for entry in manifest["entries"]}
    regressions: list[dict[str, Any]] = []
    unbaselined: list[str] = []
    worker_failures = [
        str(result.get("id"))
        for result in results
        if result.get("worker_error")
    ]
    for result in results:
        entry = entries_by_id[str(result.get("id"))]
        baselines = entry.get("baselines") if isinstance(entry.get("baselines"), dict) else {}
        baseline = baselines.get(args.mode)
        if baseline is None:
            unbaselined.append(str(result.get("id")))
        elif baseline != result["signature"]:
            regressions.append(
                {
                    "id": result.get("id"),
                    "expected": baseline,
                    "actual": result["signature"],
                }
            )

    if args.write_baseline:
        if worker_failures:
            print("Refusing to baseline worker/integrity failures.", file=sys.stderr)
        else:
            for result in results:
                entry = entries_by_id[str(result.get("id"))]
                baselines = entry.setdefault("baselines", {})
                baselines[args.mode] = result["signature"]
            manifest["baseline_updated_at"] = time.time()
            manifest["baseline_mode"] = args.mode
            _write_manifest(manifest_path, manifest)
            regressions = []
            unbaselined = []

    report = {
        "schema_version": 1,
        "created_at": time.time(),
        "mode": args.mode,
        "selected": len(results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "generated": sum(result.get("generation_status") == 200 for result in results),
        "solved": sum(result.get("solve_status") == 200 for result in results),
        "generation_failures": sum(result.get("generation_status") != 200 for result in results),
        "solve_failures": sum(
            result.get("generation_status") == 200 and result.get("solve_status") != 200
            for result in results
        ),
        "worker_failure_ids": worker_failures,
        "unbaselined_ids": unbaselined,
        "regressions": regressions,
        "results": results,
    }
    summary = {key: value for key, value in report.items() if key != "results"}
    print(json.dumps(summary, indent=2), flush=True)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        _write_report(output, report)
    temporary.cleanup()
    if worker_failures:
        return 3
    if regressions:
        return 2
    if unbaselined:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
