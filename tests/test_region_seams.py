from __future__ import annotations

from backend.region_seams import infer_bounded_region_seams


def test_infers_single_minimum_seam_for_split_track() -> None:
    nodes = {
        str(index): {
            "pos": [float(index), 0.0, 0.0],
            "data": {"pixel_center": [float(index * 10), 10.0]},
        }
        for index in range(4)
    }

    seams, info = infer_bounded_region_seams(
        nodes,
        [("0", "1"), ("2", "3")],
        {"A": ["0", "3"]},
        timeout_ms=5_000,
        initial_seam_budget=1,
        max_seam_budget=1,
    )

    assert seams == [("1", "2")]
    assert info["attempted"] is True
    assert info["selected_edges"] == 1
    assert info["warnings"] == []


def _split_track() -> tuple[dict, list, dict]:
    nodes = {
        str(index): {"pos": [float(index), 0.0, 0.0], "data": {"pixel_center": [float(index * 10), 10.0]}}
        for index in range(4)
    }
    return nodes, [("0", "1"), ("2", "3")], {"A": ["0", "3"]}


def test_exhausted_work_budget_is_reported_not_silent() -> None:
    nodes, edges, terminals = _split_track()

    no_conflicts, conflict_info = infer_bounded_region_seams(
        nodes, edges, terminals, initial_seam_budget=1, max_seam_budget=1, max_conflicts=0
    )
    no_rounds, round_info = infer_bounded_region_seams(
        nodes, edges, terminals, initial_seam_budget=1, max_seam_budget=1, max_validation_rounds=0
    )

    for seams, info in ((no_conflicts, conflict_info), (no_rounds, round_info)):
        assert seams == []
        assert info["incomplete"] is True and info["budget_exhausted"] is True
        assert "work budget" in info["warnings"][0]


def test_wall_clock_safety_net_is_reported_for_review(monkeypatch) -> None:
    from flow_solver.solver import z3_solver

    def expired(self, stage: str) -> None:
        raise z3_solver.SolveTimeoutError(f"deadline expired during {stage}")

    monkeypatch.setattr(z3_solver._Deadline, "check", expired)
    nodes, edges, terminals = _split_track()

    seams, info = infer_bounded_region_seams(nodes, edges, terminals, initial_seam_budget=1, max_seam_budget=1)

    assert seams == []
    assert info["incomplete"] is True and info["timed_out"] is True
    assert "safety limit" in info["warnings"][0]


def test_completed_inference_reports_its_deterministic_work() -> None:
    nodes, edges, terminals = _split_track()

    first = infer_bounded_region_seams(nodes, edges, terminals, initial_seam_budget=1, max_seam_budget=1)
    second = infer_bounded_region_seams(nodes, edges, terminals, initial_seam_budget=1, max_seam_budget=1)

    assert first[0] == second[0] == [("1", "2")]
    assert first[1]["conflicts"] == second[1]["conflicts"]
    assert "incomplete" not in first[1]
