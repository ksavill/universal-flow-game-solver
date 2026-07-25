from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


NAME_RE = re.compile(r"IMG_(\d+)", re.IGNORECASE)
ROOT = Path(__file__).resolve().parents[1]


def _timestamp(record: dict[str, Any]) -> float:
    return float(record.get("updated_at", record.get("created_at", 0)))


def _discover(archive: Path) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for record_path in archive.glob("*/record.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(record, dict):
            records.append((record_path, record))
    records.sort(key=lambda item: _timestamp(item[1]), reverse=True)
    return records


def _select(
    records: list[tuple[Path, dict[str, Any]]],
    *,
    latest: int | None,
    start: int | None,
    end: int | None,
    ids: list[str] | None,
    failures_only: bool,
) -> list[tuple[Path, dict[str, Any]]]:
    if ids is not None:
        by_id = {
            str(record.get("id", record_path.parent.name)): (record_path, record)
            for record_path, record in records
        }
        missing = [value for value in ids if value not in by_id]
        if missing:
            raise SystemExit(f"Archive record id(s) not found: {', '.join(missing)}")
        selected = [by_id[value] for value in ids if value in by_id]
    elif latest is not None:
        selected = records[:latest]
    else:
        assert start is not None and end is not None
        newest_by_number: dict[int, tuple[Path, dict[str, Any]]] = {}
        for record_path, record in records:
            match = NAME_RE.search(str(record.get("original_name", "")))
            if not match:
                continue
            number = int(match.group(1))
            if start <= number <= end and number not in newest_by_number:
                newest_by_number[number] = (record_path, record)
        selected = [newest_by_number[number] for number in sorted(newest_by_number)]
    if failures_only:
        selected = [
            item
            for item in selected
            if item[1].get("status") == "failed"
            or (isinstance(item[1].get("solve"), dict) and item[1]["solve"].get("status") == "failed")
        ]
    return selected


def _generation_data(
    record: dict[str, Any],
    *,
    expected_flow_count: int | None = None,
    mode: str = "recorded",
) -> dict[str, str]:
    processing = record.get("processing") if isinstance(record.get("processing"), dict) else {}
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    detection = result.get("detection") if isinstance(result.get("detection"), dict) else {}
    recorded_expected = processing.get("expected_flow_count")
    if expected_flow_count is None and recorded_expected is not None:
        expected_flow_count = int(recorded_expected)
    if mode == "auto":
        data = {
            "target_type": "auto",
            "auto_classify": "true",
            "auto_terminals": "true",
            "output_schema_version": "2",
        }
        if expected_flow_count is not None:
            data["expected_flow_count"] = str(expected_flow_count)
        return data

    data: dict[str, str] = {
        "target_type": str(processing.get("target_type") or "auto"),
        "auto_classify": str(bool(processing.get("auto_classify", True))).lower(),
        "auto_terminals": str(bool(processing.get("auto_terminals", True))).lower(),
        "output_schema_version": "2",
        "graph_layout": str(processing.get("graph_layout") or "grid"),
        "graph_nodes": str(processing.get("graph_nodes") or 10),
    }
    if expected_flow_count is not None:
        data["expected_flow_count"] = str(expected_flow_count)
    level_type = detection.get("level_type")
    if isinstance(level_type, dict):
        # The browser may classify first and then call /image/generate with
        # auto_classify=false plus this complete decision. Without replaying it,
        # bridge/warp modifiers disappear even though all scalar options match.
        data["level_type_json"] = json.dumps(level_type, separators=(",", ":"))
    generated_text = result.get("text")
    if isinstance(generated_text, str):
        try:
            generated_document = json.loads(generated_text)
            extension = generated_document.get("extensions", {}).get("flow-solver/import", {})
            corrections = extension.get("manual_edge_corrections")
            if isinstance(corrections, dict):
                data["edge_overrides_json"] = json.dumps(corrections, separators=(",", ":"))
        except (AttributeError, json.JSONDecodeError):
            pass
    for key in (
        "grid_width",
        "grid_height",
        "threshold",
        "line_threshold",
        "sat_threshold",
        "brightness_min",
        "brightness_max",
        "margin_ratio",
        "cluster_threshold",
        "bg_threshold",
    ):
        if processing.get(key) is not None:
            data[key] = str(processing[key])
    for key in ("invert", "perspective"):
        if processing.get(key) is not None:
            data[key] = str(bool(processing[key])).lower()
    crop = processing.get("crop") if isinstance(processing.get("crop"), dict) else None
    if crop and all(key in crop for key in ("x", "y", "width", "height")):
        data.update(
            crop_x=str(crop["x"]),
            crop_y=str(crop["y"]),
            crop_width=str(crop["width"]),
            crop_height=str(crop["height"]),
        )
    return data


def _replay_one(
    client: Any,
    record_path: Path,
    record: dict[str, Any],
    timeout_ms: int,
    expectation: dict[str, Any] | None = None,
    mode: str = "recorded",
    update_archive: bool = False,
) -> dict[str, Any]:
    image_path = record_path.parent / str(record.get("image_file", "source.png"))
    entry: dict[str, Any] = {
        "id": record.get("id", record_path.parent.name),
        "name": record.get("original_name"),
        "original_status": record.get("status"),
        "original_solve_status": (
            record["solve"].get("status") if isinstance(record.get("solve"), dict) else None
        ),
        "mode": mode,
        "updated_at": _timestamp(record),
    }
    expected_pairs = (
        int(expectation["expected_pairs"])
        if isinstance(expectation, dict) and expectation.get("expected_pairs") is not None
        else None
    )
    if expected_pairs is not None:
        entry["expected_pairs"] = expected_pairs
    if not image_path.is_file():
        entry.update(generation_status=0, generation_error="Archived source image is missing")
        return entry
    started = time.perf_counter()
    generation_data = _generation_data(
        record,
        expected_flow_count=expected_pairs,
        mode=mode,
    )
    if update_archive:
        generation_data["replace_import_id"] = str(
            record.get("id", record_path.parent.name)
        )
        entry["updated_archive"] = True
    generated = client.post(
        "/image/generate",
        files={
            "file": (
                str(record.get("original_name", image_path.name)),
                image_path.read_bytes(),
                str(record.get("content_type", "image/png")),
            )
        },
        data=generation_data,
    )
    entry["generation_status"] = generated.status_code
    entry["generation_seconds"] = round(time.perf_counter() - started, 3)
    if generated.status_code != 200:
        entry["generation_error"] = generated.json().get("detail", generated.text[:500])
        return entry

    payload = generated.json()
    entry["replay_import_id"] = payload.get("import_id")
    generated_document: dict[str, Any] = {}
    try:
        parsed_document = json.loads(str(payload.get("text") or ""))
        if isinstance(parsed_document, dict):
            generated_document = parsed_document
    except json.JSONDecodeError:
        pass
    detection = payload.get("detection", {})
    modifier_info = (
        detection.get("modifier_info")
        if isinstance(detection.get("modifier_info"), dict)
        else {}
    )
    region_info = (
        modifier_info.get("regions")
        if isinstance(modifier_info.get("regions"), dict)
        else {}
    )
    entry.update(
        geometry=detection.get("level_type", {}).get("geometry"),
        modifiers=detection.get("level_type", {}).get("modifiers", []),
        target=detection.get("target_type_used"),
        grid=detection.get("grid"),
        terminals=len(detection.get("terminals", [])),
        terminal_completeness=detection.get("terminal_completeness"),
        region_seams=len(region_info.get("repaired_seams", [])),
        region_seam_inference=region_info.get("seam_inference"),
        regular_hex_lattice=bool(region_info.get("regular_hex_lattice")),
    )
    topology = (
        generated_document.get("topology")
        if isinstance(generated_document.get("topology"), dict)
        else {}
    )
    adjacencies = (
        topology.get("adjacencies")
        if isinstance(topology.get("adjacencies"), list)
        else []
    )
    entry["blocked_adjacencies"] = sum(
        isinstance(adjacency, dict) and adjacency.get("state") == "blocked"
        for adjacency in adjacencies
    )
    topology_data = (
        topology.get("data")
        if isinstance(topology.get("data"), dict)
        else {}
    )
    crossovers = (
        topology_data.get("crossovers")
        if isinstance(topology_data.get("crossovers"), list)
        else []
    )
    entry["crossover_count"] = len(crossovers)
    if crossovers:
        neighbors: dict[str, set[str]] = {}
        for adjacency in adjacencies:
            if not isinstance(adjacency, dict) or adjacency.get("state") != "open":
                continue
            a = adjacency.get("a") if isinstance(adjacency.get("a"), dict) else {}
            b = adjacency.get("b") if isinstance(adjacency.get("b"), dict) else {}
            left = a.get("channel")
            right = b.get("channel")
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            neighbors.setdefault(left, set()).add(right)
            neighbors.setdefault(right, set()).add(left)
        channels = (
            topology.get("channels")
            if isinstance(topology.get("channels"), dict)
            else {}
        )
        isolation_issues: list[str] = []
        for crossover in crossovers:
            if not isinstance(crossover, dict):
                isolation_issues.append("invalid crossover record")
                continue
            crossover_id = str(crossover.get("id") or "unknown")
            path = crossover.get("under_path")
            under = crossover.get("under_channel")
            surface = crossover.get("surface_channel")
            if (
                not isinstance(path, list)
                or len(path) != 3
                or not all(isinstance(node_id, str) for node_id in path)
                or not isinstance(under, str)
                or path[1] != under
            ):
                isolation_issues.append(f"{crossover_id}: invalid straight under path")
                continue
            expected_neighbors = {str(path[0]), str(path[2])}
            if neighbors.get(under, set()) != expected_neighbors:
                isolation_issues.append(
                    f"{crossover_id}: under neighbors "
                    f"{sorted(neighbors.get(under, set()))}, expected {sorted(expected_neighbors)}"
                )
            if not isinstance(surface, str):
                isolation_issues.append(f"{crossover_id}: missing surface channel")
                continue
            under_spec = channels.get(under) if isinstance(channels.get(under), dict) else {}
            surface_spec = (
                channels.get(surface)
                if isinstance(channels.get(surface), dict)
                else {}
            )
            if under_spec.get("cell") != surface_spec.get("cell"):
                isolation_issues.append(f"{crossover_id}: channels do not share a physical cell")
            if surface in neighbors.get(under, set()):
                isolation_issues.append(f"{crossover_id}: under connects to surface")
        entry["crossover_isolation_valid"] = not isolation_issues
        entry["crossover_isolation_issues"] = isolation_issues
    if expected_pairs is not None:
        entry["terminal_count_match"] = len(detection.get("terminals", [])) == expected_pairs * 2
    solve_started = time.perf_counter()
    solved = client.post(
        "/solve",
        json={
            "name": payload["name"],
            "text": payload["text"],
            "timeout_ms": timeout_ms,
            "import_id": payload.get("import_id"),
        },
    )
    entry["solve_status"] = solved.status_code
    entry["solve_seconds"] = round(time.perf_counter() - solve_started, 3)
    if solved.status_code == 200:
        solved_payload = solved.json()
        entry["solver"] = solved_payload.get("stats", {}).get("solver")
        if crossovers:
            node_color = (
                solved_payload.get("node_color")
                if isinstance(solved_payload.get("node_color"), dict)
                else {}
            )
            used_edges = {
                tuple(sorted((str(edge[0]), str(edge[1]))))
                for color_edges in solved_payload.get("path_edges", {}).values()
                if isinstance(color_edges, list)
                for edge in color_edges
                if isinstance(edge, list) and len(edge) == 2
            }
            solution_issues: list[str] = []
            for crossover in crossovers:
                if not isinstance(crossover, dict):
                    continue
                path = crossover.get("under_path")
                under = crossover.get("under_channel")
                if (
                    not isinstance(path, list)
                    or len(path) != 3
                    or not isinstance(under, str)
                ):
                    continue
                under_color = node_color.get(under)
                if under_color is None:
                    continue
                if any(node_color.get(str(node_id)) != under_color for node_id in path):
                    solution_issues.append(
                        f"{crossover.get('id')}: under path changes color"
                    )
                expected_edges = {
                    tuple(sorted((str(path[0]), under))),
                    tuple(sorted((under, str(path[2])))),
                }
                if not expected_edges.issubset(used_edges):
                    solution_issues.append(
                        f"{crossover.get('id')}: solution does not pass straight through under channel"
                    )
            entry["crossover_solution_valid"] = not solution_issues
            entry["crossover_solution_issues"] = solution_issues
    else:
        entry["solve_error"] = solved.json().get("detail", solved.text[:500])
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay archived screenshot imports through the current pipeline")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--latest", type=int, help="Replay the latest N upload records")
    selection.add_argument("--start", type=int, help="First IMG number (requires --end)")
    selection.add_argument("--ids", nargs="+", help="Replay exact archive record ids")
    parser.add_argument("--end", type=int, help="Last IMG number")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument(
        "--mode",
        choices=("recorded", "auto"),
        default="recorded",
        help="Replay archived settings or rerun the current automatic pipeline",
    )
    parser.add_argument(
        "--update-archive",
        action="store_true",
        help="Replace each selected archive record in place instead of writing isolated replay records",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--expectations",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "historical_image_expectations.json",
        help="Optional record-id keyed expected flow counts",
    )
    parser.add_argument("--quiet", action="store_true", help="Print only the final summary")
    args = parser.parse_args()
    if args.latest is not None and args.latest < 1:
        raise SystemExit("--latest must be positive")
    if args.start is not None and args.end is None:
        raise SystemExit("--start requires --end")
    if args.jobs < 1:
        raise SystemExit("--jobs must be positive")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    expectations: dict[str, dict[str, Any]] = {}
    if args.expectations and args.expectations.is_file():
        raw_expectations = json.loads(args.expectations.read_text(encoding="utf-8"))
        if not isinstance(raw_expectations, dict):
            raise SystemExit("--expectations must contain a JSON object keyed by archive id")
        expectations = {
            str(key): value
            for key, value in raw_expectations.items()
            if isinstance(value, dict)
        }
    archive = ROOT / "data" / "image_imports"
    selected = _select(
        _discover(archive),
        latest=args.latest,
        start=args.start,
        end=args.end,
        ids=args.ids,
        failures_only=args.failures_only,
    )

    os.environ["FLOW_IMAGE_IMPORTS_DIR"] = (
        str(archive)
        if args.update_archive
        else tempfile.mkdtemp(prefix="flow-replay-imports-")
    )
    os.environ["FLOW_IMAGE_JOBS_DIR"] = tempfile.mkdtemp(prefix="flow-replay-jobs-")
    from fastapi.testclient import TestClient
    from backend.app import app

    results: list[dict[str, Any]] = []
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(
                _replay_one,
                client,
                record_path,
                record,
                args.timeout_ms,
                expectations.get(str(record.get("id", record_path.parent.name))),
                args.mode,
                args.update_archive,
            ): record_path
            for record_path, record in selected
        }
        for future in as_completed(futures):
            try:
                entry = future.result()
            except Exception as exc:
                entry = {
                    "id": futures[future].parent.name,
                    "generation_status": 0,
                    "generation_error": f"Replay worker failed: {type(exc).__name__}: {exc}",
                }
            results.append(entry)
            if not args.quiet:
                print(json.dumps(entry, separators=(",", ":")), flush=True)

    results.sort(key=lambda item: float(item.get("updated_at", 0)), reverse=True)
    summary = {
        "selected": len(selected),
        "generated": sum(item.get("generation_status") == 200 for item in results),
        "solved": sum(item.get("solve_status") == 200 for item in results),
        "generation_failures": sum(item.get("generation_status") != 200 for item in results),
        "solve_failures": sum(
            item.get("generation_status") == 200 and item.get("solve_status") != 200
            for item in results
        ),
        "previously_solved": sum(item.get("original_solve_status") == "solved" for item in results),
        "previously_solved_still_solved": sum(
            item.get("original_solve_status") == "solved" and item.get("solve_status") == 200
            for item in results
        ),
        "regression_ids": [
            item.get("id")
            for item in results
            if item.get("original_solve_status") == "solved" and item.get("solve_status") != 200
        ],
        "expectation_failure_ids": [
            item.get("id")
            for item in results
            if item.get("expected_pairs") is not None
            and (
                item.get("terminal_count_match") is not True
                or item.get("solve_status") != 200
            )
        ],
        "failed_ids": [
            item.get("id")
            for item in results
            if item.get("generation_status") != 200 or item.get("solve_status") != 200
        ],
        "results": results,
    }
    omitted = {"results"}
    if args.quiet:
        omitted.add("failed_ids")
    print(json.dumps({key: value for key, value in summary.items() if key not in omitted}, indent=2), flush=True)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["solved"] == len(results) and not summary["expectation_failure_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
