import type { SolveResponse } from "../api";
import { localGraph } from "./localPuzzle";

// A square board as the editor sees it: like LocalPuzzle, but tolerant of
// incomplete pairs so a detection mistake can be shown and fixed on the board.
export type EditableBoard = { width: number; height: number; cells: string[]; fill: boolean };
export type PairStatus = { letter: string; count: number };

export const MIN_SIDE = 2;
export const MAX_SIDE = 20;
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const UNSUPPORTED = new Set(["walls", "warps", "edge_overrides", "core"]);

export function readBoard(text: string): EditableBoard | null {
  let fill = true;
  const rows: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const directive = /^#\s*([\w-]+):\s*(.*)$/.exec(line);
    if (directive) {
      if (directive[1] === "type" && directive[2] !== "square") return null;
      if (directive[1] === "fill") fill = directive[2] !== "false";
      if (UNSUPPORTED.has(directive[1])) return null;
      continue;
    }
    if (!/^[A-Z.#]+$/.test(line)) return null;
    rows.push(line);
  }
  const width = rows[0]?.length ?? 0;
  if (width < MIN_SIDE || width > MAX_SIDE || rows.length < MIN_SIDE || rows.length > MAX_SIDE) return null;
  if (rows.some((row) => row.length !== width)) return null;
  return { width, height: rows.length, cells: rows.join("").split(""), fill };
}

export function writeBoard(board: EditableBoard): string {
  const rows = Array.from({ length: board.height }, (_, row) =>
    board.cells.slice(row * board.width, (row + 1) * board.width).join("")
  );
  return `# type: square\n# fill: ${board.fill}\n${rows.join("\n")}\n`;
}

export function blankBoard(height: number, width: number): EditableBoard {
  return { width, height, cells: Array(width * height).fill("."), fill: true };
}

export function resizeBoard(board: EditableBoard, height: number, width: number): EditableBoard {
  const cells = Array.from({ length: width * height }, (_, index) => {
    const row = Math.floor(index / width);
    const col = index % width;
    return row < board.height && col < board.width ? board.cells[row * board.width + col] : ".";
  });
  return { ...board, width, height, cells };
}

export function setCell(board: EditableBoard, index: number, value: string): EditableBoard {
  const cells = [...board.cells];
  cells[index] = value;
  return { ...board, cells };
}

export function pairStatus(board: EditableBoard): PairStatus[] {
  const counts = new Map<string, number>();
  for (const cell of board.cells) {
    if (/^[A-Z]$/.test(cell)) counts.set(cell, (counts.get(cell) ?? 0) + 1);
  }
  return Array.from(counts, ([letter, count]) => ({ letter, count })).sort((a, b) =>
    a.letter.localeCompare(b.letter)
  );
}

export function nextLetter(board: EditableBoard, reserved: Iterable<string> = []): string | null {
  const used = new Set([...board.cells, ...reserved]);
  return LETTERS.split("").find((letter) => !used.has(letter)) ?? null;
}

export function cellIndex(board: EditableBoard, cell: { x: number; y: number }): number {
  // localGraph places row r at y = -r.
  return -cell.y * board.width + cell.x;
}

export function cellPoint(board: EditableBoard, index: number): { x: number; y: number } {
  return { x: index % board.width, y: -Math.floor(index / board.width) };
}

// Game-style preview graph. A single endpoint is drawn in its color (paired
// with itself) so a missed dot is visible; extra endpoints are flagged instead.
export function editorGraph(board: EditableBoard, colors: Record<string, string>): SolveResponse["graph"] {
  const terminals: Record<string, [number, number]> = {};
  board.cells.forEach((cell, index) => {
    if (!/^[A-Z]$/.test(cell)) return;
    const pair = terminals[cell];
    if (!pair) terminals[cell] = [index, index];
    else if (pair[0] === pair[1]) pair[1] = index;
  });
  return localGraph(
    { width: board.width, height: board.height, cells: board.cells, terminals, fill: board.fill },
    colors
  );
}
