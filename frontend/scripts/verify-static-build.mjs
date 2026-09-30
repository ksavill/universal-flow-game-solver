import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";
import { createHash } from "node:crypto";

const root = fileURLToPath(new URL("../dist-static/", import.meta.url));
function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [{ path, size: statSync(path).size }];
  });
}
const files = walk(root);
assert(files.length <= 20_000, "Cloudflare Free allows at most 20,000 static files.");
for (const file of files) assert(file.size <= 25 * 1048576, `${relative(root, file.path)} exceeds 25 MiB.`);
assert(!files.some(file => file.path.endsWith(".wasm")), "Uncompressed WASM must not be shipped.");
const manifest = JSON.parse(readFileSync(join(root, "local-runtime/manifest.json"), "utf8"));
const packed = readFileSync(join(root, "local-runtime", manifest.wasm));
const unpacked = gunzipSync(packed);
assert.equal(packed.length, manifest.compressedBytes);
assert.equal(unpacked.length, manifest.bytes);
assert.equal(createHash("sha256").update(unpacked).digest("hex"), manifest.sha256);
assert.deepEqual(unpacked, readFileSync(new URL("../node_modules/z3-solver/build/z3-built.wasm", import.meta.url)));
assert(WebAssembly.validate(unpacked), "Solver must remain valid WebAssembly after compression.");
assert(readFileSync(join(root, "local-runtime", manifest.script), "utf8").includes("globalThis.initZ3 = initZ3"));
for (const name of ["Z3-LICENSE.txt", "ASYNC-MUTEX-LICENSE.txt", "TSLIB-LICENSE.txt"]) assert(statSync(join(root, "local-runtime", name)).size > 0);
const headers = readFileSync(join(root, "_headers"), "utf8");
for (const required of ["Cross-Origin-Opener-Policy: same-origin", "Cross-Origin-Embedder-Policy: require-corp", "connect-src 'self'"]) assert(headers.includes(required), `Missing ${required}`);
for (const file of files.filter(file => file.path.endsWith(".js"))) {
  const text = readFileSync(file.path, "utf8");
  assert(!/VITE_API_URL|localhost:8000|\/image-imports|\/image\/uploads/.test(text), `Server API code leaked into ${relative(root, file.path)}`);
}
const viteManifest = JSON.parse(readFileSync(join(root, ".vite/manifest.json"), "utf8"));
assert(Object.keys(viteManifest).some(key => key.endsWith("StaticApp.tsx")), "Static application entry is missing.");
const largest = [...files].sort((a, b) => b.size - a.size)[0];
console.log(JSON.stringify({
  files: files.length,
  totalMiB: +(files.reduce((sum, file) => sum + file.size, 0) / 1048576).toFixed(2),
  largestAsset: relative(root, largest.path),
  largestMiB: +(largest.size / 1048576).toFixed(2),
  cloudflareAssetLimitMiB: 25,
  wasmIntegrity: "matches installed Z3; valid WebAssembly",
  serverApi: "excluded"
}, null, 2));
