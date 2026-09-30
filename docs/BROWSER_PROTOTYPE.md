# Browser prototype

For the device-only public build and Cloudflare setup, see
[Cloudflare static deployment](CLOUDFLARE_STATIC.md). It includes the local
editor, solver and saved library, and excludes the server processing UI.

The `/local` page is an initial local-processing implementation in the existing
repository. It does not call the Python API or transmit selected images,
puzzles, or feedback reports. The existing importer and solver remain available
for comparison and for variants the prototype does not yet support.

## Try it

```powershell
cd frontend
npm install
npm run dev
```

Open the server URL with `/local` appended, or choose **Solve → This device** in
the navigation. **Solve** reopens whichever mode was chosen last.
The Python backend is not required for this page. Choose, drop or paste a
JPEG/PNG (or **Try a demo**). When the grid is found automatically, the page
goes straight to **Check the dots**; otherwise drag the four yellow corner
handles onto the board corners (a magnifier appears while dragging), set rows
and columns, and choose **Find dots**. Then choose **Solve**.

All detected boards require visual review. Tap a cell to add, change or remove
a dot, or mark it as a hole; colors without exactly two dots are circled and
block solving. **Original** shows the cropped image for comparison, and
**Edit as text** (in the ⋯ menu) remains available. On a keyboard, focus the
board and use the arrow keys; type a letter to place that color, `.` to clear
and `#` for a hole; a screen reader announces each cell. Boards whose cells
would be smaller than about 30 px get a zoom button (on by default on touch
screens) that shows 44 px cells in a pannable view. Successful pairing alone
cannot prove that the detector found every pair. Use an unplayed regular square board;
the prototype does not recognize bridges, holes, walls, or warp ports in images.
Holes can be entered manually as `#` cells. Inputs containing bridges or another
declared geometry are rejected by the local parser.

## Implemented boundary

| Area | Prototype behavior |
| --- | --- |
| Input | JPEG/PNG signatures and dimensions checked before decode; 20 MB and 40 megapixel limits; working image resized to 1,600 pixels on its longest side |
| Screenshots | Conservative regular-line detection and automatic board outline |
| Photos | Four user-selected corners, projective rectification, bilinear sampling |
| Terminals | Center-region color samples, normalized RGB grouping, explicit incomplete-pair warnings |
| Puzzle model | Square `.flow`, 2–20 rows/columns, A–Z endpoints, holes, fill true/false |
| Solver | Boolean node/edge constraints in Z3, incremental cuts removing detached cycles, independent path verification |
| Responsiveness | Detection and solving in disposable Web Workers; Cancel terminates the operation; no stale results after cancellation |
| Outcomes | Solved, proven UNSAT for the entered puzzle, timeout, unknown, input/runtime errors |
| Library | IndexedDB, up to 100 puzzles, explicit deletion and `.flow` import/export; source images are not archived |
| Feedback | Description, version, stage, outcome, puzzle text/hash, diagnostics; image attachment off by default; full preview before saving/downloading |
| Report retention | Maximum 256 KB/report, 50 reports, 5 MB total, 30-day expiry checked on inbox access; oldest reports evicted as limits require |

The report image is resized to at most 800 pixels and re-encoded as JPEG using
Canvas, removing file metadata. It may contain the whole photo, not just the
board; the preview makes this explicit. Detection failure does not prevent a
text-only or optional-image report. Reports never create server requests.

Local storage is device/origin-specific and can be cleared or evicted by the
browser. Export important puzzles and reports. Local puzzles are kept in a
separate store and are not affected by inbox expiry.

## Runtime and hosting

`predev` and `prebuild` package the pinned npm Z3 runtime and its MIT license into
the generated `frontend/public/local-runtime/` directory. Generated assets are
ignored by Git. The packaging script exposes the classic Emscripten factory for
module-worker use and compresses the unchanged WASM binary. The browser downloads
the approximately 7.54 MiB gzip asset when solving starts, unpacks it, and verifies
its size and SHA-256 before initialization. The uncompressed runtime is 33.32 MiB.

Z3 requires `SharedArrayBuffer`, a secure context, and cross-origin isolation.
Vite dev/preview middleware and the supplied production nginx configuration
set COOP `same-origin` and COEP `require-corp` for `/local` and worker resources.
Transitions into or out of Local use document navigation so the correct headers
take effect. The existing server importer document is not isolated, preserving
its ability to display images from a separate API origin.

For a different static host, reproduce the headers on `/local` and its worker
resources, serve JS and gzip assets with the correct MIME types, and serve the app
document at `/local`. Production needs HTTPS; loopback URLs are suitable for
development. No external CDN is used for the processing runtime.

The page still needs to download the app and solver assets from its host. A
service worker/offline installation is not implemented yet. This prototype
also does not promise equivalent performance to the native PySAT solver, which
has topology preprocessing and a different engine portfolio.

## Verification

```powershell
cd frontend
npm test
npm run build
```

Automated coverage includes parser limits, independent solution verification,
a repository 5×5 puzzle, UNSAT vs timeout, a disconnected-cycle regression,
agreement with independent exhaustive path enumeration across 24 small-board
cases, synthetic image detection, malformed image headers, invalid photo
corners, and report count/byte/expiry limits.

Browser checks during implementation recovered the canonical endpoint layout
of `reference_puzzle_images/IMG_3202.PNG`, solved and independently verified it,
and recovered the same endpoints from a synthesized tilted JPEG after manual
corner selection. This is evidence for the narrow prototype scope, not parity
with the complete screenshot/camera corpora.

The UI was also driven with Playwright in Chromium (Edge), WebKit and Firefox
at phone, small phone, landscape phone, tablet (both orientations), laptop
and desktop sizes. Every page was checked for horizontal overflow, touch-target
size and axe-core WCAG 2.2 AA rules. On `/local` the screenshot flow, a
keyboard-only solve, a zoomed 20 × 20 board on a phone, and 12 synthetic camera
photos (plain square levels from `reference_camera_corpus`, corners dragged to
their labeled positions) solved in all three engines. The reflection variants
each needed one on-board correction. These are desktop engine builds with
device emulation. Real iOS Safari, Android Chrome and phone-class CPUs still
need a hands-on check.

## Next migration steps

1. Replay supported screenshots and labeled real camera photos against Python
   baselines, comparing topology/endpoints rather than RGB values or letter names.
2. Port image quality checks and unsupported-variant detection, then topology,
   schema-v2 rules, and OCR. Keep the independent validators in the browser.
3. Benchmark medium/large boards and mobile devices. Consider a smaller compiled
   SAT engine while retaining the same graph and solution contracts.
4. Add resumable local batches and offline asset caching. Browser execution
   cannot promise continued work after a tab is closed.
5. Connect feedback only after choosing a collection destination and retention
   policy. Send small diagnostics first; enforce limits and deduplication on the
   service itself, then accept an explicit optional image upload. Recognition
   failures must remain reportable. Investigate expensive solves asynchronously.
6. Keep an expiring unreviewed inbox separate from deliberately promoted,
   reviewed regression examples. Promotion must not happen automatically.

This prototype implements the local report envelope and bounded inbox, not
remote acceptance, spam controls, server retention, or regression promotion.
