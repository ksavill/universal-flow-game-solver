import { init } from "z3-solver";
import type { LocalPuzzle } from "../core/localPuzzle";
import { solveLocalPuzzle } from "../solver/localSolver";
import { readSolverDownload, unpackSolver, validateRuntimeManifest } from "../solver/solverRuntime";
import { throttleProgress } from "../processing/localProgress";

// The Emscripten runtime must remain a separate script so its nested workers
// can load it. Never fetch puzzle inputs or submit them to a server.
const scope = self as unknown as {
  postMessage: (value: unknown) => void;
  onmessage: ((event: MessageEvent<{ puzzle: LocalPuzzle; timeoutMs: number }>) => void) | null;
  initZ3?: unknown;
  global?: unknown;
};
scope.global = self;
scope.onmessage = async event => {
  const report = throttleProgress(update => scope.postMessage({ kind: "progress", update }));
  try {
    if (!self.crossOriginIsolated) throw new Error("Local solving needs cross-origin isolation. Start the Vite server or use the supplied production headers.");
    report({ stage: "manifest" });
    const runtimeBase = new URL(`${import.meta.env.BASE_URL}local-runtime/`, self.location.origin);
    const manifestResponse = await fetch(new URL("manifest.json", runtimeBase), { cache: "no-cache" });
    if (!manifestResponse.ok) throw new Error("Could not download the solver manifest. Check your connection and retry.");
    const manifest = validateRuntimeManifest(await manifestResponse.json());
    report({ stage: "downloading", completed: 0, total: manifest.compressedBytes, unit: "bytes" });
    const binaryResponse = await fetch(new URL(manifest.wasm, runtimeBase));
    if (!binaryResponse.ok) throw new Error("Could not download the solver. Check your connection and retry.");
    const packed = await readSolverDownload(binaryResponse, manifest, report);
    report({ stage: "unpacking", metrics: { runtimeBytes: manifest.bytes } });
    const wasmBinary = await unpackSolver(packed, manifest);
    report({ stage: "initializing" });
    const scriptUrl = new URL(manifest.script, runtimeBase).href;
    await import(/* @vite-ignore */ scriptUrl);
    const api = await init({
      wasmBinary,
      mainScriptUrlOrBlob: scriptUrl,
      locateFile: file => new URL(file, runtimeBase).href
    });
    try {
      const ctx = new api.Context("main");
      const result = await solveLocalPuzzle(event.data.puzzle, ctx, event.data.timeoutMs, report);
      scope.postMessage({ kind: "result", result });
    } finally {
      api.em.PThread.terminateAllThreads();
    }
  } catch (error) {
    scope.postMessage({ kind: "error", message: error instanceof Error ? error.message : String(error) });
  }
};
