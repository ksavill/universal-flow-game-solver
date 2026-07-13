import { Box } from "@mui/material";
import { useMemo } from "react";
import { SolveResponse } from "../api";
import { GAME_PALETTE, buildTerminalColorMaps } from "../colors";
import {
  SolutionPathEdges,
  SolutionPaths,
  buildAdjacencyKinds,
  buildBlockedAdjacencies,
  buildSolutionEdgeColors,
  canonicalEdgeKey
} from "../solutionEdges";

type GameViewProps = {
  graph: SolveResponse["graph"];
  nodeColor?: Record<string, string | null> | null;
  pathEdges?: SolutionPathEdges | null;
  paths?: SolutionPaths | null;
  showSolution?: boolean;
  height?: number;
  cellSize?: number;
  compact?: boolean;
};

type RenderNode = {
  id: string;
  x: number;
  y: number;
  sx: number;
  sy: number;
  kind: string;
  tile: string | null;
  terminalColor: string | null;
  solutionColor: string | null;
};

type RenderTile = {
  key: string;
  sx: number;
  sy: number;
  solutionColor: string | null;
  isBridge: boolean;
};

type RenderBridge = {
  id: string;
  sx: number;
  sy: number;
  horizontalColor: string | null;
  verticalColor: string | null;
};

type RenderEdge = {
  u: string;
  v: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  length: number;
  warpLike: boolean;
  solutionColor: string | null;
};

type RenderBarrier = {
  id: string;
  u: string;
  v: string;
  cx: number;
  cy: number;
  perpendicularX: number;
  perpendicularY: number;
};

type RenderSegment = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

