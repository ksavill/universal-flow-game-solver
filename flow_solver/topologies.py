from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Tuple

from .graph import Graph, Node

Position = Tuple[float, float, float]
Edge = Tuple[str, str]
TopologyBuilder = Callable[..., "TopologySpec"]


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """One solver cell/channel together with its display position."""

    id: str
    pos: Position
    kind: str = "cell"
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TopologySpec:
    """Backend-independent, deterministic description of a puzzle topology.

    The nodes in these templates are physical cells (or, in future templates,
    explicit channels), not samples taken from inside a board silhouette.  That
    distinction is important: sampling an axial lattice inside a star or track
    gives cells six neighbors even when the drawn cells only have four sides.
    """

    template: str
    family: str
    nodes: Tuple[TopologyNode, ...]
    edges: Tuple[Edge, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    high_degree_reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.template.strip():
            raise ValueError("Topology template name cannot be empty")
        if not self.family.strip():
            raise ValueError("Topology family cannot be empty")
        if not self.nodes:
            raise ValueError(f"Topology {self.template!r} has no nodes")

        node_ids = [node.id for node in self.nodes]
        if any(not node_id for node_id in node_ids):
            raise ValueError(f"Topology {self.template!r} contains an empty node id")
        if len(set(node_ids)) != len(node_ids):
            raise ValueError(f"Topology {self.template!r} contains duplicate node ids")

        known = set(node_ids)
        seen_edges: set[Edge] = set()
        degrees: Counter[str] = Counter()
        for u, v in self.edges:
            if u == v:
                raise ValueError(f"Topology {self.template!r} contains self-loop {u!r}")
            if u not in known or v not in known:
                raise ValueError(
                    f"Topology {self.template!r} edge ({u!r}, {v!r}) has an unknown endpoint"
                )
            canonical = _canonical_edge(u, v)
            if canonical in seen_edges:
                raise ValueError(
                    f"Topology {self.template!r} contains duplicate edge {canonical!r}"
                )
            seen_edges.add(canonical)
            degrees[u] += 1
            degrees[v] += 1

        unknown_reasons = set(self.high_degree_reasons) - known
        if unknown_reasons:
            raise ValueError(
                f"Topology {self.template!r} has high-degree reasons for unknown nodes: "
                f"{sorted(unknown_reasons)!r}"
            )
        unexplained = [
            node_id
            for node_id, degree in degrees.items()
            if degree > 4 and not str(self.high_degree_reasons.get(node_id, "")).strip()
        ]
        if unexplained:
            raise ValueError(
                f"Topology {self.template!r} has unexplained degree > 4 nodes: "
                f"{sorted(unexplained)!r}"
            )

        for node in self.nodes:
            if len(node.pos) != 3 or not all(math.isfinite(float(value)) for value in node.pos):
                raise ValueError(
                    f"Topology {self.template!r} node {node.id!r} has an invalid position"
                )

        if not self.is_connected():
            raise ValueError(f"Topology {self.template!r} is disconnected")

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def node_ids(self) -> Tuple[str, ...]:
        return tuple(node.id for node in self.nodes)

    def adjacency(self) -> Dict[str, set[str]]:
        out = {node.id: set() for node in self.nodes}
        for u, v in self.edges:
            out[u].add(v)
            out[v].add(u)
        return out

    def degree(self, node_id: str) -> int:
        if node_id not in set(self.node_ids()):
            raise KeyError(f"Unknown topology node: {node_id!r}")
        return sum(1 for u, v in self.edges if u == node_id or v == node_id)

    def degree_histogram(self) -> Dict[int, int]:
        adjacency = self.adjacency()
        return dict(sorted(Counter(len(neighbors) for neighbors in adjacency.values()).items()))

    @property
    def max_degree(self) -> int:
        return max(len(neighbors) for neighbors in self.adjacency().values())

    def is_connected(self) -> bool:
        if not self.nodes:
            return False
        adjacency = self.adjacency()
        start = self.nodes[0].id
        seen = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        return len(seen) == len(self.nodes)

    def to_graph(self) -> Graph:
        """Compile this topology into the solver's existing lightweight graph."""

        graph = Graph()
        for node in self.nodes:
            graph.add_node(
                Node(
                    id=node.id,
                    pos=(float(node.pos[0]), float(node.pos[1]), float(node.pos[2])),
                    kind=node.kind,
                    data=dict(node.data),
                )
            )
        for u, v in self.edges:
            graph.add_edge(u, v)
        return graph

    def to_space_json(self) -> Dict[str, Any]:
        """Return the current graph-space JSON shape used by ``Puzzle``."""

        nodes: Dict[str, Dict[str, Any]] = {}
        for node in self.nodes:
            item: Dict[str, Any] = {
                "pos": [float(node.pos[0]), float(node.pos[1]), float(node.pos[2])]
            }
            if node.kind != "cell":
                item["kind"] = node.kind
            if node.data:
                item["data"] = dict(node.data)
            nodes[node.id] = item
        return {
            "type": "graph",
            "topology": self.template,
            "topology_family": self.family,
            "parameters": dict(self.parameters),
            "nodes": nodes,
            "edges": [[u, v] for u, v in self.edges],
        }


@dataclass(frozen=True, slots=True)
class TopologyTemplate:
    name: str
    family: str
    description: str
    builder: TopologyBuilder
    aliases: Tuple[str, ...] = ()

    def build(self, **parameters: Any) -> TopologySpec:
        spec = self.builder(**parameters)
        if spec.template != self.name:
            raise ValueError(
                f"Topology builder for {self.name!r} returned template {spec.template!r}"
            )
        return spec


def _canonical_edge(u: str, v: str) -> Edge:
    return (u, v) if u < v else (v, u)


def _make_spec(
    *,
    template: str,
    family: str,
    nodes: Iterable[TopologyNode],
    edges: Iterable[Edge],
    parameters: Mapping[str, Any],
    high_degree_reasons: Mapping[str, str] | None = None,
) -> TopologySpec:
    node_tuple = tuple(nodes)
    edge_tuple = tuple(sorted({_canonical_edge(str(u), str(v)) for u, v in edges}))
    return TopologySpec(
        template=template,
        family=family,
        nodes=node_tuple,
        edges=edge_tuple,
        parameters=dict(parameters),
        high_degree_reasons=dict(high_degree_reasons or {}),
    )


def build_grid_topology(*, width: int, height: int) -> TopologySpec:
    """Build an ordinary four-neighbor rectangular cell grid."""

    width_n = int(width)
    height_n = int(height)
    if width_n < 1 or height_n < 1:
        raise ValueError("Grid width and height must both be positive")

    nodes: list[TopologyNode] = []
    edges: list[Edge] = []
    for y in range(height_n):
        for x in range(width_n):
            node_id = f"{x},{y}"
            nodes.append(
                TopologyNode(
                    id=node_id,
                    pos=(float(x), float(-y), 0.0),
                    data={"column": x, "row": y},
                )
            )
            if x > 0:
                edges.append((f"{x - 1},{y}", node_id))
            if y > 0:
                edges.append((f"{x},{y - 1}", node_id))

    return _make_spec(
        template="grid",
        family="square_grid",
        nodes=nodes,
        edges=edges,
        parameters={"width": width_n, "height": height_n},
    )


def build_hex_topology(*, width: int, height: int) -> TopologySpec:
    """Build an odd-row-offset six-neighbor hexagonal cell grid."""

    width_n = int(width)
    height_n = int(height)
    if width_n < 1 or height_n < 1:
        raise ValueError("Hex width and height must both be positive")

    nodes: list[TopologyNode] = []
    edges: list[Edge] = []
    y_step = math.sqrt(3.0) / 2.0

    def node_id(x: int, y: int) -> str:
        return f"{x},{y}"

    for y in range(height_n):
        for x in range(width_n):
            current = node_id(x, y)
            nodes.append(
                TopologyNode(
                    id=current,
                    pos=(float(x) + (0.5 if y % 2 else 0.0), -float(y) * y_step, 0.0),
                    data={"column": x, "row": y, "offset": "odd-r"},
                )
            )
            # East plus the two downward directions generate every undirected
            # six-neighbor edge once.
            candidates = [(x + 1, y)]
            if y % 2 == 0:
                candidates.extend(((x - 1, y + 1), (x, y + 1)))
            else:
                candidates.extend(((x, y + 1), (x + 1, y + 1)))
            for nx, ny in candidates:
                if 0 <= nx < width_n and 0 <= ny < height_n:
                    edges.append((current, node_id(nx, ny)))

    return _make_spec(
        template="hex_grid",
        family="hexagonal_grid",
        nodes=nodes,
        edges=edges,
        parameters={"width": width_n, "height": height_n, "offset": "odd-r"},
        high_degree_reasons={
            node.id: "Hexagonal cells intentionally have up to six local neighbors"
            for node in nodes
        },
    )


def build_ring_topology(*, rings: int, sectors: int, core: bool = False) -> TopologySpec:
    """Build concentric rings of quadrilateral cells.

    Each ring wraps angularly and adjacent rings connect in the same sector.
    ``core=True`` adds one physical center cell.  Its fan-out is explicitly
    recorded when it exceeds four, rather than silently creating an unexplained
    high-degree ordinary cell.
    """

    ring_count = int(rings)
    sector_count = int(sectors)
    if ring_count < 1:
        raise ValueError("Ring count must be positive")
    if sector_count < 3:
        raise ValueError("A ring needs at least three sectors")

    nodes: list[TopologyNode] = []
    edges: list[Edge] = []
    base_radius = max(1.0, float(sector_count) / (2.0 * math.pi))

    for ring in range(ring_count):
        radius = base_radius + float(ring)
        for sector in range(sector_count):
            theta = (math.pi / 2.0) - (
                2.0 * math.pi * (float(sector) + 0.5) / float(sector_count)
            )
            node_id = f"{sector},{ring}"
            nodes.append(
                TopologyNode(
                    id=node_id,
                    pos=(radius * math.cos(theta), radius * math.sin(theta), 0.0),
                    data={"ring": ring, "sector": sector},
                )
            )
            edges.append((node_id, f"{(sector + 1) % sector_count},{ring}"))
            if ring + 1 < ring_count:
                edges.append((node_id, f"{sector},{ring + 1}"))

    high_degree_reasons: Dict[str, str] = {}
    if core:
        nodes.append(
            TopologyNode(
                id="core",
                pos=(0.0, 0.0, 0.0),
                kind="core",
                data={"role": "center_cell"},
            )
        )
        for sector in range(sector_count):
            edges.append(("core", f"{sector},0"))
        if sector_count > 4:
            high_degree_reasons["core"] = (
                "Explicit center cell is incident to every innermost ring sector"
            )

    return _make_spec(
        template="ring",
        family="annular_grid",
        nodes=nodes,
        edges=edges,
        parameters={"rings": ring_count, "sectors": sector_count, "core": bool(core)},
        high_degree_reasons=high_degree_reasons,
    )


def _build_radial_fan(
    *,
    template: str,
    family: str,
    prefix: str,
    faces: int,
    size: int,
) -> TopologySpec:
    """Build square/rhombus face grids joined cyclically around one corner.

    A three-face fan is the visible cube board. Five 2x2 faces match
    ``IMG_3243``; six 3x3 faces match the six-point radial star in
    ``IMG_3241``. Every solver cell remains four-neighbor or less.
    """

    face_count = int(faces)
    side = int(size)
    if face_count < 3:
        raise ValueError("A radial fan needs at least three faces")
    if side < 1:
        raise ValueError("Radial face size must be positive")

    nodes: list[TopologyNode] = []
    edges: list[Edge] = []

    def node_id(face: int, u: int, v: int) -> str:
        return f"{prefix}:{face}:{u},{v}"

    rays = [
        (
            math.cos((math.pi / 2.0) - 2.0 * math.pi * float(index) / float(face_count)),
            math.sin((math.pi / 2.0) - 2.0 * math.pi * float(index) / float(face_count)),
        )
        for index in range(face_count)
    ]

    for face in range(face_count):
        ray_a = rays[face]
        ray_b = rays[(face + 1) % face_count]
        for v in range(side):
            for u in range(side):
                along_a = (float(u) + 0.5) / float(side)
                along_b = (float(v) + 0.5) / float(side)
                x = along_a * ray_a[0] + along_b * ray_b[0]
                y = along_a * ray_a[1] + along_b * ray_b[1]
                current = node_id(face, u, v)
                nodes.append(
                    TopologyNode(
                        id=current,
                        pos=(x, y, 0.0),
                        data={"face": face, "u": u, "v": v},
                    )
                )
                if u + 1 < side:
                    edges.append((current, node_id(face, u + 1, v)))
                if v + 1 < side:
                    edges.append((current, node_id(face, u, v + 1)))

    # Face f's edge on ray f+1 joins the next face's edge on that same ray.
    # The distance from the common center is preserved by the index mapping.
    for face in range(face_count):
        next_face = (face + 1) % face_count
        for offset in range(side):
            edges.append((node_id(face, 0, offset), node_id(next_face, offset, 0)))

    return _make_spec(
        template=template,
        family=family,
        nodes=nodes,
        edges=edges,
        parameters={"faces": face_count, "size": side},
    )


def build_cube_topology(*, size: int) -> TopologySpec:
    """Build the three visible square faces of a Flow Shapes cube board."""

    return _build_radial_fan(
        template="cube",
        family="radial_surface",
        prefix="cube",
        faces=3,
        size=size,
    )


def build_radial_star_topology(*, size: int, faces: int = 6) -> TopologySpec:
    """Build a five- or six-face radial star surface."""

    face_count = int(faces)
    if face_count not in {5, 6}:
        raise ValueError("Radial star templates currently support exactly 5 or 6 faces")
    return _build_radial_fan(
        template="radial_star",
        family="radial_surface",
        prefix="star",
        faces=face_count,
        size=size,
    )


# Cell centroids and the planar-dual adjacency below are normalized from the
# 1x2 linked-loop reference board (IMG_3245). Keeping this as a named template
# is deliberate: a different knot/track is a different template, not a mask
# parameter that invents six-neighbor adjacencies.
_FIGURE8_POSITIONS: Tuple[Tuple[float, float], ...] = (
    (0.00, 5.28),
    (0.99, 4.94),
    (-0.99, 4.94),
    (1.55, 4.05),
    (-1.55, 4.05),
    (1.43, 3.01),
    (-1.43, 3.01),
    (-0.74, 2.22),
    (0.74, 2.22),
    (0.00, 1.48),
    (0.73, 0.73),
    (-0.73, 0.73),
    (0.00, 0.00),
    (1.47, -0.01),
    (-1.47, -0.01),
    (0.75, -0.73),
    (-0.75, -0.73),
    (2.33, -1.10),
    (-2.33, -1.10),
    (1.44, -1.52),
    (-1.44, -1.52),
    (-1.56, -2.57),
    (1.56, -2.57),
    (2.51, -2.79),
    (-2.51, -2.79),
    (1.00, -3.47),
    (-1.00, -3.47),
    (0.00, -3.82),
    (-1.60, -4.22),
    (1.60, -4.22),
    (0.00, -4.79),
)

_FIGURE8_INDEX_EDGES: Tuple[Tuple[int, int], ...] = (
    # Ten-cell upper loop; cell 9 is its shared waist cell.
    (0, 1),
    (1, 3),
    (3, 5),
    (5, 8),
    (8, 9),
    (9, 7),
    (7, 6),
    (6, 4),
    (4, 2),
    (2, 0),
    # Lower two-wide track/mesh.
    (9, 10),
    (9, 11),
    (10, 12),
    (10, 13),
    (11, 12),
    (11, 14),
    (12, 15),
    (12, 16),
    (13, 15),
    (13, 17),
    (14, 16),
    (14, 18),
    (15, 19),
    (16, 20),
    (17, 19),
    (17, 23),
    (18, 20),
    (18, 24),
    (19, 22),
    (20, 21),
    (21, 24),
    (21, 26),
    (22, 23),
    (22, 25),
    (23, 29),
    (24, 28),
    (25, 27),
    (25, 29),
    (26, 27),
    (26, 28),
    (27, 30),
    (28, 30),
    (29, 30),
)


def build_figure8_topology() -> TopologySpec:
    """Build the faithful 1x2 linked-loop/figure-eight reference track."""

    nodes = [
        TopologyNode(
            id=f"fig8:n{index:02d}",
            pos=(x, y, 0.0),
            data={"region_index": index},
        )
        for index, (x, y) in enumerate(_FIGURE8_POSITIONS)
    ]
    edges = [
        (f"fig8:n{u:02d}", f"fig8:n{v:02d}") for u, v in _FIGURE8_INDEX_EDGES
    ]
    return _make_spec(
        template="figure8",
        family="linked_tracks",
        nodes=nodes,
        edges=edges,
        parameters={"variant": "1x2", "source_fixture": "IMG_3245.PNG"},
    )


# Region centroids and planar-dual edges verified from the second linked-loop
# Shapes reference level (IMG_3246). Region ids intentionally retain detector
# gaps: they make comparisons with the archived level exact and reviewable.
_LINKED_LOOPS_2X2_NODES: Tuple[Tuple[int, float, float], ...] = (
    (0, 620.5, -115.0), (1, 481.0, -217.0), (2, 760.0, -217.0),
    (3, 620.5, -303.5), (4, 900.5, -450.0), (5, 340.5, -450.5),
    (6, 491.0, -448.5), (7, 750.5, -448.5), (9, 448.5, -647.5),
    (10, 792.5, -648.0), (11, 282.0, -706.5), (12, 959.0, -706.5),
    (13, 620.5, -779.5), (14, 460.0, -810.5), (15, 781.0, -811.0),
    (16, 312.5, -878.0), (17, 928.5, -878.0), (18, 163.5, -994.0),
    (19, 1077.5, -994.5), (21, 512.5, -967.5), (22, 728.5, -967.5),
    (23, 386.5, -1030.5), (24, 854.5, -1030.5), (25, 620.5, -1083.5),
    (28, 487.5, -1176.0), (29, 753.5, -1176.0), (30, 107.5, -1190.5),
    (31, 1133.5, -1190.5), (32, 324.5, -1232.5), (33, 916.5, -1232.5),
    (34, 621.0, -1292.0), (35, 1130.0, -1355.5), (36, 111.0, -1355.5),
    (37, 398.5, -1372.5), (38, 842.5, -1372.5),
)
_LINKED_LOOPS_2X2_EDGES: Tuple[Tuple[int, int], ...] = (
    (0, 1), (0, 2), (1, 3), (1, 5), (2, 3), (2, 4), (3, 6), (3, 7),
    (4, 7), (4, 12), (5, 6), (5, 11), (6, 9), (7, 10), (9, 11),
    (9, 14), (10, 12), (10, 15), (11, 16), (12, 17), (13, 14),
    (13, 15), (14, 16), (14, 21), (15, 17), (15, 22), (16, 18),
    (16, 23), (17, 19), (17, 24), (18, 30), (19, 31), (21, 23),
    (21, 25), (22, 24), (22, 25), (23, 28), (24, 29), (25, 28),
    (25, 29), (28, 32), (28, 34), (29, 33), (29, 34), (30, 32),
    (30, 36), (31, 33), (31, 35), (32, 37), (33, 38), (34, 37),
    (34, 38), (35, 38), (36, 37),
)


def build_linked_loops_2x2_topology() -> TopologySpec:
    """Build the verified 35-cell linked-loop Shapes board from IMG_3246."""

    center_x = 620.5
    center_y = -780.0
    scale = 200.0
    nodes = [
        TopologyNode(
            id=f"loops2:region:{index:03d}",
            pos=((x - center_x) / scale, (y - center_y) / scale, 0.0),
            data={"source_region": index},
        )
        for index, x, y in _LINKED_LOOPS_2X2_NODES
    ]
    edges = [
        (f"loops2:region:{left:03d}", f"loops2:region:{right:03d}")
        for left, right in _LINKED_LOOPS_2X2_EDGES
    ]
    return _make_spec(
        template="linked_loops_2x2",
        family="linked_tracks",
        nodes=nodes,
        edges=edges,
        parameters={"variant": "2x2", "source_fixture": "IMG_3246.PNG"},
    )


def build_selective_warp_grid_topology(
    *,
    width: int = 9,
    height: int = 9,
    horizontal_rows: Tuple[int, ...] = (1, 2, 3, 5, 6, 7),
    vertical_columns: Tuple[int, ...] = (1, 2, 3, 5, 6, 7),
) -> TopologySpec:
    """Grid plus the selective paired boundary ports verified in IMG_4064/4065."""

    base = build_grid_topology(width=width, height=height)
    rows = tuple(sorted({int(value) for value in horizontal_rows}))
    columns = tuple(sorted({int(value) for value in vertical_columns}))
    if any(value < 0 or value >= height for value in rows):
        raise ValueError("Horizontal warp row is outside the grid")
    if any(value < 0 or value >= width for value in columns):
        raise ValueError("Vertical warp column is outside the grid")
    warp_edges = [
        (f"0,{row}", f"{width - 1},{row}") for row in rows
    ] + [
        (f"{column},0", f"{column},{height - 1}") for column in columns
    ]
    return _make_spec(
        template="selective_warp_grid",
        family="warped_grid",
        nodes=base.nodes,
        edges=(*base.edges, *warp_edges),
        parameters={
            "width": int(width),
            "height": int(height),
            "horizontal_rows": rows,
            "vertical_columns": columns,
            "warp_edges": tuple(warp_edges),
            "source_fixtures": ("IMG_4064", "IMG_4065"),
        },
    )


_TEMPLATES: Tuple[TopologyTemplate, ...] = (
    TopologyTemplate(
        name="grid",
        family="square_grid",
        description="Four-neighbor rectangular cell grid",
        builder=build_grid_topology,
        aliases=("square", "square_grid"),
    ),
    TopologyTemplate(
        name="ring",
        family="annular_grid",
        description="Concentric circular rings with angular wrap",
        builder=build_ring_topology,
        aliases=("circle", "annulus"),
    ),
    TopologyTemplate(
        name="hex_grid",
        family="hexagonal_grid",
        description="Six-neighbor odd-row-offset hexagonal grid",
        builder=build_hex_topology,
        aliases=("hex", "hexagonal_grid"),
    ),
    TopologyTemplate(
        name="cube",
        family="radial_surface",
        description="Three visible square faces joined around a cube corner",
        builder=build_cube_topology,
    ),
    TopologyTemplate(
        name="radial_star",
        family="radial_surface",
        description="Five- or six-face radial square/rhombus surface",
        builder=build_radial_star_topology,
        aliases=("star", "surface_fan"),
    ),
    TopologyTemplate(
        name="figure8",
        family="linked_tracks",
        description="Reference 1x2 linked-loop track",
        builder=build_figure8_topology,
        aliases=("figure8_track", "linked_loop_1x2"),
    ),
    TopologyTemplate(
        name="linked_loops_2x2",
        family="linked_tracks",
        description="Verified 35-cell linked-loop Shapes board",
        builder=build_linked_loops_2x2_topology,
        aliases=("linked_loop_2x2", "img_3246"),
    ),
    TopologyTemplate(
        name="selective_warp_grid",
        family="warped_grid",
        description="Grid with declared paired boundary warp ports",
        builder=build_selective_warp_grid_topology,
        aliases=("warps_9x9", "img_4064", "img_4065"),
    ),
)

TOPOLOGY_REGISTRY: Mapping[str, TopologyTemplate] = {
    template.name: template for template in _TEMPLATES
}
_TOPOLOGY_ALIASES: Mapping[str, str] = {
    alias: template.name for template in _TEMPLATES for alias in template.aliases
}


def topology_names() -> Tuple[str, ...]:
    return tuple(sorted(TOPOLOGY_REGISTRY))


def get_topology_template(name: str) -> TopologyTemplate:
    normalized = str(name).strip().lower()
    canonical = _TOPOLOGY_ALIASES.get(normalized, normalized)
    try:
        return TOPOLOGY_REGISTRY[canonical]
    except KeyError as exc:
        choices = ", ".join(topology_names())
        raise KeyError(f"Unknown topology template {name!r}; available: {choices}") from exc


def build_topology(name: str, **parameters: Any) -> TopologySpec:
    return get_topology_template(name).build(**parameters)


__all__ = [
    "Edge",
    "Position",
    "TOPOLOGY_REGISTRY",
    "TopologyNode",
    "TopologySpec",
    "TopologyTemplate",
    "build_cube_topology",
    "build_figure8_topology",
    "build_grid_topology",
    "build_hex_topology",
    "build_linked_loops_2x2_topology",
    "build_radial_star_topology",
    "build_ring_topology",
    "build_selective_warp_grid_topology",
    "build_topology",
    "get_topology_template",
    "topology_names",
]
