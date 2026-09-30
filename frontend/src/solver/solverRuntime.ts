import type { ProgressReporter } from "../processing/localProgress";

export type RuntimeManifest = { script: string; wasm: string; bytes: number; compressedBytes: number; sha256: string };

export async function readSolverDownload(response: Response, manifest: RuntimeManifest, report?: ProgressReporter): Promise<Uint8Array> {
  if (!response.body) throw new Error("The solver download is empty. Please retry.");
  const reader = response.body.getReader();
  const packed = new Uint8Array(manifest.compressedBytes);
  let offset = 0;
  report?.({ stage: "downloading", completed: 0, total: packed.length, unit: "bytes" });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (offset + value.length > packed.length) throw new Error("The solver download has an unexpected size.");
      packed.set(value, offset);
      offset += value.length;
      report?.({ stage: "downloading", completed: offset, total: packed.length, unit: "bytes", metrics: { downloadBytes: offset } });
    }
  } finally { await reader.cancel(); }
  if (offset !== packed.length) throw new Error("The solver download is incomplete. Please retry.");
  return packed;
}

export function validateRuntimeManifest(value: unknown): RuntimeManifest {
  const entry = value as RuntimeManifest | null;
  if (!entry || !/^z3-[a-f0-9]{16}\.js$/.test(entry.script) || !/^z3-[a-f0-9]{16}\.wasm\.gz$/.test(entry.wasm)
    || !/^[a-f0-9]{64}$/.test(entry.sha256) || !Number.isInteger(entry.bytes) || entry.bytes < 8 || entry.bytes > 64 * 1024 * 1024
    || !Number.isInteger(entry.compressedBytes) || entry.compressedBytes < 1 || entry.compressedBytes > 25 * 1024 * 1024) {
    throw new Error("The solver download manifest is invalid. Reload the page and retry.");
  }
  return entry;
}

export async function unpackSolver(packed: Uint8Array, manifest: RuntimeManifest): Promise<Uint8Array> {
  if (typeof DecompressionStream === "undefined") throw new Error("This browser cannot unpack the solver. Please update your browser.");
  if (packed.byteLength !== manifest.compressedBytes) throw new Error("The solver download is incomplete. Please retry.");
  const reader = new Blob([new Uint8Array(packed)]).stream().pipeThrough(new DecompressionStream("gzip")).getReader();
  const binary = new Uint8Array(manifest.bytes);
  let offset = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (offset + value.length > binary.length) throw new Error("The solver download has an unexpected size.");
      binary.set(value, offset);
      offset += value.length;
    }
  } finally { await reader.cancel(); }
  if (offset !== binary.length) throw new Error("The solver download has an unexpected size.");
  const digest = await crypto.subtle.digest("SHA-256", binary);
  const hash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  if (hash !== manifest.sha256) throw new Error("The solver download failed its integrity check. Reload the page and retry.");
  return binary;
}
