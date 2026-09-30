# Device-only Cloudflare deployment

The static edition runs image processing, board editing and Z3 solving in the
visitor's browser. It uses the same local UI as `/local` in the full application,
with a separate navigation shell. Server processing, the Python API client,
server libraries, screenshot archives and flagged reviews are excluded from the
static build. Help is bundled locally. No Python service or database is needed.

## Build and preview

Use Node 22.12 or newer (a current Node 22 LTS release is recommended).

```powershell
cd frontend
npm ci
npm test
npm run build:static
npm run preview:static
```

Open `http://127.0.0.1:8787`. The preview uses Cloudflare Wrangler's local runtime
to exercise the actual static header and SPA configuration. `npm run dev:static`
is available for development with Vite. The regular `npm run build` still builds
the full application into `dist`; static output is isolated in `dist-static`.

## Cloudflare configuration

`frontend/wrangler.jsonc` deploys only `dist-static`, with SPA fallback. There is
no Worker script, `run_worker_first`, database, R2 bucket, or paid binding.
Requests are served as static assets. The configured name is
`flow-puzzle-solver`, with `flowpuzzlesolver.net` as its production custom domain.

After authenticating to the intended Cloudflare account:

```powershell
npm run deploy:static
```

For Cloudflare Git integration, select `frontend` as the root directory and
`npm run build:static` as the build command, then `npx wrangler deploy` as the
deploy command. Production builds follow `main`, with `NODE_VERSION=22` and
preview builds disabled. No
`VITE_API_URL` or API secrets are needed. Keep deployment credentials in the
hosting provider's secrets, never in frontend environment variables.

Cloudflare Pages can also serve `dist-static` with build command
`npm run build:static` and root `frontend`. Its default SPA fallback works when
no top-level `404.html` is present. The generated `_headers` applies to both
platforms. The documented local preview and deploy commands target Workers
Static Assets.

## Runtime packaging and headers

The preparation script reads Z3 from the pinned npm dependency and emits:

- A gzip-compressed WASM asset and patched JavaScript loader, both with content
  hashes in their filenames.
- A small manifest containing filenames, byte counts and SHA-256 of the WASM.
- The Z3, async-mutex and tslib license notices.

Only the compressed WASM is deployed. The browser worker downloads it,
decompresses it with `DecompressionStream`, checks its size and SHA-256, and
passes the bytes to Emscripten through `wasmBinary`. It never uploads puzzle data.
The 33.32 MiB unpacked runtime compresses to approximately 7.54 MiB. Compression
reduces transfer and deployed asset size; it does not reduce the solver's working
memory needs. Current browsers with shared-memory WebAssembly and gzip stream
decompression are required.

`frontend/static/_headers` is emitted only into the static build. It sets COOP
`same-origin` and COEP `require-corp` for documents and workers. A content security
policy permits same-origin runtime downloads and WebAssembly while disallowing
external network connections. Images use local blob/data URLs. Hashed assets are
cacheable for one year; the manifest is revalidated, so deployments do not mix
runtime versions. Do not add `Content-Encoding: gzip` to the packed WASM: the
application itself decompresses the downloaded file.

## Routes and storage

The processing panel shows live stages, elapsed time, download bytes, image scan
progress, board dimensions, and solver counters. Expand its details for stage
timings and Z3 statistics snapshots (taken between solver checks). Search itself
has no percentage or ETA; the remaining search budget excludes runtime loading.
Stop terminates the worker and ignores queued messages. Browser-native image
decoding cannot be interrupted mid-call, but a stopped decode is discarded and
its bitmap closed without starting detection. No pause/resume is implemented.
Finished/stopped statistics stay visible until another operation or board edit;
they are also included in optional, local-only diagnostic reports.

- `/`, `/local`, `/screenshot`: local solver, regardless of any old Server preference.
- `/create`: blank local board editor.
- `/library`: local saved puzzles and reports.
- `/docs` and the old known documentation paths: bundled usage help.
- Other paths, including old import/puzzle records and flagged reviews: an
  explanatory unavailable-page view. They never trigger API requests.

IndexedDB belongs to the browser and origin. Localhost data will not automatically
appear on a deployed domain; export/import `.flow` files to move puzzles.
Reports are optional and local only (maximum 256 KiB per report, 50 reports,
5 MiB total and 30-day expiry checked on inbox access). Actual report submission
would require a separately designed endpoint and storage policy.

