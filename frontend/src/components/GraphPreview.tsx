import { Box } from "@mui/material";
import { SolveResponse } from "../api";
import { TERMINAL_PALETTE, buildTerminalColorMaps } from "../colors";
import {
  SolutionPathEdges,
  SolutionPaths,
  buildBlockedAdjacencies,
  buildSolutionEdgeColors,
  canonicalEdgeKey
} from "../solutionEdges";

type GraphPreviewProps = {
  graph: SolveResponse["graph"];
  height?: number;
  nodeColor?: Record<string, string | null> | null;
  pathEdges?: SolutionPathEdges | null;
  paths?: SolutionPaths | null;
  showSolution?: boolean;
};

export function GraphPreview({
  graph,
  height = 140,
  nodeColor,
  pathEdges,
  paths,
  showSolution = false
}: GraphPreviewProps) {
  if (!graph.nodes.length) {
    return null;
  }

  const { colorToHex, terminalNodeColor } = buildTerminalColorMaps(graph, nodeColor, TERMINAL_PALETTE);
  const solutionEdgeColors = buildSolutionEdgeColors(pathEdges, paths);
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const barriers = buildBlockedAdjacencies(graph);

  const xs = graph.nodes.map((n) => n.x);
  const ys = graph.nodes.map((n) => n.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const padding = 10;
  const width = 220;
  const viewWidth = width - padding * 2;
  const viewHeight = height - padding * 2;
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;

  const mapX = (x: number) => padding + ((x - minX) / spanX) * viewWidth;
  const mapY = (y: number) => padding + ((maxY - y) / spanY) * viewHeight;

  return (
    <Box>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        {graph.edges.map(([u, v]) => {
          const a = graph.nodes.find((n) => n.id === u);
          const b = graph.nodes.find((n) => n.id === v);
          if (!a || !b) {
            return null;
          }
          return (
            <line
              key={`${u}-${v}`}
              x1={mapX(a.x)}
              y1={mapY(a.y)}
              x2={mapX(b.x)}
              y2={mapY(b.y)}
              stroke="rgba(200,200,200,0.5)"
              strokeWidth={1}
            />
          );
        })}
        {showSolution && (
          <>
            {graph.edges.map(([u, v]) => {
              const selectedColor = solutionEdgeColors.get(canonicalEdgeKey(u, v));
              if (!selectedColor) {
                return null;
              }
              const a = graph.nodes.find((n) => n.id === u);
              const b = graph.nodes.find((n) => n.id === v);
              if (!a || !b) {
                return null;
              }
              const color = colorToHex[selectedColor] ?? "#ff5252";
              return (
                <line
                  key={`sol-${u}-${v}`}
                  x1={mapX(a.x)}
                  y1={mapY(a.y)}
                  x2={mapX(b.x)}
                  y2={mapY(b.y)}
                  stroke={color}
                  strokeWidth={2}
                />
              );
            })}
          </>
        )}
        {barriers.map((barrier) => {
          const a = nodeById.get(barrier.u);
          const b = nodeById.get(barrier.v);
          if (!a || !b) {
            return null;
          }
          const ax = mapX(a.x);
          const ay = mapY(a.y);
          const bx = mapX(b.x);
          const by = mapY(b.y);
          const dx = bx - ax;
          const dy = by - ay;
          const magnitude = Math.hypot(dx, dy);
          if (magnitude < 0.001) {
            return null;
          }
          const halfLength = 5;
          const offsetX = (-dy / magnitude) * halfLength;
          const offsetY = (dx / magnitude) * halfLength;
          const cx = (ax + bx) / 2;
          const cy = (ay + by) / 2;
          return (
            <g key={`barrier-${barrier.id}`} aria-label={`Barrier between ${barrier.u} and ${barrier.v}`}>
              <title>Blocked path</title>
              <line
                x1={cx - offsetX}
                y1={cy - offsetY}
                x2={cx + offsetX}
                y2={cy + offsetY}
                stroke="rgba(15,15,26,0.98)"
                strokeWidth={5}
                strokeLinecap="round"
              />
              <line
                x1={cx - offsetX}
                y1={cy - offsetY}
                x2={cx + offsetX}
                y2={cy + offsetY}
                stroke="#ff9dad"
                strokeWidth={2.5}
                strokeLinecap="round"
              />
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const solutionColor = showSolution && nodeColor ? nodeColor[n.id] : null;
          const terminalColor = terminalNodeColor[n.id];
          const fill = solutionColor
            ? colorToHex[solutionColor] ?? "#ff5252"
            : terminalColor
              ? colorToHex[terminalColor] ?? "#ff5252"
              : "#b0b0b0";
          const r = solutionColor || terminalColor ? 4 : 3;
          return <circle key={n.id} cx={mapX(n.x)} cy={mapY(n.y)} r={r} fill={fill} />;
        })}
      </svg>
    </Box>
  );
}
