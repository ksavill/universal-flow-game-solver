from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .migration import migrate_file
from .puzzle import Puzzle
from .solver import SOLVER_CHOICES, solve_puzzle
from .validation import validate_puzzle
from .viz import write_plotly_html


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_solver", description="Offline Flow/Numberlink solver + visualizer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_viz = sub.add_parser("visualize", help="Render the generated graph to an HTML file")
    p_viz.add_argument("puzzle", type=str, help="Path to .flow or .json puzzle file")
    p_viz.add_argument("--out", type=str, default="out/graph.html", help="Output HTML path")
    p_viz.add_argument("--3d", action="store_true", help="Use a 3D plot (helpful for bridges/layers)")

    p_solve = sub.add_parser("solve", help="Solve a puzzle and render the solution to an HTML file")
    p_solve.add_argument("puzzle", type=str, help="Path to .flow or .json puzzle file")
    p_solve.add_argument("--out", type=str, default="out/solution.html", help="Output HTML path")
    p_solve.add_argument("--3d", action="store_true", help="Use a 3D plot (helpful for bridges/layers)")
    p_solve.add_argument("--solver", choices=SOLVER_CHOICES, default="z3", help="Solver backend")
    p_solve.add_argument("--timeout-ms", type=int, default=30_000, help="Solver timeout in milliseconds")

    p_validate = sub.add_parser(
        "validate",
        help="Validate topology, terminals, coverage invariants, and optional solvability",
    )
    p_validate.add_argument("puzzle", type=str, help="Path to .flow or .json puzzle file")
    p_validate.add_argument(
        "--solve",
        action="store_true",
        help="Also prove that the puzzle has a solution",
    )
    p_validate.add_argument("--solver", choices=SOLVER_CHOICES, default="z3", help="Solver backend")
    p_validate.add_argument("--timeout-ms", type=int, default=30_000, help="Solver timeout in milliseconds")
    p_validate.add_argument("--json", action="store_true", help="Print a machine-readable report")

    p_migrate = sub.add_parser(
        "migrate",
        help="Write a legacy .flow or graph JSON puzzle as canonical schema-v2 JSON",
    )
    p_migrate.add_argument("puzzle", type=str, help="Source .flow or .json puzzle file")
    p_migrate.add_argument("--out", type=str, required=True, help="Destination .json file")
    p_migrate.add_argument(
        "--template",
        type=str,
        default=None,
        help="Optional topology template id override",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    puzzle_path = Path(args.puzzle)
    if args.cmd == "migrate":
        out = migrate_file(puzzle_path, args.out, template_id=args.template)
        print(f"Wrote schema-v2 puzzle: {out}")
        return 0

    puzzle = Puzzle.from_file(puzzle_path)

    if args.cmd == "validate":
        report = validate_puzzle(puzzle)
        payload = report.to_dict()
        if report.valid and args.solve:
            try:
                result = solve_puzzle(puzzle, solver=args.solver, timeout_ms=args.timeout_ms)
                payload["solvable"] = True
                payload["solution"] = {
                    "colors": len(result.paths),
                    "path_lengths": {color: len(path) for color, path in result.paths.items()},
                }
            except Exception as exc:
                payload["solvable"] = False
                payload["solve_error"] = str(exc)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            status = "valid" if report.valid else "invalid"
            print(
                f"{puzzle_path.name}: {status}; nodes={payload['stats']['nodes']}, "
                f"edges={payload['stats']['edges']}, colors={payload['stats']['colors']}"
            )
            for issue in report.issues:
                print(f"  {issue.severity}: {issue.code}: {issue.message}")
            if "solvable" in payload:
                print(f"  solvable: {payload['solvable']}")
                if payload.get("solve_error"):
                    print(f"  solve_error: {payload['solve_error']}")
        return 0 if report.valid and payload.get("solvable", True) else 1

    if args.cmd == "visualize":
        out = write_plotly_html(puzzle, out_path=args.out, title=f"Graph: {puzzle_path.name}", use_3d=bool(args.__dict__.get("3d")))
        print(f"Wrote graph visualization: {out}")
        return 0

    if args.cmd == "solve":
        res = solve_puzzle(puzzle, solver=args.solver, timeout_ms=args.timeout_ms)
        out = write_plotly_html(
            puzzle,
            out_path=args.out,
            node_color=res.node_color,
            path_edges=res.path_edges,
            title=f"Solution: {puzzle_path.name}",
            use_3d=bool(args.__dict__.get("3d")),
        )
        print(f"Solved {puzzle_path.name}: colors={len(puzzle.terminals)}, nodes={len(puzzle.graph)}, edges={sum(1 for _ in puzzle.graph.edges())}")
        for c, path in res.paths.items():
            print(f"  {c}: path_len={len(path)}")
        print(f"Wrote solution visualization: {out}")
        return 0

    raise AssertionError("unreachable")



