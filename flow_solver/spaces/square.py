from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, List, Sequence, Tuple

from ..graph import Graph, Node, NodeId

Color = str


def _cell_id(x: int, y: int) -> str:
    return f"{x},{y}"


def build_square_space_from_tokens(
    token_rows: Sequence[Sequence[str]],
    *,
    require_terminals: bool = True,
) -> Tuple[Graph, Dict[str, List[NodeId]], Dict[Color, Tuple[NodeId, NodeId]]]:
    """Build a square-grid space from a 2D token grid.

    Supported tokens:
    - '.' empty cell
    - '#' hole (no cell)
    - '+' bridge tile (2 internal nodes: horizontal + vertical)
    - 'A'-'Z' terminals (each must appear exactly twice)
    """

    height = len(token_rows)
    if height == 0:
        raise ValueError("token_rows is empty")
    width = len(token_rows[0])
    if any(len(r) != width for r in token_rows):
        raise ValueError("All rows must have equal width")

    g = Graph()
    tiles: Dict[str, List[NodeId]] = {}

    # For each physical cell, map each direction to the internal node it uses.
    # Normal tiles map all directions to the same node.
    ports: Dict[Tuple[int, int], Dict[str, NodeId]] = {}

    terminal_locs: DefaultDict[Color, List[NodeId]] = defaultdict(list)

    for y in range(height):
        for x in range(width):
            tok = str(token_rows[y][x])
            if tok == "#":
                continue

            tile = _cell_id(x, y)

            # Use a y-up coordinate system for nicer plots.
            base_pos = (float(x), float(-y), 0.0)

            if tok == "+":
                h_id = f"{tile}:h"
                v_id = f"{tile}:v"
                g.add_node(Node(id=h_id, pos=(base_pos[0], base_pos[1], 0.15), kind="bridge_h", data={"tile": tile}))
                g.add_node(Node(id=v_id, pos=(base_pos[0], base_pos[1], -0.15), kind="bridge_v", data={"tile": tile}))
                tiles[tile] = [h_id, v_id]
                ports[(x, y)] = {"N": v_id, "S": v_id, "E": h_id, "W": h_id}
                continue

            if len(tok) == 1 and tok.isalpha() and tok.upper() == tok:
                node_id = tile
                g.add_node(
                    Node(
                        id=node_id,
                        pos=base_pos,
                        kind="terminal",
                        data={"tile": tile, "color": tok},
                    )
                )
                tiles[tile] = [node_id]
                ports[(x, y)] = {"N": node_id, "S": node_id, "E": node_id, "W": node_id}
                terminal_locs[tok].append(node_id)
                continue

            # Treat everything else as an empty traversable cell.
            node_id = tile
            g.add_node(Node(id=node_id, pos=base_pos, kind="cell", data={"tile": tile, "token": tok}))
            tiles[tile] = [node_id]
            ports[(x, y)] = {"N": node_id, "S": node_id, "E": node_id, "W": node_id}

    # Add edges between adjacent physical cells.
    for y in range(height):
        for x in range(width):
            if (x, y) not in ports:
                continue

            # East
            if x + 1 < width and (x + 1, y) in ports:
                u = ports[(x, y)]["E"]
                v = ports[(x + 1, y)]["W"]
                g.add_edge(u, v)

            # South
            if y + 1 < height and (x, y + 1) in ports:
                u = ports[(x, y)]["S"]
                v = ports[(x, y + 1)]["N"]
                g.add_edge(u, v)

    # Validate terminals: each letter must appear exactly twice.
    terminals: Dict[Color, Tuple[NodeId, NodeId]] = {}
    for color, locs in terminal_locs.items():
        if len(locs) != 2:
            raise ValueError(f"Terminal {color!r} must appear exactly twice (found {len(locs)})")
        terminals[color] = (locs[0], locs[1])

    if not terminals and require_terminals:
        raise ValueError("No terminals found (need at least one A-Z pair)")

    return g, tiles, terminals



