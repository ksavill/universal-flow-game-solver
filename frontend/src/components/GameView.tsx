import { Box } from "@mui/material";
import { useMemo } from "react";
import { SolveResponse } from "../api";
import { GAME_PALETTE, buildTerminalColorMaps } from "../colors";

type GameViewProps = {
  graph: SolveResponse["graph"];
  nodeColor?: Record<string, string | null> | null;
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
  terminalColor: string | null;
  solutionColor: string | null;
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

export function GameView({
  graph,
  nodeColor,
  showSolution = false,
  height = 320,
  cellSize,
  compact = false
}: GameViewProps) {
  const { colorToHex, terminalNodeColor } = useMemo(
    () => buildTerminalColorMaps(graph, nodeColor, GAME_PALETTE),
    [graph, nodeColor]
  );

  const rendered = useMemo(() => {
    const baseNodes = graph.nodes.map((node) => ({
      id: node.id,
      x: node.x,
      y: node.y,
      kind: node.kind,
      terminalColor: terminalNodeColor[node.id] ?? null,
      solutionColor: showSolution && nodeColor ? nodeColor[node.id] ?? null : null
    }));

    if (!baseNodes.length) {
      return {
        nodes: [] as RenderNode[],
        edges: [] as RenderEdge[],
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

    const padding = compact ? 12 : 18;
    const maxWidth = compact ? 320 : 700;
    const targetHeight = compact ? 140 : height;
    const scaleByHeight = (targetHeight - padding * 2) / spanY;
    const scaleByWidth = (maxWidth - padding * 2) / spanX;
    const scale = clamp(cellSize ?? Math.min(scaleByHeight, scaleByWidth), 8, 96);
    const width = Math.max(140, Math.min(maxWidth, spanX * scale + padding * 2));
    const viewHeight = Math.max(96, spanY * scale + padding * 2);

    const mapX = (x: number) => padding + (x - minX) * scale;
    const mapY = (y: number) => padding + (maxY - y) * scale;

    const nodes: RenderNode[] = baseNodes.map((node) => ({
      ...node,
      sx: mapX(node.x),
      sy: mapY(node.y)
    }));
    const nodeById = new Map(nodes.map((node) => [node.id, node]));

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
        solutionColor:
          showSolution && nodeColor && nodeColor[u] && nodeColor[u] === nodeColor[v]
            ? (nodeColor[u] as string)
            : null
      });
    }

    const medianLength = Math.max(0.001, median(lengths));
    const edges: RenderEdge[] = rawEdges.map((edge) => ({
      ...edge,
      warpLike: edge.length > medianLength * 1.7
    }));

    return { nodes, edges, width, height: viewHeight, scale };
  }, [graph, nodeColor, showSolution, terminalNodeColor, compact, height, cellSize]);

  const nodeRadius = clamp(rendered.scale * 0.12, compact ? 2.5 : 3, compact ? 6 : 9);
  const terminalRadius = nodeRadius * 1.55;
  const baseEdgeWidth = clamp(rendered.scale * 0.06, 1, compact ? 1.8 : 2.2);
  const solutionEdgeWidth = clamp(rendered.scale * 0.2, compact ? 2.5 : 3.2, compact ? 5 : 8);

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
        {rendered.edges.map((edge) => (
          <line
            key={`edge-${edge.u}-${edge.v}`}
            x1={edge.x1}
            y1={edge.y1}
            x2={edge.x2}
            y2={edge.y2}
            stroke="rgba(156,163,175,0.42)"
            strokeWidth={baseEdgeWidth}
            strokeDasharray={edge.warpLike ? `${baseEdgeWidth * 2.4} ${baseEdgeWidth * 1.9}` : undefined}
          />
        ))}

        {showSolution &&
          rendered.edges.map((edge) => {
            if (!edge.solutionColor) {
              return null;
            }
            const hex = colorToHex[edge.solutionColor] ?? "#ff5252";
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
                strokeDasharray={edge.warpLike ? `${solutionEdgeWidth * 2.2} ${solutionEdgeWidth * 1.8}` : undefined}
              />
            );
          })}

        {rendered.nodes.map((node) => {
          const terminal = node.terminalColor ? colorToHex[node.terminalColor] ?? "#ff5252" : null;
          const solved = node.solutionColor ? colorToHex[node.solutionColor] ?? "#ff5252" : null;
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
