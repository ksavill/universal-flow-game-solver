from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Set, Tuple

from .graph import NodeId
from .puzzle import Color, Puzzle

IssueSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: IssueSeverity = "error"
    nodes: Tuple[NodeId, ...] = ()
    colors: Tuple[Color, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def add(
        self,
        code: str,
        message: str,
        *,
        severity: IssueSeverity = "error",
        nodes: Iterable[NodeId] = (),
        colors: Iterable[Color] = (),
    ) -> None:
        self.issues.append(
            ValidationIssue(
                code=code,
                message=message,
                severity=severity,
                nodes=tuple(nodes),
                colors=tuple(colors),
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "stats": dict(self.stats),
        }

    def require_valid(self) -> None:
        if self.valid:
            return
        details = "; ".join(issue.message for issue in self.errors[:4])
        if len(self.errors) > 4:
            details += f"; and {len(self.errors) - 4} more error(s)"
        raise ValueError(f"Puzzle validation failed: {details}")


def _connected_components(puzzle: Puzzle) -> Tuple[List[Set[NodeId]], Dict[NodeId, int]]:
    remaining = set(puzzle.graph.nodes)
    components: List[Set[NodeId]] = []
    component_by_node: Dict[NodeId, int] = {}

    while remaining:
        start = min(remaining)
        stack = [start]
        component: Set[NodeId] = set()
        remaining.remove(start)
        while stack:
            node = stack.pop()
            component.add(node)
            for neighbor in puzzle.graph.neighbors(node):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        index = len(components)
        components.append(component)
        for node in component:
            component_by_node[node] = index

    return components, component_by_node


def _bipartite_coloring(puzzle: Puzzle, component: Set[NodeId]) -> Optional[Dict[NodeId, int]]:
    side: Dict[NodeId, int] = {}
    for start in sorted(component):
        if start in side:
            continue
        side[start] = 0
        stack = [start]
        while stack:
            node = stack.pop()
            expected = 1 - side[node]
            for neighbor in puzzle.graph.neighbors(node):
                if neighbor not in component:
                    continue
                previous = side.get(neighbor)
                if previous is None:
                    side[neighbor] = expected
                    stack.append(neighbor)
                elif previous != expected:
                    return None
    return side


def validate_puzzle(puzzle: Puzzle) -> ValidationReport:
    """Validate structural invariants and inexpensive necessary conditions.

    This deliberately does not run a solver. It is safe to use at parse/import
    boundaries and catches common OCR/topology mistakes before constraint
    construction.
    """

    report = ValidationReport()
    graph_nodes = set(puzzle.graph.nodes)
    edge_count = sum(1 for _ in puzzle.graph.edges())
    report.stats.update(
        nodes=len(graph_nodes),
        edges=edge_count,
        tiles=len(puzzle.tiles),
        colors=len(puzzle.terminals),
        fill=puzzle.fill,
    )

    if not graph_nodes:
        report.add("empty_graph", "Puzzle graph has no channels/nodes.")
        return report
    if not puzzle.terminals:
        report.add("no_terminals", "Puzzle must contain at least one terminal pair.")

    node_tiles: Dict[NodeId, List[str]] = {node: [] for node in graph_nodes}
    for tile_id, tile_nodes in puzzle.tiles.items():
        if not tile_nodes:
            report.add("empty_tile", f"Cell/tile {tile_id!r} has no channels.")
            continue
        seen_in_tile: Set[NodeId] = set()
        for node in tile_nodes:
            if node in seen_in_tile:
                report.add(
                    "duplicate_tile_channel",
                    f"Cell/tile {tile_id!r} lists channel {node!r} more than once.",
                    nodes=[node],
                )
                continue
            seen_in_tile.add(node)
            if node not in graph_nodes:
                report.add(
                    "unknown_tile_channel",
                    f"Cell/tile {tile_id!r} references unknown channel {node!r}.",
                    nodes=[node],
                )
                continue
            node_tiles[node].append(tile_id)

    for node, tile_ids in node_tiles.items():
        if not tile_ids:
            report.add(
                "ungrouped_channel",
                f"Channel {node!r} does not belong to a physical cell/tile.",
                nodes=[node],
            )
        elif len(tile_ids) > 1:
            report.add(
                "channel_in_multiple_tiles",
                f"Channel {node!r} belongs to multiple cells/tiles: {tile_ids}.",
                nodes=[node],
            )

    terminal_owner: Dict[NodeId, Color] = {}
    for color, endpoints in puzzle.terminals.items():
        if not isinstance(color, str) or not color:
            report.add("invalid_color", "Terminal color labels must be non-empty strings.")
        if len(endpoints) != 2:
            report.add(
                "terminal_arity",
                f"Terminal {color!r} must have exactly two endpoints.",
                colors=[color],
            )
            continue
        a, b = endpoints
        if a == b:
            report.add(
                "same_terminal_endpoint",
                f"Terminal {color!r} uses the same channel twice ({a!r}).",
                nodes=[a],
                colors=[color],
            )
        for endpoint in (a, b):
            if endpoint not in graph_nodes:
                report.add(
                    "unknown_terminal_channel",
                    f"Terminal {color!r} references unknown channel {endpoint!r}.",
                    nodes=[endpoint],
                    colors=[color],
                )
                continue
            owner = terminal_owner.get(endpoint)
            if owner is not None and owner != color:
                report.add(
                    "shared_terminal_channel",
                    f"Channel {endpoint!r} is a terminal for both {owner!r} and {color!r}.",
                    nodes=[endpoint],
                    colors=[owner, color],
                )
            else:
                terminal_owner[endpoint] = color
            if puzzle.graph.degree(endpoint) < 1:
                report.add(
                    "isolated_terminal",
                    f"Terminal {color!r} endpoint {endpoint!r} has no available adjacency.",
                    nodes=[endpoint],
                    colors=[color],
                )

    components, component_by_node = _connected_components(puzzle)
    report.stats["components"] = len(components)
    # Bridge cells intentionally contain independent horizontal and vertical
    # routing channels. They can split the channel graph while the physical
    # board remains one connected topology, so merge channel components that
    # occupy the same physical tile for structural connectivity reporting.
    component_parent = list(range(len(components)))

    def find_component(index: int) -> int:
        while component_parent[index] != index:
            component_parent[index] = component_parent[component_parent[index]]
            index = component_parent[index]
        return index

    def union_components(left: int, right: int) -> None:
        left_root = find_component(left)
        right_root = find_component(right)
        if left_root != right_root:
            component_parent[right_root] = left_root

    for tile_nodes in puzzle.tiles.values():
        tile_components = {
            component_by_node[node]
            for node in tile_nodes
            if node in component_by_node
        }
        if len(tile_components) > 1:
            first = min(tile_components)
            for other in tile_components:
                union_components(first, other)

    physical_components = len({find_component(index) for index in range(len(components))})
    report.stats["physical_components"] = physical_components
    if physical_components > 1:
        report.add(
            "disconnected_graph",
            f"Topology contains {physical_components} disconnected physical components.",
            severity="warning",
        )

    for color, (a, b) in puzzle.terminals.items():
        if a in component_by_node and b in component_by_node and component_by_node[a] != component_by_node[b]:
            report.add(
                "terminal_pair_disconnected",
                f"Terminal {color!r} endpoints lie in different topology components.",
                nodes=[a, b],
                colors=[color],
            )

    if puzzle.fill:
        for index, component in enumerate(components):
            component_terminals = [node for node in component if node in terminal_owner]
            if component_terminals:
                continue
            # A channel component may be safely unused only when every one of
            # its physical cells has another channel outside this component.
            requires_component = False
            for node in component:
                for tile_id in node_tiles.get(node, []):
                    tile_nodes = set(puzzle.tiles[tile_id])
                    if tile_nodes and tile_nodes <= component:
                        requires_component = True
                        break
                if requires_component:
                    break
            if requires_component:
                report.add(
                    "terminal_free_required_component",
                    f"Required topology component {index} has no terminal endpoints.",
                    nodes=sorted(component),
                )

        # Single-channel cells are necessarily used on a fill-all board.
        for tile_id, tile_nodes in puzzle.tiles.items():
            if len(tile_nodes) != 1 or tile_nodes[0] not in graph_nodes:
                continue
            node = tile_nodes[0]
            required_degree = 1 if node in terminal_owner else 2
            if puzzle.graph.degree(node) < required_degree:
                report.add(
                    "insufficient_channel_degree",
                    f"Required channel {node!r} in cell {tile_id!r} has degree "
                    f"{puzzle.graph.degree(node)}, but needs at least {required_degree}.",
                    nodes=[node],
                    colors=[terminal_owner[node]] if node in terminal_owner else [],
                )

        # On a bipartite topology where every cell has exactly one channel,
        # full coverage fixes the black/white endpoint balance.
        all_channels_required = all(len(nodes) == 1 for nodes in puzzle.tiles.values())
        all_channels_grouped = all(len(tile_ids) == 1 for tile_ids in node_tiles.values())
        if all_channels_required and all_channels_grouped:
            for index, component in enumerate(components):
                side = _bipartite_coloring(puzzle, component)
                if side is None:
                    continue
                side_zero = sum(1 for value in side.values() if value == 0)
                side_one = len(side) - side_zero
                same_zero = 0
                same_one = 0
                for color, (a, b) in puzzle.terminals.items():
                    if a not in component or b not in component:
                        continue
                    if side[a] == side[b] == 0:
                        same_zero += 1
                    elif side[a] == side[b] == 1:
                        same_one += 1
                if same_zero - same_one != side_zero - side_one:
                    report.add(
                        "bipartite_parity",
                        f"Component {index} violates the full-cover bipartite endpoint balance "
                        f"({same_zero} same-side-0 pairs - {same_one} same-side-1 pairs != "
                        f"{side_zero} side-0 cells - {side_one} side-1 cells).",
                        colors=sorted(puzzle.terminals),
                    )

    return report


__all__ = [
    "IssueSeverity",
    "ValidationIssue",
    "ValidationReport",
    "validate_puzzle",
]
