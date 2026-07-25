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
