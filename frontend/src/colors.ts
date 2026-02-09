import type { SolveResponse } from "./api";

export const TERMINAL_PALETTE = [
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#e377c2",
  "#7f7f7f",
  "#bcbd22",
  "#17becf",
  "#aec7e8",
  "#ffbb78",
  "#98df8a",
  "#ff9896",
  "#c5b0d5",
  "#c49c94",
  "#f7b6d2",
  "#c7c7c7",
  "#dbdb8d",
  "#9edae5",
  "#1b9e77",
  "#d95f02",
  "#7570b3",
  "#e7298a",
  "#66a61e",
  "#ffffff"
];

export const GAME_PALETTE = [
  "#2196F3",
  "#FF9800",
  "#4CAF50",
  "#F44336",
  "#9C27B0",
  "#795548",
  "#E91E63",
  "#9E9E9E",
  "#CDDC39",
  "#00BCD4",
  "#FFEB3B",
  "#FF5722",
  "#aec7e8",
  "#ffbb78",
  "#98df8a",
  "#ff9896",
  "#c5b0d5",
  "#c49c94",
  "#f7b6d2",
  "#c7c7c7",
  "#dbdb8d",
  "#9edae5",
  "#1b9e77",
  "#d95f02",
  "#7570b3",
  "#ffffff"
];

function normalizeHexColor(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const raw = value.trim();
  if (!raw) {
    return null;
  }
  const match = raw.match(/^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
  if (!match) {
    return null;
  }
  let hex = match[1].toLowerCase();
  if (hex.length === 3) {
    hex = `${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`;
  }
  return `#${hex}`;
}

export function buildTerminalColorMaps(
  graph: SolveResponse["graph"],
  nodeColor?: Record<string, string | null> | null,
  palette: string[] = TERMINAL_PALETTE
) {
  const terminalNodeColor: Record<string, string> = {};
  const terminalColors = Object.keys(graph.terminals ?? {}).sort();
  const colorToHex: Record<string, string> = {};
  const overrides = graph.terminal_colors ?? {};

  terminalColors.forEach((color, idx) => {
    const overrideHex = normalizeHexColor(overrides[color]);
    colorToHex[color] = overrideHex ?? palette[idx % palette.length];
    const pair = graph.terminals[color];
    if (pair && pair.length === 2) {
      terminalNodeColor[pair[0]] = color;
      terminalNodeColor[pair[1]] = color;
    }
  });

  if (nodeColor) {
    const solutionColors = Array.from(new Set(Object.values(nodeColor).filter((c): c is string => Boolean(c))));
    if (solutionColors.length && terminalColors.length === 0) {
      solutionColors.sort().forEach((color, idx) => {
        colorToHex[color] = palette[idx % palette.length];
      });
    }
  }

  return { colorToHex, terminalNodeColor };
}
