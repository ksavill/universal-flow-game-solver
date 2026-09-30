import { describe, expect, it, vi } from "vitest";
import { finishTrace, startTrace, throttleProgress, updateTrace } from "./localProgress";

describe("processing telemetry", () => {
  it("keeps stage timings and freezes a stopped trace against late updates", () => {
    let trace = startTrace("reading", 100);
    trace = updateTrace(trace, { stage: "outline", metrics: { imageWidth: 100 } }, 150);
    trace = updateTrace(trace, { stage: "outline", completed: 20, total: 50 }, 200);
    trace = finishTrace(trace, "stopped", 250);
    expect(trace.stages.map(entry => entry.elapsedMs)).toEqual([50, 100]);
    expect(trace.metrics.imageWidth).toBe(100);
    expect(trace.endedAt).toBe(250);
    expect(updateTrace(trace, { stage: "verifying" }, 1000)).toBe(trace);
    expect(finishTrace(trace, "finished", 1000)).toBe(trace);
  });
  it("throttles hot loops without dropping stage transitions or completion", () => {
    const clock = vi.spyOn(performance, "now").mockReturnValue(0);
    try {
      const send = vi.fn(); const report = throttleProgress(send);
      for (let i = 0; i <= 1000; i++) report({ stage: "outline", completed: i, total: 1000 });
      report({ stage: "rectifying" });
      expect(send.mock.calls.map(([update]) => [update.stage, update.completed])).toEqual([["outline", 0], ["outline", 1000], ["rectifying", undefined]]);
    } finally { clock.mockRestore(); }
  });
});