## Release checks

`build:static` runs the size/integrity audit automatically; `check:static` can
repeat it without rebuilding. It checks every asset against 25 MiB, total file
count against 20,000, excludes uncompressed WASM, verifies lossless decompression
against the installed Z3 binary, validates WebAssembly, checks license files and
required headers, and scans for server API leakage. Vite also rejects a static
bundle containing any of the server application/view modules or API client.

Before a public release, verify in the deployed HTTPS site:

1. Root, `/local`, `/create`, `/library` and `/docs` work on direct visits and reload.
2. A demo and a real JPEG/PNG can be detected, edited and solved with Python stopped.
3. Save/reload/export/import and optional local reports work.
4. Network activity consists only of application/runtime assets; errors and
   cancellation never submit images or puzzle contents.
5. Mobile Safari and Chrome can load and solve without excessive memory use.

Cloudflare references: [static billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/),
[asset limits](https://developers.cloudflare.com/workers/platform/limits/),
[SPA routing](https://developers.cloudflare.com/workers/static-assets/routing/single-page-application/),
[headers](https://developers.cloudflare.com/workers/static-assets/headers/).

## Verified locally (2026-09-30)

- 46 tests across eleven files pass, including worker cancellation races, stopped
  image decoding, stream byte counts, image progress and real Z3 statistics.
- Both full and static production builds pass. The full build retains its
  existing large-chunk warning for the documentation/diagram dependencies.
- Static output: 14 files, approximately 8.53 MiB total; largest file 7,904,067 bytes (7.54 MiB).
- Wrangler 4.145.0 parses all four header rules; deployment dry run passes with
  no bindings. HTTP checks confirm isolation headers and SPA navigation fallback
  on root, local, create, library, docs and flagged paths.
- In the Chromium-based in-app browser against Wrangler on port 8787: demo image
  detection/solving and real `reference_puzzle_images/IMG_3202.PNG` detection and
  solving pass. The real board solves in about 0.19 seconds after runtime loading.
- Saved library persists after reload; exported `.flow` downloads and imports;
  the reimported board solves at a 390-pixel viewport. Help and Create survive
  direct refresh. The old Flagged URL shows the unavailable-page view.
- A diagnostic report previews and saves locally. Browser error/warning logs are
  empty, and observed request logs contain document and static asset GETs only
  (plus the explicit HTTP header checks), with no API or upload requests.
- Desktop and phone-width captures are in ignored `out/cloudflare-static/`.
- Progress/Stop checks: demo detection shows all ten dots and 25 examined cells;
  solved boards populate timings, constraints and Z3 snapshots. Stopping an
  active 20 × 20 search freezes its elapsed time and restores editing without a
  late result. At a 390-pixel viewport, Stop during setup and a successful retry
  both pass. No browser warnings or errors were observed in these checks.

## Published deployment (2026-09-30)

Production is live at https://flowpuzzlesolver.net on Cloudflare Workers Static
Assets, connected to `ksavill/universal-flow-game-solver` on GitHub. The initial
deployment of commit `052a189` passed Cloudflare's Linux build and static audit.
Pushes to `main` trigger production builds using the settings above.

The zone's Always Use HTTPS setting redirects HTTP requests to HTTPS. A Single
Redirect rule matches `www.flowpuzzlesolver.net` and redirects to the HTTPS apex,
preserving the path and query string. Its proxied `www` A record uses Cloudflare's
suggested placeholder `192.0.2.1`; the redirect runs before an origin is contacted.
These two zone settings are managed in the Cloudflare dashboard. The apex custom
domain is also recorded in `frontend/wrangler.jsonc` for subsequent deployments.

Live verification:

- HTTPS serves the required COOP, COEP and CSP headers. Create, Library and Help
  direct URLs return the SPA with isolation headers.
- HTTP apex and HTTP/HTTPS WWW requests return 301 redirects with path and query
  preserved.
- Edge detects and solves the demo, showing download sizes and stage statistics.
- The in-app Chromium browser detects and solves the real
  `reference_puzzle_images/IMG_3202.PNG` screenshot on the public HTTPS site.
- Physical Safari/Android device testing remains a separate release check.
