"""Sweep camera review thresholds against labeled replay reports.

Each ``replay_camera_photo_corpus.py`` report stores the inputs to the review
rules for every photo.  This script re-runs the real rules
(``backend.app._camera_photo_review``) while varying one threshold at a time
and reports, for each value, how many wrong results would go unflagged and how
many photos would be sent to review.  Calibrate on the ``tuning`` split and
confirm on ``holdout``; never pick thresholds from the held-out photos.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SWEEP_FACTORS = (0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0)


def _load_results(paths: list[Path], split: str | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        for result in report.get("results", []):
            if not isinstance(result.get("review_inputs"), dict) or result.get("camera_status") != 200:
                continue
            if split and result.get("split") not in {split, None}:
                continue
            results.append(result)
    return results


def _evaluate(results: list[dict[str, Any]], thresholds: dict[str, float]) -> dict[str, Any]:
    from backend.app import _camera_photo_review

    unflagged_wrong = 0
    flagged = 0
    flagged_correct = 0
    for result in results:
        inputs = result["review_inputs"]
        review = _camera_photo_review(
            inputs.get("photo") or None,
            inputs.get("terminal_completeness"),
            thresholds=thresholds,
        )
        is_flagged = bool(review["required"] or inputs.get("completeness_review"))
        flagged += int(is_flagged)
        flagged_correct += int(is_flagged and result.get("match"))
        unflagged_wrong += int(not is_flagged and not result.get("match"))
    total = max(1, len(results))
    return {
        "photos": len(results),
        "unflagged_wrong": unflagged_wrong,
        "unflagged_wrong_rate": round(unflagged_wrong / total, 4),
        "review_rate": round(flagged / total, 4),
        "flagged_but_correct": flagged_correct,
    }


def calibrate(results: list[dict[str, Any]], *, max_unflagged_wrong_rate: float) -> dict[str, Any]:
    from backend.app import CAMERA_REVIEW_THRESHOLDS

    report: dict[str, Any] = {
        "current": {**_evaluate(results, dict(CAMERA_REVIEW_THRESHOLDS)), "thresholds": dict(CAMERA_REVIEW_THRESHOLDS)},
        "sweeps": {},
    }
    for key, current in CAMERA_REVIEW_THRESHOLDS.items():
        rows = []
        for factor in SWEEP_FACTORS:
            value = round(float(current) * factor, 6)
            rows.append({"value": value, **_evaluate(results, {key: value})})
        acceptable = [row for row in rows if row["unflagged_wrong_rate"] <= max_unflagged_wrong_rate]
        best = min(acceptable, key=lambda row: (row["review_rate"], abs(row["value"] - current))) if acceptable else None
        report["sweeps"][key] = {"current": current, "rows": rows, "suggested": best["value"] if best else None}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+", help="replay_camera_photo_corpus.py JSON reports")
    parser.add_argument("--split", choices=("tuning", "holdout"), default="tuning")
    parser.add_argument("--max-unflagged-wrong-rate", type=float, default=0.01)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = _load_results(args.reports, args.split)
    if not results:
        raise SystemExit("No replay results with review inputs; rerun replay_camera_photo_corpus.py")
    report = calibrate(results, max_unflagged_wrong_rate=args.max_unflagged_wrong_rate)
    print(json.dumps(report["current"], indent=2))
    for key, sweep in report["sweeps"].items():
        print(f"\n{key} (current {sweep['current']}, suggested {sweep['suggested']})")
        for row in sweep["rows"]:
            print(
                f"  {row['value']:>10}  unflagged wrong {row['unflagged_wrong']:>3}"
                f"  review rate {row['review_rate']:.3f}  flagged but correct {row['flagged_but_correct']}"
            )
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
