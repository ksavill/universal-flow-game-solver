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
    "build_radial_star_topology",
    "build_ring_topology",
    "build_topology",
    "get_topology_template",
    "topology_names",
]