function median(values: number[]): number {
  if (!values.length) {
    return 1;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function buildWarpStubs(edge: RenderEdge, length: number): RenderSegment[] {
  const dx = edge.x2 - edge.x1;
  const dy = edge.y2 - edge.y1;
  const magnitude = Math.hypot(dx, dy) || 1;
  const ux = dx / magnitude;
  const uy = dy / magnitude;

  return [
    {
      x1: edge.x1,
      y1: edge.y1,
      x2: edge.x1 - ux * length,
      y2: edge.y1 - uy * length
    },
    {
      x1: edge.x2,
      y1: edge.y2,
      x2: edge.x2 + ux * length,
      y2: edge.y2 + uy * length
    }
  ];
}

const LATTICE_EPS = 0.02;

function nearInteger(value: number): boolean {
  return Math.abs(value - Math.round(value)) < LATTICE_EPS;
}

export function GameView({
  graph,
  nodeColor,
  pathEdges,
  paths,
  showSolution = false,
  height = 320,
  cellSize,
  compact = false
}: GameViewProps) {
  const { colorToHex, terminalNodeColor } = useMemo(
    () => buildTerminalColorMaps(graph, nodeColor, GAME_PALETTE),
    [graph, nodeColor]
  );
  const solutionEdgeColors = useMemo(
    () => buildSolutionEdgeColors(pathEdges, paths),
    [pathEdges, paths]
  );
  const adjacencyKinds = useMemo(() => buildAdjacencyKinds(graph), [graph]);

  const rendered = useMemo(() => {
    const baseNodes = graph.nodes.map((node) => ({
      id: node.id,
      x: node.x,
      y: node.y,
      kind: node.kind,
      tile: typeof node.data?.tile === "string" ? node.data.tile : null,
      terminalColor: terminalNodeColor[node.id] ?? null,
      solutionColor: showSolution && nodeColor ? nodeColor[node.id] ?? null : null
    }));

    if (!baseNodes.length) {
      return {
        nodes: [] as RenderNode[],
        tiles: [] as RenderTile[],
        bridges: [] as RenderBridge[],
        edges: [] as RenderEdge[],
        barriers: [] as RenderBarrier[],
        gridMode: false,
        width: compact ? 220 : 320,
        height: compact ? 140 : height,
        scale: 24
      };
    }

    const minX = Math.min(...baseNodes.map((node) => node.x));
    const maxX = Math.max(...baseNodes.map((node) => node.x));
    const minY = Math.min(...baseNodes.map((node) => node.y));
    const maxY = Math.max(...baseNodes.map((node) => node.y));
    const spanX = Math.max(0.001, maxX - minX);
    const spanY = Math.max(0.001, maxY - minY);

    const onLattice = baseNodes.every((node) => nearInteger(node.x) && nearInteger(node.y));

    const padding = compact ? 12 : 18;
    const maxWidth = compact ? 320 : 700;
    const viewHeight = Math.max(96, compact ? 140 : height);
    const availableHeight = Math.max(1, viewHeight - padding * 2);
    const availableWidth = Math.max(1, maxWidth - padding * 2);
    // Grid boards reserve half a cell on every side so the outermost cell
    // squares fit inside the canvas (node coords are cell centers).
    const gridSpanX = spanX + 1;
    const gridSpanY = spanY + 1;
    const fitScale = onLattice
      ? Math.min(availableHeight / gridSpanY, availableWidth / gridSpanX)
      : Math.min(availableHeight / spanY, availableWidth / spanX);
    // Free-form imports use screenshot-pixel coordinates rather than grid units.
    // Let those graphs scale below a normal cell size so the canvas never grows
    // thousands of pixels tall. An explicit cell size is also capped to the fit.
    const scale = clamp(Math.min(cellSize ?? fitScale, fitScale), 0.001, 96);
    const contentWidth = (onLattice ? gridSpanX : spanX) * scale;
    const contentHeight = (onLattice ? gridSpanY : spanY) * scale;
    const width = Math.max(140, Math.min(maxWidth, contentWidth + padding * 2));
    const extraX = onLattice ? scale / 2 : 0;
    const extraY = onLattice ? scale / 2 : 0;
    const offsetX = (width - contentWidth) / 2 + extraX;
    const offsetY = (viewHeight - contentHeight) / 2 + extraY;

    const mapX = (x: number) => offsetX + (x - minX) * scale;
    const mapY = (y: number) => offsetY + (maxY - y) * scale;

    const nodes: RenderNode[] = baseNodes.map((node) => ({
      ...node,
      sx: mapX(node.x),
      sy: mapY(node.y)
    }));
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const barriers: RenderBarrier[] = [];
    for (const adjacency of buildBlockedAdjacencies(graph)) {
      const a = nodeById.get(adjacency.u);
      const b = nodeById.get(adjacency.v);
      if (!a || !b) {
        continue;
      }
      const dx = b.sx - a.sx;
      const dy = b.sy - a.sy;
      const magnitude = Math.hypot(dx, dy);
      if (magnitude < 0.001) {
        continue;
      }
      barriers.push({
        id: adjacency.id,
        u: a.id,
        v: b.id,
        cx: (a.sx + b.sx) / 2,
        cy: (a.sy + b.sy) / 2,
        perpendicularX: -dy / magnitude,
        perpendicularY: dx / magnitude
      });
    }
    const bridgeGroups = new Map<string, { horizontal?: RenderNode; vertical?: RenderNode }>();
    for (const node of nodes) {
      if (node.kind !== "bridge_h" && node.kind !== "bridge_v") {
        continue;
      }
      const tile = node.tile ?? node.id.replace(/:[hv]$/, "");
      const group = bridgeGroups.get(tile) ?? {};
      if (node.kind === "bridge_h") {
        group.horizontal = node;
      } else {
        group.vertical = node;
      }
      bridgeGroups.set(tile, group);
    }
    const bridges: RenderBridge[] = Array.from(bridgeGroups.entries()).map(([id, group]) => {
      const anchor = group.horizontal ?? group.vertical!;
      return {
        id,
        sx: anchor.sx,
        sy: anchor.sy,
        horizontalColor: group.horizontal?.solutionColor ?? null,
        verticalColor: group.vertical?.solutionColor ?? null
      };
    });

    const lengths: number[] = [];
    const rawEdges: Array<Omit<RenderEdge, "warpLike">> = [];
    for (const [u, v] of graph.edges) {
      const a = nodeById.get(u);
      const b = nodeById.get(v);
      if (!a || !b) {
        continue;
      }
      const length = Math.hypot(a.x - b.x, a.y - b.y);
      lengths.push(length);
      rawEdges.push({
        u,
        v,
        x1: a.sx,
        y1: a.sy,
        x2: b.sx,
        y2: b.sy,
        length,
        solutionColor: showSolution
          ? solutionEdgeColors.get(canonicalEdgeKey(u, v)) ?? null
          : null
      });
    }

    const medianLength = Math.max(0.001, median(lengths));
    const hasTypedAdjacencies = adjacencyKinds.size > 0;
    const edges: RenderEdge[] = rawEdges.map((edge) => {
      const kind = adjacencyKinds.get(canonicalEdgeKey(edge.u, edge.v));
      return {
        ...edge,
        warpLike: kind === "warp" || (!hasTypedAdjacencies && edge.length > medianLength * 1.7)
      };
    });

    // Render as a Flow-style board only when every cell sits on an integer
    // lattice and every non-warp connection is a unit-length axis step —
    // hex, circle, and free-form graphs keep the node/edge fallback.
    const axisAligned = edges.every((edge) => {
      if (edge.warpLike) {
        return true;
      }
      const dx = Math.abs(edge.x2 - edge.x1) / scale;
      const dy = Math.abs(edge.y2 - edge.y1) / scale;
      return (
        (Math.abs(dx - 1) < LATTICE_EPS && dy < LATTICE_EPS) ||
        (Math.abs(dy - 1) < LATTICE_EPS && dx < LATTICE_EPS)
      );
    });
    const gridMode = onLattice && axisAligned && edges.length > 0;

    const tileMap = new Map<string, RenderTile>();
    if (gridMode) {
      for (const node of nodes) {
        const key = `${Math.round(node.x)}:${Math.round(node.y)}`;
        const isBridge = node.kind === "bridge_h" || node.kind === "bridge_v";
        const existing = tileMap.get(key);
        if (existing) {
          existing.isBridge = existing.isBridge || isBridge;
          continue;
        }
        tileMap.set(key, {
          key,
          sx: node.sx,
          sy: node.sy,
          solutionColor: isBridge ? null : node.solutionColor,
          isBridge
        });
      }
    }

    return {
      nodes,
      tiles: Array.from(tileMap.values()),
      edges,
      bridges,
      barriers,
      gridMode,
      width,
      height: viewHeight,
      scale
    };
  }, [
    graph,
    nodeColor,
    showSolution,
    terminalNodeColor,
    compact,
    height,
    cellSize,
    solutionEdgeColors,
    adjacencyKinds
  ]);

  const gridMode = rendered.gridMode;
  const nodeRadius = clamp(rendered.scale * 0.12, compact ? 2.5 : 3, compact ? 6 : 9);
  const terminalRadius = gridMode
    ? clamp(rendered.scale * 0.32, compact ? 4 : 6, 30)
    : nodeRadius * 1.55;
  const baseEdgeWidth = clamp(rendered.scale * 0.06, 1, compact ? 1.8 : 2.2);
  const solutionEdgeWidth = gridMode
    ? clamp(rendered.scale * 0.3, compact ? 3.5 : 5, 26)
    : clamp(rendered.scale * 0.2, compact ? 2.5 : 3.2, compact ? 5 : 8);
  const warpStubLength = clamp(rendered.scale * 0.34, compact ? 6 : 8, compact ? 8 : 13);
  const warpPortalRadius = clamp(rendered.scale * 0.075, compact ? 2.2 : 2.8, compact ? 3.2 : 4.2);
  // Walls span the full shared cell boundary so they read as solid walls, the
  // way the game draws them; the fallback view keeps a shorter tick.
  const barrierHalfLength = gridMode
    ? rendered.scale / 2
    : clamp(rendered.scale * 0.24, compact ? 4 : 5, compact ? 7 : 10);
  const barrierWidth = gridMode
    ? clamp(rendered.scale * 0.13, 2.5, 8)
    : clamp(rendered.scale * 0.09, compact ? 2 : 2.5, compact ? 3 : 4);

  return (
    <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", width: "100%", overflowX: "auto" }}>
      <svg
        width={rendered.width}
        height={rendered.height}
        viewBox={`0 0 ${rendered.width} ${rendered.height}`}
        style={{
          background: "linear-gradient(145deg, #1a1a2e 0%, #0f0f1a 100%)",
          borderRadius: compact ? 6 : 10,
          border: compact ? "1px solid #333" : "2px solid #333",
          minWidth: rendered.width
        }}
      >
        {gridMode &&
          rendered.tiles.map((tile) => {
            const half = rendered.scale / 2;
            const highlight =
              showSolution && tile.solutionColor ? colorToHex[tile.solutionColor] ?? null : null;
            return (
              <rect
                key={`tile-${tile.key}`}
                x={tile.sx - half}
                y={tile.sy - half}
                width={rendered.scale}
                height={rendered.scale}
                fill={highlight ? `${highlight}2b` : "rgba(255,255,255,0.02)"}
                stroke="rgba(255,255,255,0.12)"
                strokeWidth={1}
              />
            );
          })}

        {rendered.edges.map((edge) => {
          if (!edge.warpLike) {
            if (gridMode) {
              return null;
            }
            return (
              <line
                key={`edge-${edge.u}-${edge.v}`}
                x1={edge.x1}
                y1={edge.y1}
                x2={edge.x2}
                y2={edge.y2}
                stroke="rgba(156,163,175,0.42)"
                strokeWidth={baseEdgeWidth}
              />
            );
          }

          const stubs = buildWarpStubs(edge, warpStubLength);
          return (
            <g key={`edge-${edge.u}-${edge.v}`} aria-label={`Warp connection from ${edge.u} to ${edge.v}`}>
              <title>Warp continues at the matching portal</title>
              {stubs.map((stub, index) => (
                <g key={index}>
                  <line
                    x1={stub.x1}
                    y1={stub.y1}
                    x2={stub.x2}
                    y2={stub.y2}
                    stroke="rgba(156,163,175,0.58)"
                    strokeWidth={baseEdgeWidth}
                    strokeDasharray={`${baseEdgeWidth * 2.4} ${baseEdgeWidth * 1.6}`}
                    strokeLinecap="round"
                  />
                  <circle
                    cx={stub.x2}
                    cy={stub.y2}
                    r={warpPortalRadius}
                    fill="rgba(15,15,26,0.96)"
                    stroke="rgba(190,198,212,0.74)"
                    strokeWidth={baseEdgeWidth}
                  />
                  <circle
                    cx={stub.x2}
                    cy={stub.y2}
                    r={warpPortalRadius * 0.34}
                    fill="rgba(190,198,212,0.74)"
                  />
                </g>
              ))}
            </g>
          );
        })}

        {showSolution &&
          rendered.edges.map((edge) => {
            if (!edge.solutionColor) {
              return null;
            }
            const hex = colorToHex[edge.solutionColor] ?? "#ff5252";
            if (edge.warpLike) {
              const stubs = buildWarpStubs(edge, warpStubLength);
              return (
                <g key={`sol-${edge.u}-${edge.v}`} aria-label={`Solved warp from ${edge.u} to ${edge.v}`}>
                  <title>Solution warps to the matching portal</title>
                  {stubs.map((stub, index) => (
                    <g key={index}>
                      <line
                        x1={stub.x1}
                        y1={stub.y1}
                        x2={stub.x2}
                        y2={stub.y2}
                        stroke={hex}
                        strokeWidth={solutionEdgeWidth}
                        strokeLinecap="round"
                      />
                      <circle
                        cx={stub.x2}
                        cy={stub.y2}
                        r={warpPortalRadius * 1.22}
                        fill="rgba(15,15,26,0.96)"
                        stroke="rgba(255,255,255,0.74)"
                        strokeWidth={1.2}
                      />
                      <circle
                        cx={stub.x2}
                        cy={stub.y2}
                        r={warpPortalRadius * 0.82}
                        fill={hex}
                        stroke={hex}
                        strokeWidth={1}
                      />
                      <circle
                        cx={stub.x2}
                        cy={stub.y2}
                        r={warpPortalRadius * 0.28}
                        fill="rgba(15,15,26,0.96)"
                      />
                    </g>
                  ))}
                </g>
              );
            }
            return (
              <line
                key={`sol-${edge.u}-${edge.v}`}
                x1={edge.x1}
                y1={edge.y1}
                x2={edge.x2}
                y2={edge.y2}
                stroke={hex}
                strokeWidth={solutionEdgeWidth}
                strokeLinecap="round"
              />
            );
          })}

        {rendered.bridges.map((bridge) => {
          const horizontal = bridge.horizontalColor
            ? colorToHex[bridge.horizontalColor] ?? "#ff5252"
            : "rgba(210,215,225,0.78)";
          const vertical = bridge.verticalColor
            ? colorToHex[bridge.verticalColor] ?? "#ff5252"
            : "rgba(210,215,225,0.62)";
          const arm = Math.max(nodeRadius * 2.2, baseEdgeWidth * 3.2);
          const channelWidth = showSolution ? solutionEdgeWidth : baseEdgeWidth * 1.5;
          return (
            <g key={`bridge-${bridge.id}`} aria-label={`Bridge cell ${bridge.id}`}>
              <circle
                cx={bridge.sx}
                cy={bridge.sy}
                r={arm * 0.82}
                fill="rgba(15,15,26,0.96)"
                stroke="rgba(220,225,235,0.52)"
                strokeWidth={1}
              />
              <line
                x1={bridge.sx}
                y1={bridge.sy - arm}
                x2={bridge.sx}
                y2={bridge.sy + arm}
                stroke={vertical}
                strokeWidth={channelWidth}
                strokeLinecap="round"
              />
              <line
                x1={bridge.sx - arm}
                y1={bridge.sy}
                x2={bridge.sx + arm}
                y2={bridge.sy}
                stroke="rgba(15,15,26,1)"
                strokeWidth={channelWidth + 3}
                strokeLinecap="round"
              />
              <line
                x1={bridge.sx - arm}
                y1={bridge.sy}
                x2={bridge.sx + arm}
                y2={bridge.sy}
                stroke={horizontal}
                strokeWidth={channelWidth}
                strokeLinecap="round"
              />
            </g>
          );
        })}

        {rendered.barriers.map((barrier) => {
          const offsetX = barrier.perpendicularX * barrierHalfLength;
          const offsetY = barrier.perpendicularY * barrierHalfLength;
          return (
            <g key={`barrier-${barrier.id}`} aria-label={`Barrier between ${barrier.u} and ${barrier.v}`}>
              <title>Blocked path</title>
              <line
                x1={barrier.cx - offsetX}
                y1={barrier.cy - offsetY}
                x2={barrier.cx + offsetX}
                y2={barrier.cy + offsetY}
                stroke="rgba(15,15,26,0.98)"
                strokeWidth={barrierWidth + 3}
                strokeLinecap={gridMode ? "butt" : "round"}
              />
              <line
                x1={barrier.cx - offsetX}
                y1={barrier.cy - offsetY}
                x2={barrier.cx + offsetX}
                y2={barrier.cy + offsetY}
                stroke={gridMode ? "#e8ecf4" : "#ff9dad"}
                strokeWidth={barrierWidth}
                strokeLinecap={gridMode ? "butt" : "round"}
              />
            </g>
          );
        })}

        {rendered.nodes.map((node) => {
          if (node.kind === "bridge_h" || node.kind === "bridge_v") {
            return null;
          }
          const terminal = node.terminalColor ? colorToHex[node.terminalColor] ?? "#ff5252" : null;
          const solved = node.solutionColor ? colorToHex[node.solutionColor] ?? "#ff5252" : null;
          if (gridMode && !terminal) {
            // The game leaves non-terminal cells empty; pipes and cell tints
            // already show solved coverage.
            return null;
          }
          const fill = terminal ?? solved ?? "rgba(220,220,220,0.86)";
          const radius = terminal ? terminalRadius : nodeRadius;
          return (
            <circle
              key={`node-${node.id}`}
              cx={node.sx}
              cy={node.sy}
              r={radius}
              fill={fill}
              stroke={terminal ? "rgba(255,255,255,0.44)" : "rgba(0,0,0,0.35)"}
              strokeWidth={terminal ? 1.7 : 1}
            />
          );
        })}
      </svg>
    </Box>
  );
}
