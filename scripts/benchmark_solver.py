"""Repeatable, dependency-free benchmark harness for the exact Z3 solver.

Run from the repository root, for example::

    python scripts/benchmark_solver.py
    python scripts/benchmark_solver.py puzzles/square/10x10/classic_level_142.flow --repeat 5

The harness measures solve time, not file parsing.  Puzzle validation is still
included in each solve because it is part of the solver's end-to-end deadline.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable


# Direct script execution puts ``scripts/`` rather than the repository root on
# sys.path.  Resolve the checkout root from this file so the command is equally
# usable in an activated environment and in CI.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from flow_solver.puzzle import Puzzle  # noqa: E402
from flow_solver.solver import (  # noqa: E402
    PuzzleUnsolvableError,
    PuzzleValidationError,
    SolveTimeoutError,
    SolverUnknownError,
    solve_with_z3,
)
from flow_solver.validation import validate_puzzle  # noqa: E402


DEFAULT_CORPUS = (
    "puzzles/square/5x5/classic_level_1.flow",
    "puzzles/square/8x8/classic_level_104.flow",
    "puzzles/square/10x10/classic_level_142.flow",
    "puzzles/square/15x15/classic_level_150.flow",
)
SUPPORTED_SUFFIXES = frozenset({".flow", ".json"})


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _expand_inputs(inputs: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = REPOSITORY_ROOT / candidate
        if candidate.is_dir():
            paths.extend(
                sorted(
                    (
                        path
                        for path in candidate.rglob("*")
                        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
                    ),
                    key=lambda path: path.as_posix(),
                )
            )
        else:
            paths.append(candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _timing_summary(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": min(values),
        "median_ms": statistics.median(values),
        "p95_ms": _percentile_nearest_rank(values, 0.95),
        "max_ms": max(values),
    }


def _error_status(exc: Exception) -> str:
    if isinstance(exc, PuzzleValidationError):
        return "invalid"
    if isinstance(exc, PuzzleUnsolvableError):
        return "unsat"
    if isinstance(exc, SolveTimeoutError):
        return "timeout"
    if isinstance(exc, SolverUnknownError):
        return "unknown"
    return "error"


def benchmark_puzzle(
    path: Path,
    *,
    repeat: int,
    warmup: int,
    timeout_ms: int | None,
    check_unique: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "puzzle": _display_path(path),
        "status": "pending",
        "repeat": repeat,
        "warmup": warmup,
        "timeout_ms": timeout_ms,
        "check_unique": check_unique,
    }
    if not path.is_file():
        record.update(status="missing", error="Puzzle file does not exist")
        return record

    try:
        parse_started = time.perf_counter()
        puzzle = Puzzle.from_file(path)
        record["parse_ms"] = (time.perf_counter() - parse_started) * 1000.0
        validation = validate_puzzle(puzzle)
        record["puzzle_stats"] = validation.stats
        if not validation.valid:
            record.update(
                status="invalid",
                error="; ".join(issue.message for issue in validation.errors[:4]),
                validation=validation.to_dict(),
            )
            return record

        for _ in range(warmup):
            solve_with_z3(
                puzzle,
                timeout_ms=timeout_ms,
                check_unique=check_unique,
            )

        wall_times: list[float] = []
        solver_times: list[float] = []
        last_stats: dict[str, Any] = {}
        unique: bool | None = None
        for _ in range(repeat):
            started = time.perf_counter()
            result = solve_with_z3(
                puzzle,
                timeout_ms=timeout_ms,
                check_unique=check_unique,
            )
            wall_times.append((time.perf_counter() - started) * 1000.0)
            last_stats = dict(result.stats)
            if isinstance(last_stats.get("total_ms"), (int, float)):
                solver_times.append(float(last_stats["total_ms"]))
            unique = result.unique

        record.update(
            status="solved",
            wall=_timing_summary(wall_times),
            solver=_timing_summary(solver_times or wall_times),
            solver_stats=last_stats,
            unique=unique,
        )
    except Exception as exc:  # Keep a multi-puzzle run useful after one failure.
        record.update(status=_error_status(exc), error=str(exc))
    return record


def _format_number(value: Any, digits: int = 1) -> str:
    return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "-"


def _print_table(records: list[dict[str, Any]]) -> None:
    headers = ("puzzle", "status", "nodes", "colors", "median ms", "p95 ms", "bool vars", "cuts")
    rows: list[tuple[str, ...]] = []
    for record in records:
        puzzle_stats = record.get("puzzle_stats", {})
        solver_stats = record.get("solver_stats", {})
        wall = record.get("wall", {})
        variable_count: int | str = "-"
        node_variables = solver_stats.get("node_color_vars")
        edge_variables = solver_stats.get("edge_color_vars")
        if isinstance(node_variables, int) and isinstance(edge_variables, int):
            variable_count = node_variables + edge_variables
        rows.append(
            (
                str(record["puzzle"]),
                str(record["status"]),
                str(puzzle_stats.get("nodes", "-")),
                str(puzzle_stats.get("colors", "-")),
                _format_number(wall.get("median_ms")),
                _format_number(wall.get("p95_ms")),
                str(variable_count),
                str(solver_stats.get("connectivity_cuts", "-")),
            )
        )

    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
    for record in records:
        if record.get("error"):
            print(f"{record['puzzle']}: {record['error']}", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the exact edge-variable Z3 Flow solver",
    )
    parser.add_argument(
        "puzzles",
        nargs="*",
        help="Puzzle files or directories (default: representative 5x5 through 15x15 corpus)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Measured solves per puzzle (default: 1)")
    parser.add_argument("--warmup", type=int, default=0, help="Unmeasured warm-up solves per puzzle")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30_000,
        help="Shared end-to-end deadline for each solve (default: 30000)",
    )
    parser.add_argument(
        "--unique",
        action="store_true",
        help="Also search for a second exact edge assignment",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write complete machine-readable results to this path",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Compare median time and model size with a stored benchmark JSON",
    )
    parser.add_argument(
        "--max-drift",
        type=float,
        default=2.0,
        help="Maximum allowed median/model-size multiplier versus --baseline (default: 2.0)",
    )
    return parser


def _model_size(record: dict[str, Any]) -> int | None:
    stats = record.get("solver_stats") if isinstance(record.get("solver_stats"), dict) else {}
    node_variables = stats.get("node_color_vars")
    edge_variables = stats.get("edge_color_vars")
    if isinstance(node_variables, int) and isinstance(edge_variables, int):
        return node_variables + edge_variables
    return None


def _baseline_regressions(
    records: list[dict[str, Any]],
    baseline_path: Path,
    *,
    max_drift: float,
) -> list[str]:
    if not baseline_path.is_absolute():
        baseline_path = REPOSITORY_ROOT / baseline_path
    if not baseline_path.is_file():
        return [f"Benchmark baseline does not exist: {_display_path(baseline_path)}"]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = {
        str(item.get("puzzle")): item
        for item in baseline.get("records", [])
        if isinstance(item, dict) and item.get("puzzle")
    }
    regressions: list[str] = []
    for record in records:
        puzzle = str(record.get("puzzle"))
        previous = expected.get(puzzle)
        if previous is None:
            regressions.append(f"{puzzle}: missing from benchmark baseline")
            continue
        if record.get("status") != previous.get("status"):
            regressions.append(
                f"{puzzle}: status changed from {previous.get('status')} to {record.get('status')}"
            )
            continue
        current_median = record.get("wall", {}).get("median_ms")
        baseline_median = previous.get("wall", {}).get("median_ms")
        if isinstance(current_median, (int, float)) and isinstance(baseline_median, (int, float)):
            if current_median > baseline_median * max_drift:
                regressions.append(
                    f"{puzzle}: median {current_median:.1f}ms exceeds "
                    f"{max_drift:.2f}x baseline {baseline_median:.1f}ms"
                )
        current_size = _model_size(record)
        baseline_size = _model_size(previous)
        if current_size is not None and baseline_size is not None and current_size > baseline_size * max_drift:
            regressions.append(
                f"{puzzle}: model size {current_size} exceeds "
                f"{max_drift:.2f}x baseline {baseline_size}"
            )
    return regressions


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    if args.warmup < 0:
        raise SystemExit("--warmup cannot be negative")
    if args.timeout_ms <= 0:
        raise SystemExit("--timeout-ms must be positive")
    if args.max_drift <= 1.0:
        raise SystemExit("--max-drift must be greater than 1")

    inputs = args.puzzles or list(DEFAULT_CORPUS)
    paths = _expand_inputs(inputs)
    if not paths:
        raise SystemExit("No .flow or .json puzzle files found")

    records = [
        benchmark_puzzle(
            path,
            repeat=args.repeat,
            warmup=args.warmup,
            timeout_ms=args.timeout_ms,
            check_unique=args.unique,
        )
        for path in paths
    ]
    _print_table(records)

    payload = {
        "benchmark": "flow-solver-z3-edge",
        "schema_version": 1,
        "python": sys.version.split()[0],
        "repeat": args.repeat,
        "warmup": args.warmup,
        "timeout_ms": args.timeout_ms,
        "check_unique": bool(args.unique),
        "records": records,
    }
    if args.json_out is not None:
        output = args.json_out
        if not output.is_absolute():
            output = REPOSITORY_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote JSON results: {_display_path(output)}")

    regressions: list[str] = []
    if args.baseline is not None:
        regressions = _baseline_regressions(records, args.baseline, max_drift=args.max_drift)
        if regressions:
            print(f"{len(regressions)} benchmark regression(s):", file=sys.stderr)
            for regression in regressions:
                print(f"  ! {regression}", file=sys.stderr)
        else:
            print("Benchmark baseline check passed.")

    if regressions:
        return 2
    return 0 if all(record["status"] == "solved" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
