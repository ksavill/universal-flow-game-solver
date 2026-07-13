from __future__ import annotations

from collections import Counter

import pytest

from backend.image_utils import build_graph_json
from flow_solver.topologies import (
    build_cube_topology,
    build_figure8_topology,
    build_grid_topology,
    build_hex_topology,
    build_radial_star_topology,
    build_ring_topology,
    build_topology,
    topology_names,
)


def _degree_histogram(spec) -> dict[int, int]:
    adjacency = spec.adjacency()
    return dict(sorted(Counter(len(neighbors) for neighbors in adjacency.values()).items()))


def _json_degree_histogram(space: dict[str, object]) -> dict[int, int]:
    nodes = space["nodes"]
    edges = space["edges"]
    assert isinstance(nodes, dict)
    assert isinstance(edges, list)
    degrees = Counter({str(node_id): 0 for node_id in nodes})
    for raw_edge in edges:
        assert isinstance(raw_edge, list) and len(raw_edge) == 2
        degrees[str(raw_edge[0])] += 1
        degrees[str(raw_edge[1])] += 1
    return dict(sorted(Counter(degrees.values()).items()))


def _articulation_points(spec) -> set[str]:
    adjacency = spec.adjacency()
    points: set[str] = set()
    for removed in adjacency:
        remaining = [node_id for node_id in adjacency if node_id != removed]
        if not remaining:
            continue
        seen = {remaining[0]}
        pending = [remaining[0]]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor != removed and neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        if len(seen) != len(remaining):
            points.add(removed)
    return points


def test_registry_exposes_stable_templates_and_aliases() -> None:
    assert topology_names() == ("cube", "figure8", "grid", "hex_grid", "radial_star", "ring")
    assert build_topology("square", width=2, height=2).template == "grid"
    assert build_topology("hex", width=2, height=2).template == "hex_grid"
    assert build_topology("circle", rings=2, sectors=8).template == "ring"
    assert build_topology("star", size=2, faces=5).template == "radial_star"


def test_square_grid_3x3_exact_graph() -> None:
    spec = build_grid_topology(width=3, height=3)
    assert (spec.node_count, spec.edge_count) == (9, 12)
    assert _degree_histogram(spec) == {2: 4, 3: 4, 4: 1}
    assert spec.max_degree == 4
    assert ("0,0", "1,0") in spec.edges
    assert ("0,0", "0,1") in spec.edges
    assert ("0,0", "1,1") not in spec.edges


def test_hex_grid_3x3_exact_graph() -> None:
    spec = build_hex_topology(width=3, height=3)
    assert (spec.node_count, spec.edge_count) == (9, 16)
    assert _degree_histogram(spec) == {2: 2, 3: 3, 4: 2, 5: 1, 6: 1}
    assert spec.max_degree == 6
    assert spec.high_degree_reasons["1,1"]
    assert ("0,0", "1,1") not in spec.edges
    assert ("0,1", "1,2") in spec.edges


def test_reference_circle_2x8_exact_graph() -> None:
    spec = build_ring_topology(rings=2, sectors=8)
    assert (spec.node_count, spec.edge_count) == (16, 24)
    assert _degree_histogram(spec) == {3: 16}
    assert spec.max_degree == 3
    assert ("0,0", "7,0") in spec.edges
    assert ("3,0", "3,1") in spec.edges
    assert ("3,0", "4,1") not in spec.edges


def test_ring_core_records_justification_for_high_degree_center() -> None:
    spec = build_ring_topology(rings=1, sectors=8, core=True)
    assert spec.degree("core") == 8
    assert "core" in spec.high_degree_reasons


def test_reference_cube_three_faces_2x2_exact_graph() -> None:
    spec = build_cube_topology(size=2)
    assert spec.parameters == {"faces": 3, "size": 2}
    assert (spec.node_count, spec.edge_count) == (12, 18)
    assert _degree_histogram(spec) == {2: 3, 3: 6, 4: 3}
    assert spec.max_degree == 4


