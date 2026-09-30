export const STAGE_LABELS = {
  reading: "Reading image", resizing: "Preparing image", outline: "Finding the board",
  rectifying: "Straightening the board", detecting: "Detecting dots", validation: "Checking the puzzle",
  manifest: "Preparing the solver download", downloading: "Downloading the solver",
  unpacking: "Unpacking and checking the solver", initializing: "Starting the solver",
  building: "Building puzzle constraints", solving: "Searching for a solution", verifying: "Verifying the solution"
} as const;
export type ProcessingStage = keyof typeof STAGE_LABELS;
export type ProgressUpdate = {
  stage: ProcessingStage;
  completed?: number;
  total?: number;
  unit?: "bytes" | "lines" | "rows" | "cells" | "constraints";
  metrics?: Record<string, number>;
  engine?: Record<string, number>;
};
export type ProgressReporter = (update: ProgressUpdate) => void;
export type ProcessingTrace = {
  startedAt: number;
  endedAt?: number;
  status: "running" | "finished" | "stopped" | "error";
  current: ProgressUpdate;
  metrics: Record<string, number>;
  engine: Record<string, number>;
  stages: { stage: ProcessingStage; startedAt: number; elapsedMs: number }[];
};
export function startTrace(stage: ProcessingStage, now: number): ProcessingTrace {
  return { startedAt: now, status: "running", current: { stage }, metrics: {}, engine: {}, stages: [{ stage, startedAt: now, elapsedMs: 0 }] };
}
export function updateTrace(trace: ProcessingTrace, update: ProgressUpdate, now: number): ProcessingTrace {
  if (trace.status !== "running") return trace;
  const stages = trace.stages.map((entry, i) => i === trace.stages.length - 1 ? { ...entry, elapsedMs: now - entry.startedAt } : entry);
  if (trace.current.stage !== update.stage) stages.push({ stage: update.stage, startedAt: now, elapsedMs: 0 });
  return { ...trace, current: update, stages, metrics: { ...trace.metrics, ...update.metrics }, engine: update.engine ?? trace.engine };
}
export function finishTrace(trace: ProcessingTrace, status: Exclude<ProcessingTrace["status"], "running">, now: number): ProcessingTrace {
  if (trace.status !== "running") return trace;
  return { ...updateTrace(trace, trace.current, now), status, endedAt: now };
}

// Bound message traffic while always delivering stage changes and completion.
export function throttleProgress(send: ProgressReporter, intervalMs = 80): ProgressReporter {
  let last = -Infinity;
  let stage: ProcessingStage | undefined;
  return update => {
    const now = performance.now();
    if (update.stage !== stage || now - last >= intervalMs || update.completed === update.total && update.total !== undefined) {
      send(update); last = now; stage = update.stage;
    }
  };
}
