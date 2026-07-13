from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from ..graph import NodeId
from ..puzzle import Color, Puzzle
from .types import PathEdge, SolveResult


@dataclass(frozen=True)
class SolutionValidationReport:
    """Independent verification of a proposed set of Flow paths."""

    errors: Tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    def require_valid(self) -> None:
        if self.valid:
            return
        details = "; ".join(self.errors[:4])
        if len(self.errors) > 4:
            details += f"; and {len(self.errors) - 4} more error(s)"
        raise ValueError(f"Solution validation failed: {details}")


def validate_solution(puzzle: Puzzle, result: SolveResult) -> SolutionValidationReport:
    """Validate paths without inferring selected edges from node colors.

    This intentionally acts as an implementation-independent boundary check:
    it validates endpoint ownership, simple/adjacent paths, channel and tile
    capacity, fill policy, explicit path edges, and the node-color projection.
    """

    errors: List[str] = []
    graph_nodes = set(puzzle.graph.nodes)
    expected_colors = set(puzzle.terminals)

    path_colors = set(result.paths)
    if path_colors != expected_colors:
        missing = sorted(expected_colors - path_colors)
        extra = sorted(path_colors - expected_colors)
        if missing:
            errors.append(f"Missing paths for colors: {missing}")
        if extra:
            errors.append(f"Paths contain unknown colors: {extra}")

    unknown_color_nodes = sorted(set(result.node_color) - graph_nodes)
    if unknown_color_nodes:
        errors.append(f"node_color contains unknown channels: {unknown_color_nodes}")
    missing_color_nodes = sorted(graph_nodes - set(result.node_color))
    if missing_color_nodes:
        errors.append(f"node_color omits graph channels: {missing_color_nodes}")

    terminal_owner: Dict[NodeId, Color] = {}
    for color, (a, b) in puzzle.terminals.items():
        terminal_owner[a] = color
        terminal_owner[b] = color

    occupied: Dict[NodeId, Color] = {}
    selected_edges: Dict[Tuple[NodeId, NodeId], Color] = {}

    for color in sorted(expected_colors):
        path = result.paths.get(color)
        if path is None:
            continue
        a, b = puzzle.terminals[color]
        if len(path) < 2:
            errors.append(f"Path {color!r} has fewer than two channels")
            continue
        if (path[0], path[-1]) not in {(a, b), (b, a)}:
            errors.append(
                f"Path {color!r} must run between {a!r} and {b!r}, "
                f"not {path[0]!r} and {path[-1]!r}"
            )

        seen: Set[NodeId] = set()
        for index, node in enumerate(path):
            if node not in graph_nodes:
                errors.append(f"Path {color!r} references unknown channel {node!r}")
                continue
            if node in seen:
                errors.append(f"Path {color!r} repeats channel {node!r}")
            seen.add(node)

            owner = terminal_owner.get(node)
            if owner is not None and not (
                owner == color and node in {path[0], path[-1]}
            ):
                errors.append(
                    f"Path {color!r} traverses terminal channel {node!r} owned by {owner!r}"
                )

            previous_color = occupied.get(node)
            if previous_color is not None and previous_color != color:
                errors.append(
                    f"Channel {node!r} is shared by paths {previous_color!r} and {color!r}"
                )
            else:
                occupied[node] = color

            if index == 0:
                continue
            previous = path[index - 1]
            if previous not in graph_nodes or node not in puzzle.graph.neighbors(previous):
                errors.append(
                    f"Path {color!r} uses non-edge {previous!r} -> {node!r}"
                )
                continue
            canonical = (previous, node) if previous < node else (node, previous)
            previous_edge_color = selected_edges.get(canonical)
            if previous_edge_color is not None and previous_edge_color != color:
                errors.append(
                    f"Edge {canonical!r} is shared by paths "
                    f"{previous_edge_color!r} and {color!r}"
                )
            else:
                selected_edges[canonical] = color

    for node in sorted(graph_nodes):
        expected = occupied.get(node)
        actual = result.node_color.get(node)
        if actual != expected:
            errors.append(
                f"node_color[{node!r}] is {actual!r}, but paths imply {expected!r}"
            )

    node_to_tiles: Dict[NodeId, List[str]] = {node: [] for node in graph_nodes}
    for tile_id, tile_nodes in puzzle.tiles.items():
        for node in tile_nodes:
            if node in node_to_tiles:
                node_to_tiles[node].append(tile_id)

        used = [node for node in tile_nodes if node in occupied]
        if puzzle.fill and not used:
            errors.append(f"Required cell/tile {tile_id!r} is unfilled")

        by_color: Dict[Color, int] = {}
        for node in used:
            color = occupied[node]
            by_color[color] = by_color.get(color, 0) + 1
        for color, count in by_color.items():
            if count > 1:
                errors.append(
                    f"Path {color!r} occupies {count} channels in cell/tile {tile_id!r}"
                )

    for node, tile_ids in node_to_tiles.items():
        if len(tile_ids) != 1:
            errors.append(
                f"Channel {node!r} belongs to {len(tile_ids)} cells/tiles; expected exactly one"
            )

    # ``path_edges`` was added compatibly and may be absent on older/external
    # SolveResult objects.  Once supplied, require it to describe every path.
    if result.path_edges:
        edge_colors = set(result.path_edges)
        if edge_colors != expected_colors:
            errors.append(
                "path_edges color keys do not match puzzle terminal colors "
                f"(got {sorted(edge_colors)}, expected {sorted(expected_colors)})"
            )
        for color in sorted(expected_colors):
            path = result.paths.get(color, [])
            expected: List[PathEdge] = list(zip(path, path[1:]))
            actual = result.path_edges.get(color)
            if actual is not None and actual != expected:
                errors.append(
                    f"path_edges for {color!r} do not match consecutive path channels"
                )

    return SolutionValidationReport(errors=tuple(errors))


__all__ = ["SolutionValidationReport", "validate_solution"]
