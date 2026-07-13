from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple

from ..graph import NodeId
from ..puzzle import Color, Puzzle
from .types import SolveResult
from .validation import validate_solution


class DfsTimeoutError(ValueError):
    pass


def solve_with_dfs(puzzle: Puzzle, *, timeout_ms: int | None = 30_000) -> SolveResult:
    """Solve using a backtracking DFS that grows paths from both terminals."""

    start_time = time.perf_counter()

    nodes = list(puzzle.graph.nodes.keys())
    neighbors: Dict[NodeId, List[NodeId]] = {n: sorted(puzzle.graph.neighbors(n)) for n in nodes}
    node_degree = {n: len(neighbors[n]) for n in nodes}

    node_to_tile: Dict[NodeId, str] = {}
    tile_to_nodes: Dict[str, List[NodeId]] = {}
    for tile_id, tile_nodes in puzzle.tiles.items():
        tile_to_nodes[tile_id] = list(tile_nodes)
        for nid in tile_nodes:
            node_to_tile[nid] = tile_id
    for n in nodes:
        if n not in node_to_tile:
            node_to_tile[n] = n
            tile_to_nodes[n] = [n]

    assigned: Dict[NodeId, Optional[Color]] = {n: None for n in nodes}
    tile_color_usage: Dict[str, Set[Color]] = {tile: set() for tile in tile_to_nodes}
    terminal_nodes: Set[NodeId] = set()

    path_adj: Dict[Color, Dict[NodeId, Set[NodeId]]] = {}
    heads: Dict[Color, List[NodeId]] = {}
    done: Dict[Color, bool] = {}

    def check_timeout() -> None:
        if timeout_ms is None:
            return
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if elapsed_ms > timeout_ms:
            raise DfsTimeoutError(f"DFS solver timed out after {timeout_ms}ms")

    def assign_node(color: Color, node: NodeId) -> None:
        current = assigned[node]
        if current is not None:
            if current != color:
                raise ValueError(f"Node {node!r} already assigned to {current!r}")
            return
        tile = node_to_tile[node]
        if color in tile_color_usage[tile]:
            raise ValueError(f"Tile {tile!r} already used by {color!r}")
        assigned[node] = color
        tile_color_usage[tile].add(color)
        path_adj[color].setdefault(node, set())

    def unassign_node(color: Color, node: NodeId) -> None:
        if node in terminal_nodes:
            raise AssertionError("Attempted to unassign a terminal node")
        if assigned[node] != color:
            raise AssertionError("Unassign mismatch")
        assigned[node] = None
        tile = node_to_tile[node]
        tile_color_usage[tile].discard(color)
        if node in path_adj[color] and not path_adj[color][node]:
            del path_adj[color][node]

    def add_edge(color: Color, a: NodeId, b: NodeId) -> None:
        path_adj[color].setdefault(a, set()).add(b)
        path_adj[color].setdefault(b, set()).add(a)

    def remove_edge(color: Color, a: NodeId, b: NodeId) -> None:
        path_adj[color][a].discard(b)
        path_adj[color][b].discard(a)
        if not path_adj[color][a]:
            del path_adj[color][a]
        if not path_adj[color][b]:
            del path_adj[color][b]

    colors = puzzle.all_colors()
    for color, (a, b) in puzzle.terminals.items():
        if a == b:
            raise ValueError(f"Terminal endpoints for {color!r} must be distinct")
        path_adj[color] = {a: set(), b: set()}
        heads[color] = [a, b]
        done[color] = False
        terminal_nodes.add(a)
        terminal_nodes.add(b)
        assign_node(color, a)
        assign_node(color, b)

    def available_moves(color: Color, head_idx: int) -> List[Tuple[str, NodeId]]:
        head = heads[color][head_idx]
        other = heads[color][1 - head_idx]
        moves: List[Tuple[str, NodeId]] = []
        for nb in neighbors[head]:
            nb_color = assigned[nb]
            if nb_color is None:
                tile = node_to_tile[nb]
                if color in tile_color_usage[tile]:
                    continue
                moves.append(("extend", nb))
            elif nb_color == color and nb == other and not done[color]:
                if other not in path_adj[color].get(head, set()):
                    moves.append(("connect", nb))
        return moves

    def move_sort_key(move: Tuple[str, NodeId]) -> Tuple[int, int]:
        kind, node = move
        if kind == "connect":
            return (1, 0)
        return (0, node_degree[node])

    def can_use_node_for_color(color: Color, node: NodeId) -> bool:
        node_color = assigned[node]
        if node_color is None:
            tile = node_to_tile[node]
            return color not in tile_color_usage[tile]
        return node_color == color

    def heads_reachable(color: Color) -> bool:
        if done[color]:
            return True
        start, target = heads[color]
        if start == target:
            return True
        heads_set = {start, target}
        queue = [start]
        visited = {start}
        while queue:
            cur = queue.pop()
            if cur == target:
                return True
            if assigned[cur] == color and cur not in heads_set:
                nbrs = path_adj[color].get(cur, set())
            else:
                nbrs = neighbors[cur]
            for nb in nbrs:
                if nb in visited:
                    continue
                if not can_use_node_for_color(color, nb):
                    continue
                visited.add(nb)
                queue.append(nb)
        return False

    def all_heads_reachable() -> bool:
        for color in colors:
            if not done[color] and not heads_reachable(color):
                return False
        return True

    def all_tiles_used() -> bool:
        for tile_nodes in tile_to_nodes.values():
            if not any(assigned[n] is not None for n in tile_nodes):
                return False
        return True

    steps = 0

    def search() -> bool:
        nonlocal steps
        steps += 1
        check_timeout()

        if all(done[color] for color in colors):
            if puzzle.fill and not all_tiles_used():
                return False
            return True

        candidates: List[Tuple[int, Color, int, List[Tuple[str, NodeId]]]] = []
        for color in colors:
            if done[color]:
                continue
            moves_a = available_moves(color, 0)
            moves_b = available_moves(color, 1)
            if not moves_a and not moves_b:
                return False
            if moves_a:
                candidates.append((len(moves_a), color, 0, moves_a))
            if moves_b:
                candidates.append((len(moves_b), color, 1, moves_b))

        if not candidates:
            return False

        candidates.sort(key=lambda item: item[0])
        _, color, head_idx, moves = candidates[0]
        for kind, node in sorted(moves, key=move_sort_key):
            if kind == "extend":
                prev_head = heads[color][head_idx]
                assign_node(color, node)
                add_edge(color, prev_head, node)
                heads[color][head_idx] = node
                if all_heads_reachable() and search():
                    return True
                heads[color][head_idx] = prev_head
                remove_edge(color, prev_head, node)
                unassign_node(color, node)
            else:  # connect
                head = heads[color][head_idx]
                other = heads[color][1 - head_idx]
                add_edge(color, head, other)
                done[color] = True
                if search():
                    return True
                done[color] = False
                remove_edge(color, head, other)

        return False

    check_timeout()
    solved = search()
    if not solved:
        raise ValueError("No solution found using DFS solver.")

    node_color = {n: assigned[n] for n in nodes}
    paths: Dict[Color, List[NodeId]] = {}
    for color, (start, goal) in puzzle.terminals.items():
        path: List[NodeId] = [start]
        prev: Optional[NodeId] = None
        cur: NodeId = start
        while cur != goal:
            nexts = [nb for nb in path_adj[color].get(cur, set()) if nb != prev]
            if len(nexts) != 1:
                raise ValueError(
                    f"Cannot uniquely reconstruct path for {color!r} at node {cur!r} "
                    f"(candidates={nexts})."
                )
            nxt = nexts[0]
            path.append(nxt)
            prev, cur = cur, nxt
        paths[color] = path

    result = SolveResult(
        node_color=node_color,
        paths=paths,
        path_edges={color: list(zip(path, path[1:])) for color, path in paths.items()},
        stats={
            "solver": "dfs",
            "steps": steps,
            "total_ms": (time.perf_counter() - start_time) * 1000.0,
            "uniqueness_checked": False,
        },
    )
    validation = validate_solution(puzzle, result)
    if not validation.valid:
        raise RuntimeError(
            "Internal DFS solution failed validation: " + "; ".join(validation.errors[:4])
        )
    check_timeout()
    return result
