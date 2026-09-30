import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { readSolverDownload, unpackSolver, validateRuntimeManifest } from "./solverRuntime";
import type { ProgressUpdate } from "../processing/localProgress";

const binary = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]);
const packed = gzipSync(binary);
const manifest = { script: "z3-0123456789abcdef.js", wasm: "z3-0123456789abcdef.wasm.gz", bytes: binary.length, compressedBytes: packed.length, sha256: createHash("sha256").update(binary).digest("hex") };

describe("compressed solver loading", () => {
  it("reports actual streamed bytes even without a Content-Length header", async () => {
    const events: ProgressUpdate[] = [];
    const body = new ReadableStream({ start(controller) { controller.enqueue(packed.subarray(0, 5)); controller.enqueue(packed.subarray(5)); controller.close(); } });
    expect(await readSolverDownload(new Response(body), manifest, update => events.push(update))).toEqual(new Uint8Array(packed));
    expect(events.map(event => event.completed)).toEqual([0, 5, packed.length]);
    expect(events.every(event => event.total === packed.length)).toBe(true);
  });
  it("rejects incomplete and oversized streams before unpacking", async () => {
    await expect(readSolverDownload(new Response(packed.subarray(1)), manifest)).rejects.toThrow("incomplete");
    await expect(readSolverDownload(new Response(new Uint8Array(packed.length + 1)), manifest)).rejects.toThrow("unexpected size");
  });
  it("reconstructs identical WebAssembly bytes", async () => {
    const result = await unpackSolver(packed, validateRuntimeManifest(manifest));
    expect(result).toEqual(binary);
    expect(WebAssembly.validate(new Uint8Array(result))).toBe(true);
  });
  it("rejects truncated downloads", async () => {
    await expect(unpackSolver(packed.subarray(1), manifest)).rejects.toThrow("incomplete");
  });
  it("rejects wrong contents and wrong decompressed sizes", async () => {
    await expect(unpackSolver(packed, { ...manifest, sha256: "0".repeat(64) })).rejects.toThrow("integrity");
    await expect(unpackSolver(packed, { ...manifest, bytes: 4 })).rejects.toThrow("unexpected size");
    await expect(unpackSolver(packed, { ...manifest, bytes: 16 })).rejects.toThrow("unexpected size");
  });
  it("rejects unexpected paths and oversized allocations", () => {
    expect(() => validateRuntimeManifest({ ...manifest, script: "https://example.com/solver.js" })).toThrow("invalid");
    expect(() => validateRuntimeManifest({ ...manifest, bytes: 1e9 })).toThrow("invalid");
  });
});
