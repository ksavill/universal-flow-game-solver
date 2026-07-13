"""Regression harness that re-solves every known-good puzzle in the repository.

The harness walks the curated puzzle library and (optionally) the
archived screenshot-import results, solves each puzzle with the exact solver,
and independently verifies the returned paths with
``flow_solver.solver.validation.validate_solution``.  Results are compared
against a committed golden baseline so solver or parser regressions surface as
a nonzero exit instead of silently corrupting future solves.

Typical usage from the repository root::

    python scripts/validate_puzzles.py --write-baseline   # first run / accept changes
    python scripts/validate_puzzles.py                    # CI / pre-release check
    python scripts/validate_puzzles.py --include-imports  # also archived screenshots

Only stable facts go into the baseline (parses, solvable, solution validates).
Path lengths are intentionally excluded: puzzles with multiple solutions may
legally produce different paths between runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from flow_solver.puzzle import Puzzle  # noqa: E402
from flow_solver.solver import solve_puzzle  # noqa: E402
from flow_solver.solver.validation import validate_solution  # noqa: E402

DEFAULT_BASELINE = REPOSITORY_ROOT / "tests" / "golden" / "puzzle_baseline.json"
PUZZLE_SUFFIXES = {".flow", ".json"}


@dataclass
class PuzzleOutcome:
    key: str
    parses: bool
    solvable: bool
    solution_valid: bool
    nodes: Optional[int] = None
    colors: Optional[int] = None
    solve_ms: Optional[float] = None
    error: Optional[str] = None

    def signature(self) -> Dict[str, Any]:
        """The stable subset compared against the baseline."""
        return {
            "parses": self.parses,
            "solvable": self.solvable,
            "solution_valid": self.solution_valid,
            "nodes": self.nodes,
            "colors": self.colors,
        }


def discover_library_puzzles() -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for label, base in (("user", REPOSITORY_ROOT / "puzzles"),):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(base)
            # puzzles/templates holds crop templates, not puzzle definitions.
            if relative.parts and relative.parts[0] == "templates":
                continue
            if path.suffix.lower() in PUZZLE_SUFFIXES and path.is_file():
                out[f"{label}/{relative.as_posix()}"] = path
    return out


def discover_import_puzzles() -> Dict[str, str]:
    """Generated puzzle texts from archived screenshot imports that processed."""
    out: Dict[str, str] = {}
    imports_dir = REPOSITORY_ROOT / "data" / "image_imports"
    if not imports_dir.is_dir():
        return out
    for record_path in sorted(imports_dir.glob("*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = record.get("result")
        if record.get("status") != "processed" or not isinstance(result, dict):
            continue
        name = str(result.get("name") or "import")
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            out[f"imports/{record_path.parent.name}/{name}"] = text
    return out


def evaluate(key: str, *, path: Optional[Path] = None, text: Optional[str] = None,
             timeout_ms: int) -> PuzzleOutcome:
    try:
        if path is not None:
            puzzle = Puzzle.from_file(path)
        else:
            assert text is not None
            name_hint = key.rsplit("/", 1)[-1]
            if text.lstrip().startswith("{"):
                puzzle = Puzzle.from_json(text)
            else:
                puzzle = Puzzle.from_flow_text(text, source_name=name_hint)
    except Exception as exc:
        return PuzzleOutcome(key=key, parses=False, solvable=False, solution_valid=False,
                             error=f"parse: {exc}")

    nodes = len(puzzle.graph.nodes)
    colors = len(puzzle.terminals)
    started = time.perf_counter()
    try:
        result = solve_puzzle(puzzle, solver="z3", timeout_ms=timeout_ms)
    except Exception as exc:
        return PuzzleOutcome(key=key, parses=True, solvable=False, solution_valid=False,
                             nodes=nodes, colors=colors, error=f"solve: {exc}")
    solve_ms = (time.perf_counter() - started) * 1000.0

    report = validate_solution(puzzle, result)
    return PuzzleOutcome(
        key=key,
        parses=True,
        solvable=True,
        solution_valid=report.valid,
        nodes=nodes,
        colors=colors,
        solve_ms=round(solve_ms, 2),
        error=None if report.valid else "validate: " + "; ".join(report.errors[:3]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true",
                        help="Record current outcomes as the new golden baseline")
    parser.add_argument("--include-imports", action="store_true",
                        help="Also validate generated puzzles from archived screenshot imports")
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--jobs", type=int, default=4,
                        help="Parallel solves (z3 releases the GIL, so threads scale)")
    args = parser.parse_args()

    jobs: List[Dict[str, Any]] = []
    for key, path in discover_library_puzzles().items():
        jobs.append({"key": key, "path": path})
    if args.include_imports:
        for key, text in discover_import_puzzles().items():
            jobs.append({"key": key, "text": text})

    if not jobs:
        print("No puzzles found.")
        return 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        outcomes = list(pool.map(
            lambda job: evaluate(job["key"], path=job.get("path"), text=job.get("text"),
                                 timeout_ms=args.timeout_ms),
            jobs,
        ))
    elapsed = time.perf_counter() - started

    outcomes.sort(key=lambda outcome: outcome.key)
    current = {outcome.key: outcome.signature() for outcome in outcomes}

    ok = sum(1 for o in outcomes if o.solution_valid)
    unsolvable = [o for o in outcomes if o.parses and not o.solvable]
    invalid = [o for o in outcomes if o.solvable and not o.solution_valid]
    unparsed = [o for o in outcomes if not o.parses]
    print(f"Validated {len(outcomes)} puzzles in {elapsed:.1f}s "
          f"({args.jobs} workers): {ok} solved+verified, "
          f"{len(unsolvable)} unsolvable, {len(unparsed)} parse failures, "
          f"{len(invalid)} INVALID solutions.")
    for outcome in (*unparsed, *unsolvable, *invalid):
        print(f"  - {outcome.key}: {outcome.error}")

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"Baseline written: {args.baseline} ({len(current)} entries)")
        # Invalid solutions are never an acceptable baseline.
        return 1 if invalid else 0

    if not args.baseline.exists():
        print(f"No baseline at {args.baseline}. Run with --write-baseline first.")
        return 1

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    regressions: List[str] = []
    for key, signature in baseline.items():
        if key.startswith("imports/") and not args.include_imports:
            continue
        if key not in current:
            regressions.append(f"missing puzzle (was in baseline): {key}")
            continue
        if current[key] != signature:
            regressions.append(f"changed outcome: {key} baseline={signature} now={current[key]}")
    new_keys = [key for key in current if key not in baseline]
    if new_keys:
        print(f"{len(new_keys)} new puzzles not in baseline (informational): "
              + ", ".join(new_keys[:5]) + ("…" if len(new_keys) > 5 else ""))

    if regressions:
        print(f"\n{len(regressions)} REGRESSION(S):")
        for line in regressions:
            print(f"  ! {line}")
        return 2
    if invalid:
        print("\nSolver returned invalid solutions (see above).")
        return 3
    print("Baseline check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
