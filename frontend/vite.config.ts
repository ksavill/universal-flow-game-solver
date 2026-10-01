import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import type { IncomingMessage, ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function isolateLocalPrototype(request: IncomingMessage, response: ServerResponse, next: () => void) {
  const path = request.url?.split("?")[0];
  // Isolate the local document and worker assets without changing the legacy
  // document's ability to display images served by a separate API origin.
  if (/^\/local\/?$/.test(path ?? "") || !request.headers.accept?.includes("text/html")) {
    response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
    response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  }
  next();
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const deviceOnly = mode === "static";
  const isolation = (request: IncomingMessage, response: ServerResponse, next: () => void) => {
    if (/^\/(guide|privacy)(\/|$)/.test(request.url?.split("?")[0] ?? "")) {
      next();
      return;
    }
    if (deviceOnly) {
      response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
      response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
    }
    isolateLocalPrototype(request, response, next);
  };
  const usePolling = env.CHOKIDAR_USEPOLLING === "1";
  const intervalRaw = Number(env.CHOKIDAR_INTERVAL ?? "1000");
  const interval = Number.isFinite(intervalRaw) && intervalRaw > 0 ? intervalRaw : 1000;

  return {
    plugins: [react(), {
      name: "isolate-local-prototype",
      transformIndexHtml() {
        if (!deviceOnly) return;
        return [{ tag: "meta", attrs: { name: "google-adsense-account", content: "ca-pub-9792422128121970" }, injectTo: "head" }];
      },
      configureServer(server) { server.middlewares.use(isolation); },
      configurePreviewServer(server) { server.middlewares.use(isolation); },
      generateBundle(_options, bundle) {
        if (!deviceOnly) return;
        for (const chunk of Object.values(bundle)) {
          if (chunk.type !== "chunk") continue;
          const serverModule = Object.keys(chunk.modules).find(id => /\/src\/(api\.ts|App\.tsx|views\/(ImageView|LibraryView|NewPuzzleView|SolveView|DocsView|FlaggedView)\.tsx)$/.test(id.replace(/\\/g, "/")));
          if (serverModule) this.error(`Device-only build contains a server module: ${serverModule}`);
        }
        this.emitFile({ type: "asset", fileName: "_headers", source: readFileSync(new URL("./static/_headers", import.meta.url), "utf8") });
      }
    }],
    worker: { format: "es" },
    define: { global: "globalThis" },
    build: {
      outDir: deviceOnly ? "dist-static" : "dist", manifest: deviceOnly,
      rollupOptions: deviceOnly ? { input: {
        app: fileURLToPath(new URL("./index.html", import.meta.url)),
        guide: fileURLToPath(new URL("./guide/index.html", import.meta.url)),
        privacy: fileURLToPath(new URL("./privacy/index.html", import.meta.url))
      } } : undefined
    },
    server: {
      port: 5173,
      host: true,
      watch: {
        usePolling,
        interval
      }
    }
  };
});
