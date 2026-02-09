from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .graph import Graph, Node, NodeId
from .spaces.circle import build_circle_space_from_token_rows
from .spaces.hex import build_hex_space_from_tokens
from .spaces.square import build_square_space_from_tokens

Color = str


def _edge_pairs_from_json(raw: Any, *, field: str) -> List[Tuple[NodeId, NodeId]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list of [u, v] edge pairs")

    out: List[Tuple[NodeId, NodeId]] = []
    for idx, pair in enumerate(raw):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{field}[{idx}] must be [u, v]")
        u = str(pair[0])
        v = str(pair[1])
        if not u or not v:
            raise ValueError(f"{field}[{idx}] has an empty endpoint")
        if u == v:
            raise ValueError(f"{field}[{idx}] has a self-loop ({u!r})")
        out.append((u, v))
    return out


@dataclass
class Puzzle:
    """A Flow/Numberlink puzzle defined on a graph.

    - `graph` defines connectivity.
    - `tiles` groups 1+ internal nodes into one “physical space”.
      (e.g. a bridge tile has 2 internal nodes: horizontal + vertical)
    - `terminals` maps color labels -> (start_node, end_node).
    """

    graph: Graph
    tiles: Dict[str, List[NodeId]]
    terminals: Dict[Color, Tuple[NodeId, NodeId]]
    fill: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)

    def all_colors(self) -> List[Color]:
        return sorted(self.terminals.keys())

    def terminal_nodes(self) -> Dict[NodeId, Color]:
        out: Dict[NodeId, Color] = {}
        for color, (a, b) in self.terminals.items():
            out[a] = color
            out[b] = color
        return out

    @staticmethod
    def from_file(path: str | Path) -> "Puzzle":
        path = Path(path)
        if path.suffix.lower() == ".json":
            return Puzzle.from_json(path.read_text(encoding="utf-8"))
        return Puzzle.from_flow_text(path.read_text(encoding="utf-8"), source_name=str(path))

    @staticmethod
    def from_json(text: str) -> "Puzzle":
        obj = json.loads(text)
        kind = obj.get("space", {}).get("type", "graph")
        fill = bool(obj.get("fill", True))

        if kind == "graph":
            space = obj["space"]
            g = Graph()
            tiles: Dict[str, List[NodeId]] = {}

            for node_id, nd in space["nodes"].items():
                pos = tuple(nd.get("pos", [0.0, 0.0, 0.0]))
                if len(pos) == 2:
                    pos = (float(pos[0]), float(pos[1]), 0.0)
                else:
                    pos = (float(pos[0]), float(pos[1]), float(pos[2]))
                kind_ = nd.get("kind", "cell")
                data = dict(nd.get("data", {}))
                g.add_node(Node(id=str(node_id), pos=pos, kind=kind_, data=data))

            for u, v in space.get("edges", []):
                g.add_edge(str(u), str(v))

            edge_adds: List[Tuple[NodeId, NodeId]] = []
            edge_removes: List[Tuple[NodeId, NodeId]] = []

            overrides = space.get("edge_overrides", {})
            if overrides is not None:
                if not isinstance(overrides, dict):
                    raise ValueError("space.edge_overrides must be an object with optional add/remove lists")
                edge_adds.extend(_edge_pairs_from_json(overrides.get("add"), field="space.edge_overrides.add"))
                edge_removes.extend(_edge_pairs_from_json(overrides.get("remove"), field="space.edge_overrides.remove"))

            # Convenience aliases:
            # - `warps` add long-range adjacencies
            # - `walls` remove adjacencies
            edge_adds.extend(_edge_pairs_from_json(space.get("warps"), field="space.warps"))
            edge_removes.extend(_edge_pairs_from_json(space.get("walls"), field="space.walls"))

            for u, v in edge_removes:
                g.remove_edge(u, v)
            for u, v in edge_adds:
                g.add_edge(u, v)

            # Tiles default to 1:1 with nodes unless specified.
            tiles_obj = obj.get("tiles")
            if tiles_obj is None:
                for node_id in g.nodes:
                    tiles[node_id] = [node_id]
            else:
                tiles = {str(tid): [str(nid) for nid in nids] for tid, nids in tiles_obj.items()}

            terminals: Dict[Color, Tuple[NodeId, NodeId]] = {}
            for color, pair in obj["terminals"].items():
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError(f"Terminal pair for {color!r} must be a list of 2 node ids")
                terminals[str(color)] = (str(pair[0]), str(pair[1]))

            return Puzzle(graph=g, tiles=tiles, terminals=terminals, fill=fill, meta=dict(obj.get("meta", {})))

        if kind == "square":
            # JSON square space: {type:"square", grid:[["A",".",...], ...]}
            space = obj["space"]
            grid = space["grid"]
            return Puzzle.from_flow_grid_tokens(grid, fill=fill, meta=dict(obj.get("meta", {})))

        raise ValueError(f"Unsupported space type in JSON: {kind!r}")

    @staticmethod
    def from_flow_text(text: str, *, source_name: str = "<text>") -> "Puzzle":
        lines = [ln.rstrip("\n") for ln in text.splitlines()]
        grid_lines: List[str] = []
        meta: Dict[str, Any] = {"source": source_name}
        fill = True
        board_type = "square"

        for ln in lines:
            raw = ln.strip()
            if not raw:
                continue
            # IMPORTANT: In `.flow`, `#` is also a *grid token* meaning “hole”.
            # Parsing rules:
            # - Lines like "# key: value" are directives/metadata.
            # - Lines starting with "# " (hash + whitespace) but without ":" are comments (ignored).
            # - Lines starting with "#" but NOT followed by whitespace are treated as grid rows
            #   (so rows like "#B#" work as expected).
            if raw.startswith("#"):
                hdr = raw[1:].strip()
                if ":" in hdr:
                    k, v = [x.strip() for x in hdr.split(":", 1)]
                    if k.lower() == "type":
                        board_type = v.lower()
                    elif k.lower() == "fill":
                        fill = v.lower() in {"1", "true", "yes", "y", "on"}
                    else:
                        meta[k] = v
                    continue
                if len(raw) >= 2 and raw[1].isspace():
                    continue
            grid_lines.append(ln)

        # tokenization:
        # - if any row contains spaces, treat as whitespace-separated tokens
        # - else treat each character as a token
        token_rows: List[List[str]] = []
        for row in grid_lines:
            if " " in row.strip():
                toks = [t for t in row.strip().split() if t]
            else:
                toks = list(row.strip())
            if toks:
                token_rows.append(toks)

        if not token_rows:
            raise ValueError("No grid found in .flow file")

        width = max(len(r) for r in token_rows)
        for r in token_rows:
            if len(r) != width:
                raise ValueError("All grid rows must have the same width in .flow")

        if board_type == "square":
            g, tiles, terminals = build_square_space_from_tokens(token_rows)
            return Puzzle(graph=g, tiles=tiles, terminals=terminals, fill=fill, meta=meta)

        if board_type == "hex":
            g, tiles, terminals = build_hex_space_from_tokens(token_rows)
            return Puzzle(graph=g, tiles=tiles, terminals=terminals, fill=fill, meta=meta)

        if board_type == "circle":
            core = str(meta.get("core", "false")).lower() in {"1", "true", "yes", "y", "on"}
            g, tiles, terminals = build_circle_space_from_token_rows(token_rows, core=core)
            return Puzzle(graph=g, tiles=tiles, terminals=terminals, fill=fill, meta=meta)

        raise ValueError(
            f"Unsupported '# type: {board_type}' in .flow (supported: square, hex, circle)"
        )

    @staticmethod
    def from_flow_grid_tokens(
        token_rows: Sequence[Sequence[str]],
        *,
        fill: bool = True,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Puzzle":
        g, tiles, terminals = build_square_space_from_tokens(token_rows)
        return Puzzle(graph=g, tiles=tiles, terminals=terminals, fill=fill, meta=meta or {})

