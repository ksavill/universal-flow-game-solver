import { Box, ToggleButton, ToggleButtonGroup } from "@mui/material";
import { SolveResponse } from "../api";
import { GameView } from "./GameView";
import { GraphPreview } from "./GraphPreview";

export type BoardViewMode = "game" | "graph";

type BoardViewToggleProps = {
  value: BoardViewMode;
  onChange: (value: BoardViewMode) => void;
};

export function BoardViewToggle({ value, onChange }: BoardViewToggleProps) {
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={value}
      onChange={(_event, nextValue: BoardViewMode | null) => nextValue && onChange(nextValue)}
      aria-label="Board visualization"
    >
      <ToggleButton value="game" aria-label="Game-like view">
        Game
      </ToggleButton>
      <ToggleButton value="graph" aria-label="Graph view">
        Graph
      </ToggleButton>
    </ToggleButtonGroup>
  );
}

type BoardVisualizationProps = {
  graph: SolveResponse["graph"];
  mode?: BoardViewMode;
  nodeColor?: SolveResponse["node_color"] | null;
  pathEdges?: SolveResponse["path_edges"] | null;
  paths?: SolveResponse["paths"] | null;
  showSolution?: boolean;
  height?: number;
  graphHeight?: number;
  compact?: boolean;
};

export function BoardVisualization({
  graph,
  mode = "game",
  nodeColor,
  pathEdges,
  paths,
  showSolution = false,
  height = 320,
  graphHeight,
  compact = false
}: BoardVisualizationProps) {
  if (mode === "game") {
    return (
      <GameView
        graph={graph}
        nodeColor={nodeColor}
        pathEdges={pathEdges}
        paths={paths}
        showSolution={showSolution}
        height={height}
        compact={compact}
      />
    );
  }

  return (
    <Box sx={{ width: "100%", display: "flex", justifyContent: "center", overflowX: "auto" }}>
      <GraphPreview
        graph={graph}
        height={graphHeight ?? (compact ? 140 : height)}
        nodeColor={nodeColor}
        pathEdges={pathEdges}
        paths={paths}
        showSolution={showSolution}
      />
    </Box>
  );
}
