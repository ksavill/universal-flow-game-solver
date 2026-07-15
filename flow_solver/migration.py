from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .graph import NodeId
from .puzzle import Puzzle
from .schema_v2 import (
    AdjacencyEndpoint,
    AdjacencySpec,
    CatalogLevelSpec,
    CatalogPackSpec,
    CatalogSpec,
    CellCoverageOverride,
    CellDisplaySpec,
    CellSpec,
    ChannelDisplaySpec,
    ChannelSpec,
    CoverageSpec,
    DisplaySizeSpec,
    DisplaySpec,
    PortSpec,
    PathRulesSpec,
    PuzzleSpec,
    RulesSpec,
    TemplateSpec,
    TerminalSpec,
    TopologySpec,
    parse_v2_dict,
)

EdgeKind = str


def _canonical_edge(u: NodeId, v: NodeId) -> Tuple[NodeId, NodeId]:
    return (u, v) if u < v else (v, u)


def _nonempty_text(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value or None


def _mapping_value(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    wanted = {name.casefold() for name in names}
    for key, value in mapping.items():
        if str(key).strip().casefold() in wanted:
            return value
    return None


def _terminal_color_entries(raw: Any) -> Dict[str, str]:
    """Parse terminal-color metadata used by legacy files and importers.

    Older ``.flow`` files retain all directive values as strings, while JSON
    importers have historically emitted both maps and lists of color records.
    Pair strings are also accepted for compatibility with hand-authored files,
    for example ``A=#f00; B:#00ff00``.
    """

    if isinstance(raw, Mapping):
        out: Dict[str, str] = {}
        for key, raw_value in raw.items():
            label = _nonempty_text(str(key))
            if label is None:
                continue
            if isinstance(raw_value, Mapping):
                raw_value = _mapping_value(raw_value, ("color", "colour", "value", "hex"))
            value = _nonempty_text(raw_value)
            if value is not None:
                out[label] = value
        return out

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        out: Dict[str, str] = {}
        for item in raw:
            label_raw: Any = None
            value_raw: Any = None
            if isinstance(item, Mapping):
                label_raw = _mapping_value(
                    item,
                    ("letter", "color_id", "colour_id", "id", "key", "label", "name"),
                )
                value_raw = _mapping_value(item, ("color", "colour", "value", "hex"))
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                if len(item) == 2:
                    label_raw, value_raw = item
            label = _nonempty_text(label_raw)
            value = _nonempty_text(value_raw)
            if label is not None and value is not None:
                out[label] = value
        return out

    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if not text:
        return {}

    # Flow directives such as ``# terminal_colors: {"A":"#f00"}``
    # arrive here as a JSON-encoded string rather than as a mapping.
    if text[:1] in {"{", "["}:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (Mapping, list, tuple)):
            return _terminal_color_entries(parsed)

    out: Dict[str, str] = {}
    for part in re.split(r"[;,\r\n]+", text):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" in chunk:
            label_raw, value_raw = chunk.split("=", 1)
        elif ":" in chunk:
            label_raw, value_raw = chunk.split(":", 1)
        else:
            bits = chunk.split(None, 1)
            if len(bits) != 2:
                continue
            label_raw, value_raw = bits
        label = _nonempty_text(label_raw)
        value = _nonempty_text(value_raw)
        if label is not None and value is not None:
            out[label] = value
    return out


def _terminal_colors(meta: Mapping[str, Any]) -> Dict[str, str]:
    for key, raw in meta.items():
        if str(key).strip().casefold() in {"terminal_colors", "terminal_colours"}:
            return _terminal_color_entries(raw)
    return {}


def _terminal_color_for(colors: Mapping[str, str], terminal: str) -> Optional[str]:
    exact = colors.get(terminal)
    if exact is not None:
        return exact
    folded = terminal.casefold()
    return next((value for key, value in colors.items() if key.casefold() == folded), None)


def _display_size(
    meta: Mapping[str, Any], parameters: Mapping[str, Any]
) -> Optional[DisplaySizeSpec]:
    width = parameters.get("width", parameters.get("sectors"))
    height = parameters.get("height", parameters.get("rings"))
    width_int = int(width) if isinstance(width, int) and not isinstance(width, bool) else None
    height_int = int(height) if isinstance(height, int) and not isinstance(height, bool) else None
    label: Optional[str] = None
    raw_label = meta.get("size") or meta.get("display_size")
    if isinstance(raw_label, str) and raw_label.strip():
        label = raw_label.strip()
    elif width_int is not None and height_int is not None:
        label = f"{width_int}x{height_int}"
    if label is None and width_int is None and height_int is None:
        return None
    return DisplaySizeSpec(
        label=label,
        width=width_int,
        height=height_int,
        unit="template",
    )


def puzzle_to_spec(
    puzzle: Puzzle,
    *,
    template_id: str = "custom-graph",
    template_parameters: Optional[Mapping[str, Any]] = None,
    edge_kinds: Optional[Mapping[Tuple[NodeId, NodeId], EdgeKind]] = None,
    blocked_edges: Optional[Mapping[Tuple[NodeId, NodeId], EdgeKind]] = None,
    catalog: Optional[CatalogSpec] = None,
) -> PuzzleSpec:
    """Convert a compiled/legacy puzzle into canonical schema v2.

    Legacy formats do not name ports, so deterministic per-edge ports are
    synthesized. The compiled graph, tile grouping, terminal pairs, positions,
    and fill semantics are preserved exactly.
    """

    if puzzle.source_spec is not None:
        return parse_v2_dict(puzzle.source_spec.to_dict())

    parameters = dict(template_parameters or {})
    normalized_edge_kinds = {
        _canonical_edge(str(u), str(v)): str(kind)
        for (u, v), kind in (edge_kinds or {}).items()
    }
    normalized_blocked_edges = {
        _canonical_edge(str(u), str(v)): str(kind)
        for (u, v), kind in (blocked_edges or {}).items()
        if str(u) != str(v)
    }
    nodes = tuple(sorted(puzzle.graph.nodes))
    edges = tuple(sorted(puzzle.graph.edges()))
    open_edge_set = set(edges)
    blocked = tuple(
        edge
        for edge in sorted(normalized_blocked_edges)
        if edge not in open_edge_set
        and edge[0] in puzzle.graph.nodes
        and edge[1] in puzzle.graph.nodes
    )

    node_to_cell: Dict[NodeId, str] = {}
    cells: Dict[str, CellSpec] = {}
    for cell_id, channel_ids in sorted(puzzle.tiles.items()):
        cells[cell_id] = CellSpec(kind="bridge" if len(channel_ids) > 1 else "ordinary")
        for channel_id in channel_ids:
            node_to_cell[channel_id] = cell_id
    for node_id in nodes:
        if node_id not in node_to_cell:
            node_to_cell[node_id] = node_id
            cells[node_id] = CellSpec(kind="ordinary")

    port_ids: Dict[NodeId, Dict[str, PortSpec]] = {node_id: {} for node_id in nodes}
    adjacencies = []
    mechanics = set()
    adjacency_inputs = [(u, v, "open") for u, v in edges]
    adjacency_inputs.extend((u, v, "blocked") for u, v in blocked)
    for index, (u, v, state) in enumerate(adjacency_inputs):
        a_port = f"edge-{index}:a"
        b_port = f"edge-{index}:b"
        port_ids[u][a_port] = PortSpec()
        port_ids[v][b_port] = PortSpec()
        kind = (
            normalized_edge_kinds.get((u, v), "local")
            if state == "open"
            else normalized_blocked_edges[(u, v)]
        )
        if kind not in {"local", "seam", "warp", "custom"}:
            kind = "custom"
        if kind != "local":
            mechanics.add(kind)
        if state == "blocked":
            mechanics.add("walls")
        adjacencies.append(
            AdjacencySpec(
                id=f"edge-{index:04d}",
                a=AdjacencyEndpoint(channel=u, port=a_port),
                b=AdjacencyEndpoint(channel=v, port=b_port),
                kind=kind,
                state=state,
                group=f"{kind}-1" if kind in {"seam", "warp"} else None,
            )
        )

    channels: Dict[str, ChannelSpec] = {}
    channel_display: Dict[str, ChannelDisplaySpec] = {}
    for node_id in nodes:
        node = puzzle.graph.nodes[node_id]
        data = copy.deepcopy(node.data)
        for generated_key in ("tile", "cell", "ports"):
            data.pop(generated_key, None)
        channels[node_id] = ChannelSpec(
            cell=node_to_cell[node_id],
            ports=port_ids[node_id],
            kind=node.kind,
            data=data,
        )
        channel_display[node_id] = ChannelDisplaySpec(position=node.pos)

    cell_display: Dict[str, CellDisplaySpec] = {}
    for cell_id, channel_ids in sorted(puzzle.tiles.items()):
        positions = [
            puzzle.graph.nodes[node].pos
            for node in channel_ids
            if node in puzzle.graph.nodes
        ]
        if not positions:
            continue
        count = float(len(positions))
        cell_display[cell_id] = CellDisplaySpec(
            position=(
                sum(position[0] for position in positions) / count,
                sum(position[1] for position in positions) / count,
                sum(position[2] for position in positions) / count,
            )
        )

    colors = _terminal_colors(puzzle.meta)
    terminals = {
        color: TerminalSpec(endpoints=endpoints, color=_terminal_color_for(colors, color))
        for color, endpoints in sorted(puzzle.terminals.items())
    }

    if any(len(channel_ids) > 1 for channel_ids in puzzle.tiles.values()):
        mechanics.add("bridges")
    if catalog is None:
        pack_raw = puzzle.meta.get("pack")
        level_raw = puzzle.meta.get("level")
        level_number: Optional[int] = None
        if isinstance(level_raw, str) and level_raw.isdigit():
            level_number = int(level_raw)
        catalog = CatalogSpec(
            app=str(puzzle.meta["app"]) if puzzle.meta.get("app") else None,
            variant=str(puzzle.meta["variant"]) if puzzle.meta.get("variant") else None,
            pack=CatalogPackSpec(name=str(pack_raw)) if pack_raw else None,
            level=CatalogLevelSpec(
                id=str(level_raw) if level_raw else None,
                number=level_number,
            )
            if level_raw
            else None,
            display_size=_display_size(puzzle.meta, parameters),
            mechanics=tuple(sorted(mechanics)),
        )

    spec = PuzzleSpec(
        topology=TopologySpec(
            cells=cells,
            channels=channels,
            adjacencies=tuple(adjacencies),
            template=TemplateSpec(id=template_id, parameters=parameters),
        ),
        terminals=terminals,
        rules=RulesSpec(
            coverage=CoverageSpec(
                mode="all-cells" if puzzle.fill else "optional",
                overrides={
                    tile_id: CellCoverageOverride(
                        min_used_channels=minimum,
                        max_used_channels=maximum,
                    )
                    for tile_id in sorted(puzzle.coverage_bounds)
                    for minimum, maximum in [puzzle.cell_coverage_bounds(tile_id)]
                },
            ),
            paths=PathRulesSpec(
                minimum_nodes=puzzle.path_length_bounds[0],
                maximum_nodes=puzzle.path_length_bounds[1],
            ),
            multi_channel_cell_color_policy=puzzle.multi_channel_cell_color_policy,
        ),
        display=DisplaySpec(cells=cell_display, channels=channel_display),
        catalog=catalog,
        meta=copy.deepcopy(puzzle.meta),
    )
    return parse_v2_dict(spec.to_dict())


def infer_legacy_template(path: Path, puzzle: Puzzle) -> Tuple[str, Dict[str, Any]]:
    source = str(puzzle.meta.get("source", "")).lower()
    name = path.name.lower()
    coordinates = []
    for node_id in puzzle.graph.nodes:
        match = re.fullmatch(r"(\d+),(\d+)", node_id)
        if match:
            coordinates.append((int(match.group(1)), int(match.group(2))))
    sectors = max((x for x, _y in coordinates), default=-1) + 1
    has_angular_wrap = sectors >= 3 and any(
        f"{sectors - 1},{y}" in puzzle.graph.neighbors(f"0,{y}")
        for y in {y for _x, y in coordinates}
        if f"0,{y}" in puzzle.graph.nodes and f"{sectors - 1},{y}" in puzzle.graph.nodes
    )
    if "circle" in source or "circle" in name or has_angular_wrap:
        if coordinates:
            rings = max(y for _x, y in coordinates) + 1
            return "ring", {
                "rings": rings,
                "sectors": sectors,
                "core": "core" in puzzle.graph.nodes,
            }
        indexed = [int(node_id) for node_id in puzzle.graph.nodes if node_id.isdigit()]
        return "ring", {"rings": 1, "sectors": max(indexed) + 1} if indexed else {}
    grid_coordinates = []
    for node_id in puzzle.graph.nodes:
        match = re.fullmatch(r"(\d+),(\d+)(?::[hv])?", node_id)
        if match:
            grid_coordinates.append((int(match.group(1)), int(match.group(2))))
    grid_parameters: Dict[str, Any] = {}
    if grid_coordinates:
        grid_parameters = {
            "width": max(x for x, _y in grid_coordinates) + 1,
            "height": max(y for _x, y in grid_coordinates) + 1,
        }
    if any(node.kind.startswith("bridge") for node in puzzle.graph.nodes.values()):
        return "square-grid", {**grid_parameters, "mechanic": "bridges"}
    if grid_coordinates:
        looks_hexagonal = "hex" in source or "hex" in name or any(
            abs(node.pos[0] - round(node.pos[0])) > 1e-6
            or abs(node.pos[1] - round(node.pos[1])) > 1e-6
            for node in puzzle.graph.nodes.values()
        )
        return ("hex-grid" if looks_hexagonal else "square-grid"), grid_parameters
    return "custom-graph", {}


def _legacy_edge_kinds(raw: Any) -> Dict[Tuple[NodeId, NodeId], EdgeKind]:
    """Recover typed enabled edges that the legacy runtime graph flattens.

    ``Puzzle.from_json`` intentionally compiles ``warps`` and generic edge
    additions into ordinary graph edges. Reading these annotations alongside
    the compiled puzzle keeps a file migration from silently turning warps into
    local adjacencies.
    """

    if not isinstance(raw, Mapping):
        return {}
    space = raw.get("space")
    if not isinstance(space, Mapping) or space.get("type", "graph") != "graph":
        return {}

    out: Dict[Tuple[NodeId, NodeId], EdgeKind] = {}

    def add_pairs(value: Any, kind: EdgeKind) -> None:
        if not isinstance(value, list):
            return
        for pair in value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            u, v = str(pair[0]), str(pair[1])
            if u and v and u != v:
                out[_canonical_edge(u, v)] = kind

    overrides = space.get("edge_overrides")
    if isinstance(overrides, Mapping):
        add_pairs(overrides.get("add"), "custom")
    # Backend imports may include a warp in both ``edge_overrides.add`` and the
    # more specific alias; the specific annotation must win.
    add_pairs(space.get("warps"), "warp")
    return out


def _legacy_blocked_edges(raw: Any) -> Dict[Tuple[NodeId, NodeId], EdgeKind]:
    if not isinstance(raw, Mapping):
        return {}
    space = raw.get("space")
    if not isinstance(space, Mapping) or space.get("type", "graph") != "graph":
        return {}

    out: Dict[Tuple[NodeId, NodeId], EdgeKind] = {}

    def add_pairs(value: Any) -> None:
        if not isinstance(value, list):
            return
        for pair in value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            u, v = str(pair[0]), str(pair[1])
            if u and v and u != v:
                out[_canonical_edge(u, v)] = "local"

    overrides = space.get("edge_overrides")
    if isinstance(overrides, Mapping):
        add_pairs(overrides.get("remove"))
    add_pairs(space.get("walls"))
    return out


def migrate_file(
    source_path: str | Path,
    output_path: str | Path,
    *,
    template_id: Optional[str] = None,
) -> Path:
    source = Path(source_path)
    output = Path(output_path)
    edge_kinds: Dict[Tuple[NodeId, NodeId], EdgeKind] = {}
    blocked_edges: Dict[Tuple[NodeId, NodeId], EdgeKind] = {}
    if source.suffix.lower() == ".json":
        source_text = source.read_text(encoding="utf-8")
        puzzle = Puzzle.from_json(source_text)
        try:
            raw_document = json.loads(source_text)
            edge_kinds = _legacy_edge_kinds(raw_document)
            blocked_edges = _legacy_blocked_edges(raw_document)
        except (TypeError, ValueError):
            # Puzzle.from_json above owns the user-facing parse error. This
            # fallback is only defensive for unusual JSON decoder behavior.
            edge_kinds = {}
            blocked_edges = {}
    else:
        puzzle = Puzzle.from_file(source)
    inferred_id, parameters = infer_legacy_template(source, puzzle)
    if inferred_id == "ring" and isinstance(parameters.get("sectors"), int):
        sectors = int(parameters["sectors"])
        for u, v in puzzle.graph.edges():
            left = re.fullmatch(r"(\d+),(\d+)", u)
            right = re.fullmatch(r"(\d+),(\d+)", v)
            if left and right:
                ux, uy = int(left.group(1)), int(left.group(2))
                vx, vy = int(right.group(1)), int(right.group(2))
                if uy == vy and abs(ux - vx) == sectors - 1:
                    edge_kinds[_canonical_edge(u, v)] = "seam"
            elif u.isdigit() and v.isdigit() and abs(int(u) - int(v)) == sectors - 1:
                edge_kinds[_canonical_edge(u, v)] = "seam"
    spec = puzzle_to_spec(
        puzzle,
        template_id=template_id or inferred_id,
        template_parameters=parameters,
        edge_kinds=edge_kinds,
        blocked_edges=blocked_edges,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(spec.to_json(), encoding="utf-8")
    return output


__all__ = ["infer_legacy_template", "migrate_file", "puzzle_to_spec"]
