import type { LocalPuzzle } from "../core/localPuzzle";
import type { LocalSolveResult } from "../solver/localSolver";
import type { DetectedBoard, LocalDetection, Pixels, Point } from "./localImage";
import { encodedImageSize } from "./localImage";
import type { ProgressReporter } from "./localProgress";

export type ImageWorkerResult = { kind: "outline"; board: DetectedBoard | null } | { kind: "detected"; detection: LocalDetection; prepared: Pixels };

export function runLocalWorker<T>(worker: Worker, payload: unknown, signal: AbortSignal, maxMs: number, progress?: ProgressReporter): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (error?: Error, value?: T) => {
      if (settled) return;
      settled = true;
      worker.onmessage = null; worker.onerror = null;
      clearTimeout(timer); signal.removeEventListener("abort", abort); worker.terminate();
      if (error) reject(error); else resolve(value!);
    };
    const abort = () => finish(new DOMException("Cancelled", "AbortError"));
    timer = setTimeout(() => finish(new Error("The local operation exceeded its time limit. Your image has not been uploaded.")), maxMs);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) { abort(); return; }
    worker.onerror = event => finish(new Error(event.message || "The browser worker could not start."));
    worker.onmessage = event => {
      if (settled || signal.aborted) return;
      if (event.data.kind === "progress") { progress?.(event.data.update); return; }
      if (event.data.kind === "error") finish(new Error(event.data.message));
      else finish(undefined, event.data.kind === "result" ? event.data.result : event.data);
    };
    try { worker.postMessage(payload); } catch (error) { finish(error instanceof Error ? error : new Error(String(error))); }
  });
}

export function detectLocally(pixels: Pixels, signal: AbortSignal, options?: { corners: Point[]; rows: number; cols: number }, progress?: ProgressReporter): Promise<ImageWorkerResult> {
  return runLocalWorker(new Worker(new URL("../workers/localImage.worker.ts", import.meta.url), { type: "module" }), { pixels, ...options }, signal, 20_000, progress);
}

export function solveLocally(puzzle: LocalPuzzle, timeoutMs: number, signal: AbortSignal, progress: ProgressReporter): Promise<LocalSolveResult> {
  return runLocalWorker(new Worker(new URL("../workers/localSolver.worker.ts", import.meta.url), { type: "module" }), { puzzle, timeoutMs }, signal, timeoutMs + 60_000, progress);
}

export async function decodeLocalImage(file: File, signal?: AbortSignal, report?: ProgressReporter): Promise<Pixels> {
  signal?.throwIfAborted();
  report?.({ stage: "reading", metrics: { inputBytes: file.size } });
  if (file.size > 20 * 1024 * 1024) throw new Error("Choose a JPEG or PNG smaller than 20 MB.");
  const dimensions = encodedImageSize(new Uint8Array(await file.slice(0, 512 * 1024).arrayBuffer()));
  signal?.throwIfAborted();
  report?.({ stage: "reading", metrics: { originalWidth: dimensions.width, originalHeight: dimensions.height } });
  if (!dimensions.width || !dimensions.height || dimensions.width * dimensions.height > 40_000_000) throw new Error("The image exceeds the prototype's 40 megapixel limit or has invalid dimensions. Resize it first.");
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  try {
    signal?.throwIfAborted();
    report?.({ stage: "resizing" });
    if (bitmap.width * bitmap.height > 40_000_000) throw new Error("The image exceeds the prototype's 40 megapixel limit. Resize it first.");
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("This browser cannot read image pixels.");
    context.fillStyle = "#000"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    report?.({ stage: "resizing", metrics: { imageWidth: canvas.width, imageHeight: canvas.height } });
    return { width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data };
  } finally { bitmap.close(); }
}

export function pixelsCanvas(pixels: Pixels): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = pixels.width; canvas.height = pixels.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas is unavailable.");
  context.putImageData(new ImageData(new Uint8ClampedArray(pixels.data), pixels.width, pixels.height), 0, 0);
  return canvas;
}

export function reportAttachment(pixels: Pixels): string {
  const source = pixelsCanvas(pixels);
  const target = document.createElement("canvas");
  const scale = Math.min(1, 800 / Math.max(source.width, source.height));
  target.width = Math.round(source.width * scale); target.height = Math.round(source.height * scale);
  target.getContext("2d")!.drawImage(source, 0, 0, target.width, target.height);
  // Canvas encoding strips EXIF, device and location metadata.
  return target.toDataURL("image/jpeg", .7);
}
