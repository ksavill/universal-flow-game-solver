import type { SolveResponse } from "../api";

export type LocalPuzzle = {
  width: number;
  height: number;
  cells: string[];
  terminals: Record<string, [number, number]>;
  fill: boolean;
};

export const LOCAL_VERSION = "local-square-v1";

export function parseLocalPuzzle(text: string): LocalPuzzle {
  let fill = true;
  const rows: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const directive = /^#\s*([\w-]+):\s*(.*)$/.exec(line);
    if (directive) {
      const [, key, value] = directive;
      if (key === "type" && value !== "square") throw new Error("The local prototype supports square boards only.");
      if (key === "fill") {
        if (value !== "true" && value !== "false") throw new Error("Fill must be true or false.");
        fill = value === "true";
      }
      if (["walls", "warps", "edge_overrides", "core"].includes(key)) {
        throw new Error(`The local prototype does not support ${key}.`);
      }
      continue;
    }
    if (!/^[A-Z.#]+$/.test(line)) throw new Error("Use A–Z for endpoints, . for empty cells, and # for holes. Bridges and graph JSON are not supported yet.");
    rows.push(line);
  }
  const width = rows[0]?.length ?? 0;
  if (width < 2 || width > 20 || rows.length < 2 || rows.length > 20) throw new Error("Use a board between 2 × 2 and 20 × 20.");
  if (rows.some(row => row.length !== width)) throw new Error("Every board row must have the same length.");
  const cells = rows.join("").split("");
  const endpoints: Record<string, number[]> = {};
  cells.forEach((cell, index) => {
    if (/^[A-Z]$/.test(cell)) (endpoints[cell] ??= []).push(index);
  });
  if (!Object.keys(endpoints).length) throw new Error("Add at least one endpoint pair.");
  for (const [color, positions] of Object.entries(endpoints)) {
    if (positions.length !== 2) throw new Error(`${color} has ${positions.length} endpoints; each letter needs exactly two.`);
  }
  return { width, height: rows.length, cells, terminals: endpoints as LocalPuzzle["terminals"], fill };
}

export function neighbors(puzzle: LocalPuzzle, index: number): number[] {
  const x = index % puzzle.width;
  const y = Math.floor(index / puzzle.width);
  return [x > 0 ? index - 1 : -1, x + 1 < puzzle.width ? index + 1 : -1,
    y > 0 ? index - puzzle.width : -1, y + 1 < puzzle.height ? index + puzzle.width : -1]
    .filter(next => next >= 0 && puzzle.cells[next] !== "#");
}

// Independent verification: never render an unchecked solver model as a solution.
export function validateLocalSolution(puzzle: LocalPuzzle, paths: Record<string, number[]>): void {
  const used = new Set<number>();
  if (Object.keys(paths).length !== Object.keys(puzzle.terminals).length) throw new Error("Solution has the wrong number of paths.");
  for (const [color, [start, end]] of Object.entries(puzzle.terminals)) {
    const path = paths[color];
    if (!path || path.length < 2 || path[0] !== start || path[path.length - 1] !== end) throw new Error(`Invalid endpoints for ${color}.`);
    path.forEach((index, offset) => {
      if (!Number.isInteger(index) || index < 0 || index >= puzzle.cells.length || puzzle.cells[index] === "#" || used.has(index)) throw new Error("Solution overlaps or visits an invalid cell.");
      if (puzzle.cells[index] !== "." && puzzle.cells[index] !== color) throw new Error("Solution crosses another color's endpoint.");
      if (offset && !neighbors(puzzle, path[offset - 1]).includes(index)) throw new Error("Solution has a non-adjacent step.");
      used.add(index);
    });
  }
  if (puzzle.fill && used.size !== puzzle.cells.filter(cell => cell !== "#").length) throw new Error("Solution does not fill the board.");
}

export function localGraph(puzzle: LocalPuzzle, colors: Record<string, string> = {}): SolveResponse["graph"] {
  const id = (index: number) => `${index % puzzle.width},${Math.floor(index / puzzle.width)}`;
  const nodes = puzzle.cells.flatMap((cell, index) => cell === "#" ? [] : [{
    id: id(index), x: index % puzzle.width, y: -Math.floor(index / puzzle.width), z: 0,
    kind: cell === "." ? "cell" : "terminal", data: { tile: id(index), color: cell === "." ? null : cell }
  }]);
  const edges: Array<[string, string]> = [];
  puzzle.cells.forEach((cell, index) => {
    if (cell !== "#") neighbors(puzzle, index).filter(next => next > index).forEach(next => edges.push([id(index), id(next)]));
  });
  return {
    nodes, edges, terminals: Object.fromEntries(Object.entries(puzzle.terminals).map(([color, pair]) => [color, pair.map(id) as [string, string]])),
    tiles: Object.fromEntries(nodes.map(node => [node.id, [node.id]])), terminal_colors: colors,
    meta: { type: "square", width: puzzle.width, height: puzzle.height }
  };
}

export function displayPaths(puzzle: LocalPuzzle, paths: Record<string, number[]>): Record<string, string[]> {
  return Object.fromEntries(Object.entries(paths).map(([color, path]) => [color, path.map(index => `${index % puzzle.width},${Math.floor(index / puzzle.width)}`)]));
}
