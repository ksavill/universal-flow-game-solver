from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..graph import NodeId
from ..puzzle import Color, Puzzle


_PALETTE = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#7f7f7f",  # gray
    "#bcbd22",  # olive
    "#17becf",  # cyan
]


def build_plotly_figure(
    puzzle: Puzzle,
    *,
    node_color: Optional[Dict[NodeId, Optional[Color]]] = None,
    path_edges: Optional[Mapping[Color, Sequence[Tuple[NodeId, NodeId]]]] = None,
    title: str = "Universal Flow Game Solver",
    use_3d: bool = False,
):
    import plotly.graph_objects as go

    colors = puzzle.all_colors()
    color_to_hex = {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(colors)}

    # Base edges (light)
    ex, ey, ez = [], [], []
    for u, v in puzzle.graph.edges():
        pu = puzzle.graph.nodes[u].pos
        pv = puzzle.graph.nodes[v].pos
        ex += [pu[0], pv[0], None]
        ey += [pu[1], pv[1], None]
        ez += [pu[2], pv[2], None]

    # Explicit selected edges are required: equal endpoint colors do not imply
    # that a touching/chord adjacency belongs to the path.
    sol_edges = [
        (u, v, color)
        for color, edges in (path_edges or {}).items()
        for u, v in edges
    ]

    # Nodes
    nx, ny, nz, ntext, ncolor, nsize = [], [], [], [], [], []
    terminals = puzzle.terminal_nodes()
    for node_id, node in puzzle.graph.nodes.items():
        nx.append(node.pos[0])
        ny.append(node.pos[1])
        nz.append(node.pos[2])

        label_bits = [f"id={node_id}", f"kind={node.kind}"]
        if "tile" in node.data:
            label_bits.append(f"tile={node.data['tile']}")
        if node_id in terminals:
            label_bits.append(f"terminal={terminals[node_id]}")
        ntext.append("<br>".join(label_bits))

        if node_color is not None and node_color.get(node_id) is not None:
            ncolor.append(color_to_hex[node_color[node_id]])  # type: ignore[index]
        elif node_id in terminals:
            ncolor.append(color_to_hex[terminals[node_id]])
        else:
            ncolor.append("#cccccc")

        nsize.append(12 if node_id in terminals else 7)

    traces = []
    if use_3d:
        traces.append(
            go.Scatter3d(
                x=ex,
                y=ey,
                z=ez,
                mode="lines",
                line=dict(width=2, color="rgba(160,160,160,0.35)"),
                hoverinfo="none",
                name="edges",
            )
        )
        for u, v, c in sol_edges:
            pu = puzzle.graph.nodes[u].pos
            pv = puzzle.graph.nodes[v].pos
            traces.append(
                go.Scatter3d(
                    x=[pu[0], pv[0]],
                    y=[pu[1], pv[1]],
                    z=[pu[2], pv[2]],
                    mode="lines",
                    line=dict(width=8, color=color_to_hex[c]),
                    hoverinfo="none",
                    showlegend=False,
                )
            )
        traces.append(
            go.Scatter3d(
                x=nx,
                y=ny,
                z=nz,
                mode="markers",
                marker=dict(size=nsize, color=ncolor, line=dict(width=0)),
                text=ntext,
                hoverinfo="text",
                name="nodes",
            )
        )
        fig = go.Figure(data=traces)
        fig.update_layout(
            title=title,
            scene=dict(
                xaxis=dict(visible=False),
                yaxis=dict(visible=False),
                zaxis=dict(visible=False),
            ),
            margin=dict(l=0, r=0, t=40, b=0),
        )
    else:
        traces.append(
            go.Scatter(
                x=ex,
                y=ey,
                mode="lines",
                line=dict(width=1, color="rgba(160,160,160,0.5)"),
                hoverinfo="none",
                name="edges",
            )
        )
        for u, v, c in sol_edges:
            pu = puzzle.graph.nodes[u].pos
            pv = puzzle.graph.nodes[v].pos
            traces.append(
                go.Scatter(
                    x=[pu[0], pv[0]],
                    y=[pu[1], pv[1]],
                    mode="lines",
                    line=dict(width=6, color=color_to_hex[c]),
                    hoverinfo="none",
                    showlegend=False,
                )
            )
        traces.append(
            go.Scatter(
                x=nx,
                y=ny,
                mode="markers",
                marker=dict(size=nsize, color=ncolor, line=dict(width=0)),
                text=ntext,
                hoverinfo="text",
                name="nodes",
            )
        )
        fig = go.Figure(data=traces)
        fig.update_layout(
            title=title,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
            margin=dict(l=0, r=0, t=40, b=0),
        )

    return fig


def write_plotly_html(
    puzzle: Puzzle,
    *,
    out_path: str | Path,
    node_color: Optional[Dict[NodeId, Optional[Color]]] = None,
    path_edges: Optional[Mapping[Color, Sequence[Tuple[NodeId, NodeId]]]] = None,
    title: str = "Universal Flow Game Solver",
    use_3d: bool = False,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig = build_plotly_figure(
        puzzle,
        node_color=node_color,
        path_edges=path_edges,
        title=title,
        use_3d=use_3d,
    )
    fig.write_html(str(out_path), include_plotlyjs="cdn", full_html=True)
    return out_path

