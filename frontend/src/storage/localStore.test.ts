import { describe, expect, it } from "vitest";
import { REPORT_POLICY, retainReports, type FeedbackReport } from "./localStore";

const report = (id: number, createdAt: number, size = 0): FeedbackReport => ({ id: String(id), createdAt, version: "test", description: "problem", stage: "detection", outcome: "error", puzzleText: "", puzzleHash: "", diagnostics: {}, attachment: size ? { mime: "image/jpeg", dataUrl: "x".repeat(size) } : undefined });

describe("bounded report retention", () => {
  it("expires old reports and keeps the newest fifty", () => {
    const now = Date.now();
    const reports = Array.from({ length: 60 }, (_, i) => report(i, now - i));
    reports.push(report(99, now - REPORT_POLICY.ttlMs - 1));
    const kept = retainReports(reports, now);
    expect(kept).toHaveLength(50); expect(kept[0].id).toBe("0"); expect(kept[49].id).toBe("49");
  });
  it("enforces both report and total byte limits", () => {
    const now = Date.now();
    const reports = Array.from({ length: 40 }, (_, i) => report(i, now - i, 250_000));
    reports.push(report(100, now, REPORT_POLICY.maxReportBytes + 1));
    const kept = retainReports(reports, now);
    expect(kept.length).toBeLessThan(40);
    expect(kept.some(value => value.id === "100")).toBe(false);
    expect(new TextEncoder().encode(kept.map(value => JSON.stringify(value)).join("")).length).toBeLessThanOrEqual(REPORT_POLICY.maxBytes);
  });
});
