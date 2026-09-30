import type { Bool, Context } from "z3-solver";
import { neighbors, validateLocalSolution, type LocalPuzzle } from "../core/localPuzzle";
import type { ProgressReporter } from "../processing/localProgress";

export type LocalSolveResult = {
  status: "solved" | "unsat" | "timeout" | "unknown";
  paths: Record<string, number[]>;
  elapsedMs: number;
  checks: number;
  cuts: number;
  reason?: string;
};

export async function solveLocalPuzzle(puzzle: LocalPuzzle, ctx: Context, timeoutMs: number, report?: ProgressReporter): Promise<LocalSolveResult> {
  const started = performance.now();
  report?.({ stage: "building", metrics: { timeLimitMs: timeoutMs, rows: puzzle.height, cols: puzzle.width, pairs: Object.keys(puzzle.terminals).length } });
  const solver = new ctx.Solver("QF_FD");
  const colors = Object.keys(puzzle.terminals);
  const active = puzzle.cells.map((cell, index) => cell === "#" ? -1 : index).filter(index => index >= 0);
  const edges: Array<[number, number]> = [];
  const incident = puzzle.cells.map(() => [] as number[]);
  active.forEach(index => neighbors(puzzle, index).filter(next => next > index).forEach(next => {
    const edge = edges.length;
    edges.push([index, next]); incident[index].push(edge); incident[next].push(edge);
  }));
  const nodeVars = colors.map((_, c) => puzzle.cells.map((cell, index) => cell === "#" ? ctx.Bool.val(false) : ctx.Bool.const(`n_${c}_${index}`)));
  const edgeVars = colors.map((_, c) => edges.map((_, edge) => ctx.Bool.const(`e_${c}_${edge}`)));
  const eq = (values: Bool[], count: number) => values.length
    ? ctx.PbEq(values as [Bool, ...Bool[]], values.map(() => 1) as [number, ...number[]], count)
    : ctx.Bool.val(count === 0);
  let checks = 0;
  let cuts = 0;
  let constraints = 0;
  let completedChecks = 0;
  const metrics = () => ({ constraints, checks, completedChecks, cuts, activeCells: active.length, solverElapsedMs: performance.now() - started });
  const result = (status: LocalSolveResult["status"], paths: Record<string, number[]> = {}, reason?: string): LocalSolveResult => ({ status, paths, reason, checks, cuts, elapsedMs: performance.now() - started });
  try {
    for (const index of active) {
      const choices = colors.map((_, c) => nodeVars[c][index]);
      solver.add(puzzle.fill ? eq(choices, 1) : ctx.AtMost(choices as [Bool, ...Bool[]], 1));
      constraints++;
      for (let c = 0; c < colors.length; c++) {
        const owner = puzzle.cells[index];
        const terminal = owner !== ".";
        if (terminal) { solver.add(owner === colors[c] ? nodeVars[c][index] : ctx.Not(nodeVars[c][index])); constraints++; }
        solver.add(ctx.Implies(nodeVars[c][index], eq(incident[index].map(edge => edgeVars[c][edge]), terminal ? 1 : 2)));
        constraints++;
      }
      if (performance.now() - started >= timeoutMs) return result("timeout");
      report?.({ stage: "building", metrics: metrics() });
    }
    edges.forEach(([a, b], edge) => colors.forEach((_, c) => {
      solver.add(ctx.Implies(edgeVars[c][edge], ctx.And(nodeVars[c][a], nodeVars[c][b])));
      constraints++;
    }));
    while (true) {
      const remaining = timeoutMs - (performance.now() - started);
      if (remaining <= 0) return result("timeout");
      solver.set("timeout", Math.max(1, Math.floor(remaining)));
      checks++;
      report?.({ stage: "solving", metrics: metrics() });
      const status = await solver.check();
      completedChecks++;
      // Read statistics only between checks: the WebAssembly engine is busy during check().
      const statistics = solver.statistics();
      let engine: Record<string, number>;
      try { engine = Object.fromEntries([...statistics].filter(entry => Number.isFinite(entry.value)).map(entry => [entry.key, entry.value])); }
      finally { statistics.release(); }
      report?.({ stage: "verifying", metrics: metrics(), engine });
      if (status === "unsat") return result("unsat");
      if (status === "unknown") {
        const reason = solver.reasonUnknown();
        return result(/timeout|canceled|resource/i.test(reason) ? "timeout" : "unknown", {}, reason);
      }
      const model = solver.model();
      const paths: Record<string, number[]> = {};
      let disconnected = false;
      try {
        for (let c = 0; c < colors.length; c++) {
          const color = colors[c];
          const selected = new Set(active.filter(index => ctx.isTrue(model.eval(nodeVars[c][index], true))));
          const links = puzzle.cells.map(() => [] as number[]);
          edges.forEach(([a, b], edge) => {
            if (ctx.isTrue(model.eval(edgeVars[c][edge], true))) { links[a].push(b); links[b].push(a); }
          });
          const [start, end] = puzzle.terminals[color];
          const path = [start];
          let previous = -1;
          let current = start;
          while (current !== end) {
            const next = links[current].find(index => index !== previous);
            if (next === undefined || path.includes(next)) throw new Error("Solver produced an invalid endpoint path.");
            previous = current; current = next; path.push(current);
          }
          path.forEach(index => selected.delete(index));
          paths[color] = path;
          while (selected.size) {
            disconnected = true;
            const component = new Set<number>();
            const pending = [selected.values().next().value as number];
            while (pending.length) {
              const index = pending.pop()!;
              if (component.has(index)) continue;
              component.add(index); selected.delete(index);
              links[index].forEach(next => { if (!component.has(next)) pending.push(next); });
            }
            // Any color visiting this terminal-free region must enter and leave it.
            const boundary = edges.flatMap(([a, b], edge) => component.has(a) !== component.has(b) ? [edgeVars[c][edge]] : []);
            const crossing = boundary.length ? ctx.PbGe(boundary as [Bool, ...Bool[]], boundary.map(() => 1) as [number, ...number[]], 2) : ctx.Bool.val(false);
            solver.add(ctx.Implies(ctx.Or(...[...component].map(index => nodeVars[c][index])), crossing));
            cuts++;
            constraints++;
          }
        }
      } finally { model.release(); }
      if (!disconnected) {
        validateLocalSolution(puzzle, paths);
        report?.({ stage: "verifying", completed: active.length, total: active.length, unit: "cells", metrics: metrics(), engine });
        return result("solved", paths);
      }
    }
  } finally { solver.release(); }
}
