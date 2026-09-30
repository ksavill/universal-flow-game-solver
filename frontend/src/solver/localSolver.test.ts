import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { init, killThreads, type Context } from "z3-solver";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { neighbors, parseLocalPuzzle, validateLocalSolution, type LocalPuzzle } from "../core/localPuzzle";
import { solveLocalPuzzle } from "./localSolver";
import type { ProgressUpdate } from "../processing/localProgress";

let api: Awaited<ReturnType<typeof init>>;
let ctx: Context;
beforeAll(async () => { api = await init(); ctx = new api.Context("main"); }, 60_000);
afterAll(async () => { if (api) await killThreads(api.em); });

// A separate exhaustive path enumerator is the oracle for small boards.
function exhaustive(puzzle: LocalPuzzle): boolean {
  const pairs = Object.entries(puzzle.terminals);
  const used = new Set<number>();
  function pairAt(pairIndex: number): boolean {
    if (pairIndex === pairs.length) return !puzzle.fill || used.size === puzzle.cells.filter(cell => cell !== "#").length;
    const [color, [start, end]] = pairs[pairIndex];
    used.add(start);
    function extend(index: number): boolean {
      if (index === end) return pairAt(pairIndex + 1);
      for (const next of neighbors(puzzle, index)) {
        if (used.has(next) || (puzzle.cells[next] !== "." && puzzle.cells[next] !== color)) continue;
        used.add(next);
        if (extend(next)) return true;
        used.delete(next);
      }
      return false;
    }
    const found = extend(start);
    used.delete(start);
    return found;
  }
  return pairAt(0);
}

describe("local exact solver", () => {
  it("solves and independently verifies a repository screenshot puzzle", async () => {
    const text = readFileSync(resolve("../puzzles/square/5x5/classic_level_1.flow"), "utf8");
    const puzzle = parseLocalPuzzle(text);
    const progress: ProgressUpdate[] = [];
    const result = await solveLocalPuzzle(puzzle, ctx, 10_000, update => progress.push(update));
    expect(result.status).toBe("solved");
    validateLocalSolution(puzzle, result.paths);
    expect(new Set(progress.map(update => update.stage))).toEqual(new Set(["building", "solving", "verifying"]));
    expect(progress[progress.length - 1].metrics).toMatchObject({ completedChecks: result.checks, cuts: result.cuts });
    expect(Object.keys(progress[progress.length - 1].engine!).length).toBeGreaterThan(0);
  }, 20_000);

  it("distinguishes UNSAT from exhausted time", async () => {
    expect((await solveLocalPuzzle(parseLocalPuzzle("AB\nBA"), ctx, 5000)).status).toBe("unsat");
    expect((await solveLocalPuzzle(parseLocalPuzzle("AA\nBB"), ctx, 0)).status).toBe("timeout");
  });

  it("agrees with exhaustive enumeration for varied small boards", async () => {
    for (let trial = 0; trial < 24; trial++) {
      const cells = Array(9).fill(".") as string[];
      const order = Array.from({ length: 9 }, (_, i) => (i * 4 + trial) % 9);
      cells[order[0]] = cells[order[1]] = "A";
      cells[order[2]] = cells[order[3]] = "B";
      if (trial % 3 === 0) cells[order[4]] = "#";
      const text = `# fill: ${trial % 2 === 0}\n` + [0, 3, 6].map(offset => cells.slice(offset, offset + 3).join("")).join("\n");
      const puzzle = parseLocalPuzzle(text);
      const result = await solveLocalPuzzle(puzzle, ctx, 3000);
      expect(result.status, text).toBe(exhaustive(puzzle) ? "solved" : "unsat");
      if (result.status === "solved") validateLocalSolution(puzzle, result.paths);
    }
  }, 30_000);

  it("enforces connectivity on a board where local degree constraints allow a detached cycle", async () => {
    const puzzle = parseLocalPuzzle("AA##\n####\n##..\n##..");
    expect((await solveLocalPuzzle(puzzle, ctx, 5000)).status).toBe("unsat");
    const optional = { ...puzzle, fill: false };
    const result = await solveLocalPuzzle(optional, ctx, 5000);
    expect(result.status).toBe("solved");
    validateLocalSolution(optional, result.paths);
  });
});
