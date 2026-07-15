from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, Mapping, Tuple

from .graph import NodeId
from .puzzle import Puzzle

Edge = Tuple[NodeId, NodeId]


@dataclass(frozen=True)
class BridgeSeparator:
    edge: Edge
    left: FrozenSet[NodeId]
    right: FrozenSet[NodeId]


@dataclass(frozen=True)
class TopologyAnalysis:
    schema_hash: str
    incident: Mapping[NodeId, Tuple[int, ...]]
    articulation_components: Mapping[NodeId, Tuple[FrozenSet[NodeId], ...]]
    bridges: Tuple[BridgeSeparator, ...]


def _components_without(
    adjacency: Mapping[NodeId, FrozenSet[NodeId]],
    *,
    removed_node: NodeId | None = None,
    removed_edge: Edge | None = None,
) -> Tuple[FrozenSet[NodeId], ...]:
    unseen = set(adjacency)
    if removed_node is not None:
        unseen.discard(removed_node)
    components = []
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        component = {seed}
        pending = [seed]
        while pending:
            node = pending.pop()
            for neighbor in adjacency[node]:
                if neighbor == removed_node:
                    continue
                if removed_edge is not None and (
                    (node, neighbor) == removed_edge or (neighbor, node) == removed_edge
                ):
                    continue
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        components.append(frozenset(component))
    return tuple(sorted(components, key=lambda value: (min(value), len(value))))


@lru_cache(maxsize=256)
def _analyze_cached(
    nodes: Tuple[NodeId, ...],
    edges: Tuple[Edge, ...],
    tiles: Tuple[Tuple[str, Tuple[NodeId, ...]], ...],
) -> TopologyAnalysis:
    canonical = json.dumps(
        {"nodes": nodes, "edges": edges, "tiles": tiles},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    schema_hash = hashlib.sha256(canonical).hexdigest()

    adjacency_mutable: Dict[NodeId, set[NodeId]] = {node: set() for node in nodes}
    incident_mutable: Dict[NodeId, list[int]] = {node: [] for node in nodes}
    for index, (left, right) in enumerate(edges):
        adjacency_mutable[left].add(right)
        adjacency_mutable[right].add(left)
        incident_mutable[left].append(index)
        incident_mutable[right].append(index)
    adjacency = {node: frozenset(values) for node, values in adjacency_mutable.items()}

    discovery: Dict[NodeId, int] = {}
    low: Dict[NodeId, int] = {}
    parent: Dict[NodeId, NodeId | None] = {}
    articulation_points: set[NodeId] = set()
    bridge_edges: set[Edge] = set()
    clock = 0

    def visit(node: NodeId) -> None:
        nonlocal clock
        discovery[node] = low[node] = clock
        clock += 1
        child_count = 0
        for neighbor in sorted(adjacency[node]):
            if neighbor not in discovery:
                parent[neighbor] = node
                child_count += 1
                visit(neighbor)
                low[node] = min(low[node], low[neighbor])
                if parent.get(node) is None and child_count > 1:
                    articulation_points.add(node)
                if parent.get(node) is not None and low[neighbor] >= discovery[node]:
                    articulation_points.add(node)
                if low[neighbor] > discovery[node]:
                    bridge_edges.add((node, neighbor) if node < neighbor else (neighbor, node))
            elif neighbor != parent.get(node):
                low[node] = min(low[node], discovery[neighbor])

    for node in nodes:
        if node not in discovery:
            parent[node] = None
            visit(node)

    articulation_components = {
        node: _components_without(adjacency, removed_node=node)
        for node in sorted(articulation_points)
    }
    bridge_separators = []
    for edge in sorted(bridge_edges):
        components = _components_without(adjacency, removed_edge=edge)
        if len(components) == 2:
            bridge_separators.append(
                BridgeSeparator(edge=edge, left=components[0], right=components[1])
            )

    return TopologyAnalysis(
        schema_hash=schema_hash,
        incident={node: tuple(indices) for node, indices in incident_mutable.items()},
        articulation_components=articulation_components,
        bridges=tuple(bridge_separators),
    )


def analyze_topology(puzzle: Puzzle) -> TopologyAnalysis:
    nodes = tuple(sorted(puzzle.graph.nodes))
    edges = tuple(sorted(puzzle.graph.edges()))
    tiles = tuple(
        (tile_id, tuple(sorted(channels)))
        for tile_id, channels in sorted(puzzle.tiles.items())
    )
    return _analyze_cached(nodes, edges, tiles)


def topology_cache_info():
    return _analyze_cached.cache_info()


__all__ = [
    "BridgeSeparator",
    "TopologyAnalysis",
    "analyze_topology",
    "topology_cache_info",
]
