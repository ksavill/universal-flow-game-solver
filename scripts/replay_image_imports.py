from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


NAME_RE = re.compile(r"IMG_(\d+)", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay archived screenshot imports through the current pipeline")
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--timeout-ms", type=int, default=10_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    archive = root / "data" / "image_imports"
    latest: dict[int, tuple[float, Path, dict[str, Any]]] = {}
    for record_path in archive.glob("*/record.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        match = NAME_RE.search(str(record.get("original_name", "")))
        if not match:
            continue
        number = int(match.group(1))
        if not args.start <= number <= args.end:
            continue
        created = float(record.get("created_at", 0.0))
        if number not in latest or created > latest[number][0]:
            latest[number] = (created, record_path, record)

    os.environ["FLOW_IMAGE_IMPORTS_DIR"] = tempfile.mkdtemp(prefix="flow-replay-")
    from fastapi.testclient import TestClient
    from backend.app import app

    client = TestClient(app)
    results: list[dict[str, Any]] = []
    for number in sorted(latest):
        _created, record_path, record = latest[number]
        processing = record.get("processing") if isinstance(record.get("processing"), dict) else {}
        crop = processing.get("crop") if isinstance(processing.get("crop"), dict) else None
        image_path = record_path.parent / str(record.get("image_file", "source.png"))
        data: dict[str, str] = {
            "target_type": "auto",
            "auto_classify": "true",
            "auto_terminals": "true",
            "output_schema_version": "2",
            "graph_layout": "grid",
        }
        if crop:
            data.update(
                {
                    "crop_x": str(crop["x"]),
                    "crop_y": str(crop["y"]),
                    "crop_width": str(crop["width"]),
                    "crop_height": str(crop["height"]),
                }
            )
        started = time.perf_counter()
        generated = client.post(
            "/image/generate",
            files={
                "file": (
                    str(record.get("original_name", image_path.name)),
                    image_path.read_bytes(),
                    str(record.get("content_type", "image/png")),
                )
            },
            data=data,
        )
        entry: dict[str, Any] = {
            "number": number,
            "name": record.get("original_name"),
            "generation_status": generated.status_code,
            "generation_seconds": round(time.perf_counter() - started, 3),
        }
        if generated.status_code == 200:
            payload = generated.json()
            detection = payload.get("detection", {})
            entry.update(
                {
                    "geometry": detection.get("level_type", {}).get("geometry"),
                    "modifiers": detection.get("level_type", {}).get("modifiers", []),
                    "target": detection.get("target_type_used"),
                    "grid": detection.get("grid"),
                    "terminals": len(detection.get("terminals", [])),
                }
            )
            solve_started = time.perf_counter()
            solved = client.post(
                "/solve",
                json={
                    "name": payload["name"],
                    "text": payload["text"],
                    "timeout_ms": args.timeout_ms,
                },
            )
            entry["solve_status"] = solved.status_code
            entry["solve_seconds"] = round(time.perf_counter() - solve_started, 3)
            if solved.status_code == 200:
                entry["solver"] = solved.json().get("stats", {}).get("solver")
            else:
                entry["solve_error"] = solved.json().get("detail", solved.text[:300])
        else:
            entry["generation_error"] = generated.json().get("detail", generated.text[:300])
        results.append(entry)
        print(json.dumps(entry, separators=(",", ":")), flush=True)

    summary = {
        "requested": args.end - args.start + 1,
        "found": len(results),
        "generated": sum(item["generation_status"] == 200 for item in results),
        "solved": sum(item.get("solve_status") == 200 for item in results),
        "failed": [item["number"] for item in results if item.get("solve_status") != 200],
        "results": results,
    }
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=2), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["solved"] == summary["found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
