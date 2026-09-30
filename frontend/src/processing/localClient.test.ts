import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeLocalImage, runLocalWorker } from "./localClient";

function fakeWorker() {
  return { onmessage: null, onerror: null, postMessage: vi.fn(), terminate: vi.fn() } as unknown as Worker;
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("local operation cancellation", () => {
  it("terminates an active worker and rejects queued progress/results after Stop", async () => {
    const worker = fakeWorker();
    const controller = new AbortController();
    const progress = vi.fn();
    const pending = runLocalWorker(worker, {}, controller.signal, 5000, progress);
    const receive = worker.onmessage!;
    receive.call(worker, new MessageEvent("message", { data: { kind: "progress", update: { stage: "solving" } } }));
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    receive.call(worker, new MessageEvent("message", { data: { kind: "progress", update: { stage: "verifying" } } }));
    receive.call(worker, new MessageEvent("message", { data: { kind: "result", result: "stale" } }));
    await rejection;
    expect(progress).toHaveBeenCalledTimes(1);
    expect(worker.terminate).toHaveBeenCalledTimes(1);
    expect(worker.onmessage).toBeNull();
  });
  it("does not start a worker if the signal was already aborted", async () => {
    const worker = fakeWorker();
    const controller = new AbortController(); controller.abort();
    await expect(runLocalWorker(worker, {}, controller.signal, 5000)).rejects.toMatchObject({ name: "AbortError" });
    expect(worker.postMessage).not.toHaveBeenCalled();
    expect(worker.terminate).toHaveBeenCalledOnce();
  });
  it("terminates a stuck worker at the outer deadline", async () => {
    vi.useFakeTimers();
    const worker = fakeWorker();
    const pending = runLocalWorker(worker, {}, new AbortController().signal, 100);
    const rejection = expect(pending).rejects.toThrow("time limit");
    await vi.advanceTimersByTimeAsync(100);
    await rejection;
    expect(worker.terminate).toHaveBeenCalledOnce();
  });
  it("cleans up both completed and failed-to-start workers", async () => {
    const worker = fakeWorker();
    const controller = new AbortController();
    const pending = runLocalWorker(worker, {}, controller.signal, 5000);
    worker.onmessage!.call(worker, new MessageEvent("message", { data: { kind: "result", result: 42 } }));
    expect(await pending).toBe(42);
    controller.abort();
    expect(worker.terminate).toHaveBeenCalledOnce();
    const broken = fakeWorker();
    vi.mocked(broken.postMessage).mockImplementation(() => { throw new Error("Cannot clone"); });
    await expect(runLocalWorker(broken, {}, new AbortController().signal, 5000)).rejects.toThrow("Cannot clone");
    expect(broken.terminate).toHaveBeenCalledOnce();
  });
  it("discards and closes a bitmap when Stop is pressed during native image decoding", async () => {
    const header = new Uint8Array(24); header.set([137, 80, 78, 71, 13, 10, 26, 10]);
    const view = new DataView(header.buffer); view.setUint32(16, 10); view.setUint32(20, 10);
    const controller = new AbortController();
    const bitmap = { close: vi.fn(), width: 10, height: 10 };
    vi.stubGlobal("createImageBitmap", vi.fn(async () => { controller.abort(); return bitmap; }));
    await expect(decodeLocalImage(new File([header], "test.png"), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(bitmap.close).toHaveBeenCalledOnce();
  });
});
