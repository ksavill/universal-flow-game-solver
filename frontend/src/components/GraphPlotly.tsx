import Plot from "react-plotly.js";
import { SolveResponse } from "../api";
import { TERMINAL_PALETTE, buildTerminalColorMaps } from "../colors";
import {
  SolutionPathEdges,
  SolutionPaths,
  buildBlockedAdjacencies,
  buildSolutionEdgeColors,
  canonicalEdgeKey
} from "../solutionEdges";

type GraphPlotlyProps = {
  graph: SolveResponse["graph"];
  use3d?: boolean;
  nodeColor?: Record<string, string | null> | null;
  pathEdges?: SolutionPathEdges | null;
  paths?: SolutionPaths | null;
  showSolution?: boolean;
};

export function GraphPlotly({
  graph,
  use3d = false,
  nodeColor,
  pathEdges,
  paths,
  showSolution = false
}: GraphPlotlyProps) {
  const { colorToHex, terminalNodeColor } = buildTerminalColorMaps(graph, nodeColor, TERMINAL_PALETTE);
  const solutionEdgeColors = buildSolutionEdgeColors(pathEdges, paths);
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edgesX: (number | null)[] = [];
  const edgesY: (number | null)[] = [];
  const edgesZ: (number | null)[] = [];

  graph.edges.forEach(([u, v]) => {
    const a = graph.nodes.find((n) => n.id === u);
    const b = graph.nodes.find((n) => n.id === v);
    if (!a || !b) {
      return;
    }
    edgesX.push(a.x, b.x, null);
    edgesY.push(a.y, b.y, null);
    edgesZ.push(a.z, b.z, null);
  });

  const nodesX = graph.nodes.map((n) => n.x);
  const nodesY = graph.nodes.map((n) => n.y);
  const nodesZ = graph.nodes.map((n) => n.z);
  const nodeColors = graph.nodes.map((n) => {
    const solutionColor = showSolution && nodeColor ? nodeColor[n.id] : null;
    const terminalColor = terminalNodeColor[n.id];
    if (solutionColor) {
      return colorToHex[solutionColor] ?? "#ff5252";
    }
    return terminalColor ? colorToHex[terminalColor] ?? "#ff5252" : "#b0b0b0";
  });
  const nodeSizes = graph.nodes.map((n) => {
    const solutionColor = showSolution && nodeColor ? nodeColor[n.id] : null;
    return solutionColor || terminalNodeColor[n.id] ? 8 : 5;
  });

  const barriersX: (number | null)[] = [];
  const barriersY: (number | null)[] = [];
  const barriersZ: (number | null)[] = [];
  buildBlockedAdjacencies(graph).forEach((barrier) => {
    const a = nodeById.get(barrier.u);
    const b = nodeById.get(barrier.v);
    if (!a || !b) {
      return;
    }
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const magnitude = Math.hypot(dx, dy);
    if (magnitude < 0.001) {
      return;
    }
    const halfLength = magnitude * 0.24;
    const offsetX = (-dy / magnitude) * halfLength;
    const offsetY = (dx / magnitude) * halfLength;
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2;
    const cz = (a.z + b.z) / 2;
    barriersX.push(cx - offsetX, cx + offsetX, null);
    barriersY.push(cy - offsetY, cy + offsetY, null);
    barriersZ.push(cz, cz, null);
  });

  const barrierTraces: Array<Record<string, unknown>> = barriersX.length
    ? use3d
      ? [
          {
            type: "scatter3d",
            mode: "lines",
            x: barriersX,
            y: barriersY,
            z: barriersZ,
            line: { width: 10, color: "rgba(15,15,26,0.98)" },
            hoverinfo: "none",
            showlegend: false
          },
          {
            type: "scatter3d",
            mode: "lines",
            x: barriersX,
            y: barriersY,
            z: barriersZ,
            line: { width: 5, color: "#ff9dad" },
            hoverinfo: "none",
            showlegend: false
          }
        ]
      : [
          {
            type: "scatter",
            mode: "lines",
            x: barriersX,
            y: barriersY,
            line: { width: 8, color: "rgba(15,15,26,0.98)" },
            hoverinfo: "none",
            showlegend: false
          },
          {
            type: "scatter",
            mode: "lines",
            x: barriersX,
            y: barriersY,
            line: { width: 4, color: "#ff9dad" },
            hoverinfo: "none",
            showlegend: false
          }
        ]
    : [];

  const solutionTraces: Array<Record<string, unknown>> = [];
  if (showSolution) {
    const edgesByColor: Record<string, { x: (number | null)[]; y: (number | null)[]; z: (number | null)[] }> = {};
    graph.edges.forEach(([u, v]) => {
      const selectedColor = solutionEdgeColors.get(canonicalEdgeKey(u, v));
      if (!selectedColor) {
        return;
      }
      const a = graph.nodes.find((n) => n.id === u);
      const b = graph.nodes.find((n) => n.id === v);
      if (!a || !b) {
        return;
      }
      if (!edgesByColor[selectedColor]) {
        edgesByColor[selectedColor] = { x: [], y: [], z: [] };
      }
      edgesByColor[selectedColor].x.push(a.x, b.x, null);
      edgesByColor[selectedColor].y.push(a.y, b.y, null);
      edgesByColor[selectedColor].z.push(a.z, b.z, null);
    });
    Object.entries(edgesByColor).forEach(([color, coords]) => {
      const hex = colorToHex[color] ?? "#ff5252";
      if (use3d) {
        solutionTraces.push({
          type: "scatter3d",
          mode: "lines",
          x: coords.x,
          y: coords.y,
          z: coords.z,
          line: { width: 8, color: hex },
          hoverinfo: "none",
          showlegend: false
        });
      } else {
        solutionTraces.push({
          type: "scatter",
          mode: "lines",
          x: coords.x,
          y: coords.y,
          line: { width: 4, color: hex },
          hoverinfo: "none",
          showlegend: false
        });
      }
    });
  }

  if (use3d) {
    return (
      <Plot
        data={[
          {
            type: "scatter3d",
            mode: "lines",
            x: edgesX,
            y: edgesY,
            z: edgesZ,
            line: { width: 3, color: "rgba(160,160,160,0.6)" },
            hoverinfo: "none"
          },
          ...solutionTraces,
          ...barrierTraces,
          {
            type: "scatter3d",
            mode: "markers",
            x: nodesX,
            y: nodesY,
            z: nodesZ,
            marker: { size: nodeSizes, color: nodeColors },
            hoverinfo: "none"
          }
        ]}
        layout={{
          margin: { l: 0, r: 0, t: 0, b: 0 },
          scene: {
            xaxis: { visible: false },
            yaxis: { visible: false },
            zaxis: { visible: false }
          },
          height: 320,
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent"
        }}
        config={{ displayModeBar: false }}
        style={{ width: "100%" }}
      />
    );
  }

  return (
    <Plot
      data={[
        {
          type: "scatter",
          mode: "lines",
          x: edgesX,
          y: edgesY,
          line: { width: 1.5, color: "rgba(160,160,160,0.6)" },
          hoverinfo: "none"
        },
        ...solutionTraces,
        ...barrierTraces,
        {
          type: "scatter",
          mode: "markers",
          x: nodesX,
          y: nodesY,
          marker: { size: nodeSizes, color: nodeColors },
          hoverinfo: "none"
        }
      ]}
      layout={{
        margin: { l: 0, r: 0, t: 0, b: 0 },
        xaxis: { visible: false },
        yaxis: { visible: false, scaleanchor: "x", scaleratio: 1 },
        height: 320,
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent"
      }}
      config={{ displayModeBar: false }}
      style={{ width: "100%" }}
    />
  );
}