def test_reference_five_face_radial_surface_2x2_exact_graph() -> None:
    # IMG_3243 contains five joined 2x2 faces, not the three-face cube template.
    spec = build_radial_star_topology(size=2, faces=5)
    assert (spec.node_count, spec.edge_count) == (20, 30)
    assert _degree_histogram(spec) == {2: 5, 3: 10, 4: 5}
    assert spec.max_degree == 4


def test_reference_six_face_star_3x3_exact_graph() -> None:
    # IMG_3241 is six cyclic 3x3 rhombus/square faces: 54 cells, not an
    # axial lattice clipped to a similar-looking silhouette.
    spec = build_radial_star_topology(size=3, faces=6)
    assert (spec.node_count, spec.edge_count) == (54, 90)
    assert _degree_histogram(spec) == {2: 6, 3: 24, 4: 24}
    assert spec.max_degree == 4


def test_reference_figure8_track_exact_region_dual() -> None:
    spec = build_figure8_topology()
    assert (spec.node_count, spec.edge_count) == (31, 43)
    assert _degree_histogram(spec) == {2: 9, 3: 20, 4: 2}
    assert spec.max_degree == 4
    assert _articulation_points(spec) == {"fig8:n09"}


def test_specs_compile_to_existing_graph_and_json_without_topology_loss() -> None:
    spec = build_radial_star_topology(size=2, faces=6)
    graph = spec.to_graph()
    assert len(graph.nodes) == spec.node_count
    assert set(graph.edges()) == set(spec.edges)

    space = spec.to_space_json()
    assert space["topology"] == "radial_star"
    assert space["topology_family"] == "radial_surface"
    assert space["parameters"] == {"faces": 6, "size": 2}
    assert len(space["nodes"]) == spec.node_count
    assert {tuple(edge) for edge in space["edges"]} == set(spec.edges)


def test_radial_star_rejects_silent_shape_coercion() -> None:
    with pytest.raises(ValueError, match="exactly 5 or 6"):
        build_radial_star_topology(size=2, faces=4)
    with pytest.raises(ValueError, match="positive"):
        build_cube_topology(size=0)


@pytest.mark.parametrize(
    ("layout", "width", "height", "expected_counts", "expected_degrees"),
    [
        ("cube", 2, 2, (12, 18), {2: 3, 3: 6, 4: 3}),
        ("star", 2, 2, (20, 30), {2: 5, 3: 10, 4: 5}),
        ("figure8", 1, 2, (31, 43), {2: 9, 3: 20, 4: 2}),
    ],
)
def test_backend_graph_json_uses_registered_topologies(
    layout: str,
    width: int,
    height: int,
    expected_counts: tuple[int, int],
    expected_degrees: dict[int, int],
) -> None:
    obj = build_graph_json(layout=layout, width=width, height=height, nodes=2, meta={})
    space = obj["space"]
    assert space["type"] == "graph"
    assert space["topology"] == layout
    assert (len(space["nodes"]), len(space["edges"])) == expected_counts
    assert _json_degree_histogram(space) == expected_degrees
    assert max(expected_degrees) <= 4


def test_backend_registry_integration_preserves_edge_override_shape() -> None:
    warp = ("star:0:0,0", "star:2:1,1")
    wall = ("star:0:0,0", "star:0:1,0")
    obj = build_graph_json(
        layout="star",
        width=2,
        height=2,
        nodes=2,
        meta={"source": "unit-test"},
        warp_edges=[warp],
        wall_edges=[wall],
    )
    space = obj["space"]
    assert space["warps"] == [list(warp)]
    assert space["walls"] == [list(wall)]
    assert space["edge_overrides"] == {
        "add": [list(warp)],
        "remove": [list(wall)],
    }


def test_backend_star_face_count_is_configurable_with_five_face_default() -> None:
    default_obj = build_graph_json(layout="star", width=2, height=2, nodes=2, meta={})
    assert len(default_obj["space"]["nodes"]) == 20

    six_face_obj = build_graph_json(
        layout="star",
        width=3,
        height=3,
        nodes=3,
        meta={},
        star_faces=6,
    )
    space = six_face_obj["space"]
    assert (len(space["nodes"]), len(space["edges"])) == (54, 90)
    assert _json_degree_histogram(space) == {2: 6, 3: 24, 4: 24}
