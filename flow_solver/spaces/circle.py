from __future__ import annotations

import math
from collections import defaultdict
from typing import DefaultDict, Dict, List, Sequence, Tuple

from ..graph import Graph, Node, NodeId

Color = str


def _circle_theta(index: int, total: int) -> float:
    """Angle convention shared with frontend/editor + image import.

    - sector 0 starts at the top boundary (12 o'clock)
    - node positions are at sector centers, so index uses a +0.5 offset
    - sector index increases clockwise
    """
    if total <= 0:
        return 0.0
    return (math.pi / 2.0) - (2.0 * math.pi * (float(index) + 0.5) / float(total))


def build_circle_space_from_tokens(
    tokens: Sequence[str],
) -> Tuple[Graph, Dict[str, List[NodeId]], Dict[Color, Tuple[NodeId, NodeId]]]:
    """Build a 1D ring (circular) space from a token sequence.

    - Nodes are arranged on a circle and connected to their immediate neighbors.
    - The last node connects back to the first (wrap-around).

    Supported tokens:
    - '.' empty node
    - '#' hole (no node at this position; breaks the ring at that gap)
    - 'A'-'Z' terminals (each must appear exactly twice)
    """

    n = len(tokens)
    if n == 0:
        raise ValueError("circle token list is empty")

    g = Graph()
    tiles: Dict[str, List[NodeId]] = {}
    present: Dict[int, NodeId] = {}
    terminal_locs: DefaultDict[Color, List[NodeId]] = defaultdict(list)

    r = max(1.0, float(n) / (2.0 * math.pi))  # ~1 unit spacing along arc

    for i, tok_raw in enumerate(tokens):
        tok = str(tok_raw)
        if tok == "#":
            continue

        node_id = str(i)
        theta = _circle_theta(i, n)
        pos = (r * math.cos(theta), r * math.sin(theta), 0.0)

        if len(tok) == 1 and tok.isalpha() and tok.upper() == tok:
            g.add_node(Node(id=node_id, pos=pos, kind="terminal", data={"tile": node_id, "color": tok}))
            terminal_locs[tok].append(node_id)
        else:
            g.add_node(Node(id=node_id, pos=pos, kind="cell", data={"tile": node_id, "token": tok}))

        tiles[node_id] = [node_id]
        present[i] = node_id

    # Connect neighbors (i <-> i+1) and wrap-around (n-1 <-> 0) if both exist.
    for i, u in present.items():
        j = (i + 1) % n
        v = present.get(j)
        if v is not None:
            g.add_edge(u, v)

    terminals: Dict[Color, Tuple[NodeId, NodeId]] = {}
    for color, locs in terminal_locs.items():
        if len(locs) != 2:
            raise ValueError(f"Terminal {color!r} must appear exactly twice (found {len(locs)})")
        terminals[color] = (locs[0], locs[1])

    if not terminals:
        raise ValueError("No terminals found (need at least one A-Z pair)")

    return g, tiles, terminals


def build_circle_space_from_token_rows(
    token_rows: Sequence[Sequence[str]],
    *,
    core: bool = False,
) -> Tuple[Graph, Dict[str, List[NodeId]], Dict[Color, Tuple[NodeId, NodeId]]]:
    """Build a circular space from a 2D token grid.

    Interpretation:
    - Each **row** is a concentric ring (inner -> outer).
    - Each **column** is an angular sector.
    - Within a ring, columns wrap around (circular adjacency).
    - Between rings, cells connect radially to the same column in the adjacent ring.
    - If `core=True`, we add a single center node connected to all cells in the innermost ring.

    Supported tokens:
    - '.' empty
    - '#' hole (no node at this position)
    - 'A'-'Z' terminals (each must appear exactly twice)
    """

    rings = len(token_rows)
    if rings == 0:
        raise ValueError("circle token grid is empty")

    width = len(token_rows[0])
    if width == 0:
        raise ValueError("circle token grid has empty rows")
    if any(len(r) != width for r in token_rows):
        raise ValueError("All circle rows (rings) must have the same width")

    g = Graph()
    tiles: Dict[str, List[NodeId]] = {}
    present: Dict[Tuple[int, int], NodeId] = {}
    terminal_locs: DefaultDict[Color, List[NodeId]] = defaultdict(list)

    # Choose a base radius so adjacent sectors are ~1 unit apart on the inner ring.
    base_r = max(1.0, float(width) / (2.0 * math.pi))
    dr = 1.0

    for y in range(rings):
        for x in range(width):
            tok = str(token_rows[y][x])
            if tok == "#":
                continue

            node_id = f"{x},{y}"
            tile = node_id

            r = base_r + float(y) * dr
            theta = _circle_theta(x, width)
            pos = (r * math.cos(theta), r * math.sin(theta), 0.0)

            if len(tok) == 1 and tok.isalpha() and tok.upper() == tok:
                g.add_node(Node(id=node_id, pos=pos, kind="terminal", data={"tile": tile, "color": tok}))
                terminal_locs[tok].append(node_id)
            else:
                g.add_node(Node(id=node_id, pos=pos, kind="cell", data={"tile": tile, "token": tok}))

            tiles[tile] = [node_id]
            present[(x, y)] = node_id

    # Angular adjacency (wrap-around)
    for (x, y), u in list(present.items()):
        v = present.get(((x + 1) % width, y))
        if v is not None:
            g.add_edge(u, v)

    # Radial adjacency
    for (x, y), u in list(present.items()):
        v = present.get((x, y + 1))
        if v is not None:
            g.add_edge(u, v)

    if core:
        core_id = "core"
        g.add_node(Node(id=core_id, pos=(0.0, 0.0, 0.0), kind="core", data={"tile": core_id}))
        tiles[core_id] = [core_id]

        for x in range(width):
            v = present.get((x, 0))
            if v is not None:
                g.add_edge(core_id, v)

    terminals: Dict[Color, Tuple[NodeId, NodeId]] = {}
    for color, locs in terminal_locs.items():
        if len(locs) != 2:
            raise ValueError(f"Terminal {color!r} must appear exactly twice (found {len(locs)})")
        terminals[color] = (locs[0], locs[1])

    if not terminals:
        raise ValueError("No terminals found (need at least one A-Z pair)")

    return g, tiles, terminals
