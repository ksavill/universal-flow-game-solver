import { mkdirSync, copyFileSync, readFileSync, writeFileSync, readdirSync, unlinkSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { createHash } from "node:crypto";

const source = new URL("../node_modules/z3-solver/", import.meta.url);
const target = new URL("../public/local-runtime/", import.meta.url);
mkdirSync(target, { recursive: true });
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const wasm = readFileSync(new URL("build/z3-built.wasm", source));
const compressed = gzipSync(wasm, { level: 9 });
const script = readFileSync(new URL("build/z3-built.js", source), "utf8") +
  "\nif (typeof globalThis !== 'undefined') globalThis.initZ3 = initZ3;\n";
const sha256 = digest(wasm);
const manifest = {
  script: `z3-${digest(script).slice(0, 16)}.js`,
  wasm: `z3-${sha256.slice(0, 16)}.wasm.gz`,
  bytes: wasm.length,
  compressedBytes: compressed.length,
  sha256
};
if (compressed.length > 25 * 1024 * 1024) throw new Error("Compressed solver exceeds Cloudflare's 25 MiB asset limit.");
// Remove only generated runtime assets, including the oversized legacy binary.
for (const name of readdirSync(target)) {
  if (/^z3-(built|[a-f0-9]{16})\.(js|wasm(?:\.gz)?)$/.test(name)) unlinkSync(new URL(name, target));
}
writeFileSync(new URL(manifest.script, target), script);
writeFileSync(new URL(manifest.wasm, target), compressed);
writeFileSync(new URL("manifest.json", target), JSON.stringify(manifest, null, 2) + "\n");
console.log(`Local solver: ${(compressed.length / 1048576).toFixed(2)} MiB gzip (${(wasm.length / 1048576).toFixed(2)} MiB unpacked)`);
copyFileSync(fileURLToPath(new URL("LICENSE.txt", source)), fileURLToPath(new URL("Z3-LICENSE.txt", target)));
copyFileSync(fileURLToPath(new URL("../node_modules/async-mutex/LICENSE", import.meta.url)), fileURLToPath(new URL("ASYNC-MUTEX-LICENSE.txt", target)));
copyFileSync(fileURLToPath(new URL("../node_modules/tslib/LICENSE.txt", import.meta.url)), fileURLToPath(new URL("TSLIB-LICENSE.txt", target)));
