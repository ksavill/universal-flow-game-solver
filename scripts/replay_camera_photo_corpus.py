"""Replay synthetic/real camera photos against their source screenshot semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "reference_camera_corpus"


_VOLATILE_KEYS = {
    "meta",
    "metadata",
    "extensions",
    "display",
    "source_image",
    "image_size",
    "crop",
    "crop_size",
    "generated",
    "source_mode",
    # Camera/JPEG sampling can shift endpoint RGB by a few values without
    # changing the detected puzzle.  Pair identity and endpoint cells are the
    # semantic contract exercised by this replay gate.
    "color",
}
_DISPLAY_KEYS = {"x", "y", "sx", "sy", "cx", "cy", "polygon", "contour"}


def _semantic_value(value: Any, *, inside_nodes: bool = False) -> Any:
    if isinstance(value, list):
        return [_semantic_value(item, inside_nodes=inside_nodes) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in sorted(value.items()):
        if key in _VOLATILE_KEYS or (inside_nodes and key in _DISPLAY_KEYS):
            continue
        if key == "terminals" and isinstance(item, dict):
            # Pair letters are presentation labels.  The same endpoint pairs
            # may be lettered differently when an earlier color slot was
            # intentionally omitted from an incomplete reviewed import.
            normalized_terminals = [
                _semantic_value(terminal, inside_nodes=inside_nodes)
                for terminal in item.values()
            ]
            out[key] = sorted(
                normalized_terminals,
                key=lambda terminal: json.dumps(terminal, sort_keys=True),
            )
            continue
        normalized = _semantic_value(item, inside_nodes=inside_nodes or key == "nodes")
        if key == "endpoints" and isinstance(normalized, list):
            normalized = sorted(normalized, key=lambda entry: json.dumps(entry, sort_keys=True))
        out[key] = normalized
    return out


def _semantic_signature(text: str) -> str:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    else:
        normalized = json.dumps(_semantic_value(document), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _region_graph(text: str) -> tuple[dict[str, tuple[float, float]], set[frozenset[str]], list[tuple[str, ...]]] | None:
    try:
        document = json.loads(text)
        channels = document["topology"]["channels"]
        centers = {key: value["data"]["pixel_center"] for key, value in channels.items()}
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not centers:
        return None
    xs = [float(center[0]) for center in centers.values()]
    ys = [float(center[1]) for center in centers.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    normalized = {
        key: ((float(center[0]) - x0) / max(1.0, x1 - x0), (float(center[1]) - y0) / max(1.0, y1 - y0))
        for key, center in centers.items()
    }
    adjacency = {
        frozenset((item["a"]["channel"], item["b"]["channel"]))
        for item in document["topology"].get("adjacencies", [])
    }
    pairs = [tuple(value.get("endpoints", [])) for value in document.get("terminals", {}).values()]
    return normalized, adjacency, pairs


def _region_graphs_equivalent(source_text: str, camera_text: str) -> bool:
    """Same region puzzle up to renamed cells.

    Region ids follow pixel raster order and cell centers depend on the
    resolution, so photo imports of region boards never share a byte-level
    signature with the screenshot; match cells by normalized position instead.
    """

    source, camera = _region_graph(source_text), _region_graph(camera_text)
    if source is None or camera is None:
        return False
    source_cells, source_adjacency, source_pairs = source
    camera_cells, camera_adjacency, camera_pairs = camera
    if len(source_cells) != len(camera_cells):
        return False
    mapping = {
        key: min(
            source_cells,
            key=lambda other: (source_cells[other][0] - point[0]) ** 2 + (source_cells[other][1] - point[1]) ** 2,
        )
        for key, point in camera_cells.items()
    }
    if len(set(mapping.values())) != len(mapping):
        return False
    if {frozenset(mapping[node] for node in edge) for edge in camera_adjacency} != source_adjacency:
        return False
    mapped_pairs = sorted(tuple(sorted(mapping.get(node, node) for node in pair)) for pair in camera_pairs)
    return mapped_pairs == sorted(tuple(sorted(pair)) for pair in source_pairs)


def _source_request_data(entry: dict[str, Any]) -> dict[str, str]:
    raw = entry.get("source_generation_data")
    data = {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}
    data.pop("replace_import_id", None)
    data["source_mode"] = "screenshot"
    data["output_schema_version"] = "2"
    return data


def _camera_request_data(entry: dict[str, Any], *, auto_corners: bool = False) -> dict[str, str]:
    data = _source_request_data(entry)
    for key in ("crop_x", "crop_y", "crop_width", "crop_height", "perspective"):
        data.pop(key, None)
    data["source_mode"] = "camera"
    corners = entry.get("screen_corners")
    if not auto_corners and isinstance(corners, list) and len(corners) == 4:
        # Ground-truth corners isolate board/terminal recovery from display
        # localization; --auto-corners exercises the automatic detector too.
        data["photo_corners_json"] = json.dumps(corners, separators=(",", ":"))
    return data


def _post_generate(client: Any, path: Path, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    payload = path.read_bytes()
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    response = client.post(
        "/image/generate",
        files={"file": (path.name, payload, mime)},
        data=data,
    )
    try:
        body = response.json()
    except Exception:
        body = {"detail": response.text[:2000]}
    return response.status_code, body


def _generation_summary(body: dict[str, Any]) -> dict[str, Any]:
    detection = body.get("detection") if isinstance(body.get("detection"), dict) else {}
    terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    return {
        "target": detection.get("target_type_used"),
        "geometry": (
            detection.get("level_type", {}).get("geometry")
            if isinstance(detection.get("level_type"), dict)
            else None
        ),
        "grid": detection.get("grid"),
        "terminal_count": len(terminals),
        "terminals": terminals,
    }


def _review_inputs(body: dict[str, Any]) -> dict[str, Any]:
    detection = body.get("detection") if isinstance(body.get("detection"), dict) else {}
    photo = detection.get("photo") if isinstance(detection.get("photo"), dict) else {}
    keep = (
        "selected",
        "selection_margin",
        "consensus_votes",
        "selected_corroboration",
        "quality",
        "prepared_board_size",
    )
    terminal_info = detection.get("terminal_info") if isinstance(detection.get("terminal_info"), dict) else {}
    return {
        "photo": {key: photo.get(key) for key in keep if key in photo},
        # Terminal and level evidence used by the camera review rules.
        "detection": {
            "terminals": detection.get("terminals"),
            "terminal_info": {"clusters": terminal_info.get("clusters")},
            "level_type": {
                key: (detection.get("level_type") or {}).get(key) for key in ("geometry", "modifiers", "signals")
            } if isinstance(detection.get("level_type"), dict) else None,
            "graph_layout": detection.get("graph_layout_auto_selected"),
        },
        "terminal_completeness": detection.get("terminal_completeness"),
        "completeness_review": bool(
            isinstance(detection.get("terminal_completeness"), dict)
            and detection["terminal_completeness"].get("review_required")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--split", help="Replay only one split (real: tuning|holdout; scene: train|val|test)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--auto-corners",
        action="store_true",
        help="Detect the display automatically instead of sending the labeled screen corners",
    )
    args = parser.parse_args()

    corpus = args.corpus.resolve()
    manifest_path = corpus / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"Camera corpus manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [entry for entry in manifest.get("entries", []) if isinstance(entry, dict)]
    if args.split:
        entries = [entry for entry in entries if entry.get("split") == args.split]
    if args.limit is not None:
        entries = entries[: args.limit]
    source_corpus = Path(str(manifest.get("source_corpus") or ROOT / "reference_screenshot_corpus"))
    if not source_corpus.is_absolute():
        source_corpus = (ROOT / source_corpus).resolve()

    temporary = tempfile.TemporaryDirectory(prefix="flow-camera-replay-")
    replay_root = Path(temporary.name)
    os.environ["FLOW_IMAGE_IMPORTS_DIR"] = str(replay_root / "imports")
    os.environ["FLOW_IMAGE_JOBS_DIR"] = str(replay_root / "jobs")
    os.environ["FLOW_IMAGE_IMPORT_INDEX_PATH"] = str(replay_root / "index.sqlite3")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from backend.app import app

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    # Several photos share one source screenshot; generate each source once.
    source_cache: dict[str, tuple[int, dict[str, Any]]] = {}
    with TestClient(app) as client:
        for entry in entries:
            photo_path = corpus / str(entry.get("image") or "")
            if entry.get("expected_signature"):
                # Real photos carry a reviewed label instead of a source
                # screenshot to regenerate.
                source_status, source = 200, entry.get("expected_summary") or {}
                source_signature = str(entry["expected_signature"])
            else:
                source_path = source_corpus / str(entry.get("source_image") or "")
                source_data = _source_request_data(entry)
                cache_key = json.dumps([str(source_path), source_data], sort_keys=True)
                if cache_key not in source_cache:
                    source_cache[cache_key] = _post_generate(client, source_path, source_data)
                source_status, source = source_cache[cache_key]
                source_signature = (
                    _semantic_signature(str(source.get("text") or "")) if source_status == 200 else None
                )
            camera_status, camera = _post_generate(
                client,
                photo_path,
                _camera_request_data(entry, auto_corners=args.auto_corners),
            )
            camera_signature = (
                _semantic_signature(str(camera.get("text") or "")) if camera_status == 200 else None
            )
            result = {
                "id": entry.get("id"),
                "source_id": entry.get("source_id"),
                "source_status": source_status,
                "camera_status": camera_status,
                "source_signature": source_signature,
                "camera_signature": camera_signature,
                "match": bool(source_signature and source_signature == camera_signature)
                or bool(
                    source_status == 200
                    and camera_status == 200
                    and not entry.get("expected_signature")
                    and _region_graphs_equivalent(str(source.get("text") or ""), str(camera.get("text") or ""))
                ),
                "variant_index": entry.get("variant_index"),
                "difficulty": entry.get("difficulty"),
                "in_envelope": entry.get("in_envelope"),
                "device": entry.get("device"),
                "environment": entry.get("environment"),
                "geometry": entry.get("geometry"),
                "degradation": entry.get("degradation"),
                "camera_error": camera.get("detail") if camera_status != 200 else None,
                "split": entry.get("split"),
                "source_summary": source if entry.get("expected_signature") else _generation_summary(source),
                "camera_summary": _generation_summary(camera),
                # Inputs to the review rules, so calibrate_camera_review.py can
                # re-decide review offline for other thresholds.
                "review_inputs": _review_inputs(camera),
                "photo_review": (
                    camera.get("detection", {}).get("photo_review")
                    if isinstance(camera.get("detection"), dict)
                    else None
                ),
            }
            # The archive flag also covers terminal-completeness review, which
            # is what a user actually sees as "needs review".
            result["review_flagged"] = bool(camera.get("review_flagged"))
            result["flag_reason"] = camera.get("flag_reason")
            result["review_required"] = bool(
                result["review_flagged"]
                or (isinstance(result["photo_review"], dict) and result["photo_review"].get("required"))
            )
            result["incorrect_high_confidence"] = bool(
                camera_status == 200 and not result["match"] and not result["review_required"]
            )
            results.append(result)
            if not args.quiet:
                print(json.dumps(result, separators=(",", ":")), flush=True)

    def grouped(key: str) -> dict[str, dict[str, int]]:
        groups: dict[str, dict[str, int]] = {}
        for result in results:
            summary = groups.setdefault(
                str(result.get(key)),
                {"selected": 0, "matched": 0, "review_required": 0, "incorrect_high_confidence": 0},
            )
            summary["selected"] += 1
            summary["matched"] += int(bool(result["match"]))
            summary["review_required"] += int(bool(result["review_required"]))
            summary["incorrect_high_confidence"] += int(bool(result["incorrect_high_confidence"]))
        return groups

    by_variant = grouped("variant_index")
    # Scene-rendered corpora also report by what went into each photo.
    scene_groups = {
        f"by_{key}": grouped(key)
        for key in ("difficulty", "in_envelope", "device", "environment", "split", "geometry")
        if any(result.get(key) is not None for result in results)
    }

    report = {
        "schema_version": 2,
        "created_at": time.time(),
        "corners": "automatic" if args.auto_corners else "labeled",
        "selected": len(results),
        "matched": sum(result["match"] for result in results),
        "camera_generated": sum(result["camera_status"] == 200 for result in results),
        "review_required": sum(result["review_required"] for result in results),
        "incorrect_high_confidence": sum(result["incorrect_high_confidence"] for result in results),
        "by_variant": by_variant,
        **scene_groups,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "mismatches": [result["id"] for result in results if not result["match"]],
        "results": results,
    }
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.cleanup()
    return 0 if report["matched"] == report["selected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
