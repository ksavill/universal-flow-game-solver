# Production readiness — single-node deployment

Status as of July 2026. The supported production target is one node running a
multi-worker FastAPI backend and a same-origin static frontend. The roadmap
items below are implemented; this document now records the operating contract
and the checks required for a release.

## 1. Parallel processing and durable work

- Screenshot processing is bounded in both places that can create load. The
  browser uses a three-item worker pool and the API admits at most
  `FLOW_IMAGE_PIPELINE_CONCURRENCY` heavy image requests (default `2`). Regular
  solve and archive requests remain responsive while a batch is active because
  admitted OpenCV/PIL work runs off the FastAPI event loop.
- `POST /image/jobs` persists a batch, its source images, options, per-file
  progress, and results under `data/image_jobs/`. A client may disconnect and
  later query `GET /image/jobs/{id}`. Queued or interrupted jobs are recovered
  on process startup, and failed jobs can be retried.
- Screenshot records still use atomic temporary-file replacement. Every
  read-modify-write and delete path additionally takes a stable advisory file
  lock under `.image_imports.locks`, so `uvicorn --workers N` processes cannot
  overwrite each other's updates. The lock implementation uses
  `msvcrt.locking` on Windows and `fcntl.flock` on POSIX.
- Archive filtering, ordering, and pagination use a persistent SQLite summary
  index. The per-import JSON remains authoritative and performs a one-time
  backfill if the index is absent.
- Development reload watches only `backend/` and `flow_solver/`. Docker Desktop
  polling remains enabled for reliable hot reload, but it no longer traverses
  the archive, frontend dependencies, or virtual environment; both development
  watchers use a two-second polling interval to avoid unnecessary wakeups.
- Solver parallelism is real: Z3 runs outside the Python GIL and the preferred
  python-sat engine runs in a worker process. Archive validation supports
  `--jobs N` for bounded parallel re-solving.

The API reports the configured image limit and available accelerators through
`GET /capabilities`. Keep `API_WORKERS` and the image concurrency limit small
on a memory-constrained host; extra workers do not bypass the file locks.

## 2. Algorithmic efficiency

- The exact solver prefers a compact CNF encoding on python-sat and falls back
  to Z3 when python-sat is unavailable (`FLOW_DISABLE_PYSAT=1` forces the
  fallback). Every returned result is independently validated.
- Deterministic topology preprocessing is cached by SHA-256 topology hash.
  Repeated boards reuse incident maps, articulation components, and bridge
  separators even when their terminal placements differ.
- Bridge and articulation/separator-capacity reasoning prunes impossible color
  assignments before model construction. Lazy connectivity cuts remain a
  correctness backstop, but the representative corpus now needs zero such cuts.
- The experimental DFS choice was removed from the UI. The supported UI path
  is the exact solver; the DFS implementation remains available only for
  development and rejects schema rule extensions it cannot enforce.
- `scripts/benchmark_solver.py` compares status, median time, and model size to
  `tests/golden/benchmark_baseline.json`. CI fails on a greater than 2× time or
  model-size drift.

Representative current medians on the development machine are approximately
7.5 ms (5×5), 54.7 ms (8×8), 129.8 ms (10×10), and 866.2 ms (15×15). On the
15×15 sample, SAT checking is now the dominant measured stage; preprocessing
is about 23 ms and connectivity cuts are zero. Machine timings are indicative,
so release decisions use the stored ratio thresholds rather than these numbers.

## 3. Automated historical validation

- `scripts/validate_puzzles.py` re-solves the curated library and, with
  `--include-imports`, every saved generated puzzle in the screenshot archive.
  It independently validates solutions and compares stable outcomes against
  `tests/golden/puzzle_baseline.json`.
- Pull requests run backend tests, curated validation, benchmark comparison,
  and the production frontend build in `.github/workflows/ci.yml`. The scheduled
  run also executes the archive sweep when archived data is available to the
  runner.
- Golden results cannot be overwritten accidentally. A reviewed update requires
  both flags:

  ```bash
  python scripts/validate_puzzles.py --write-baseline --accept-baseline-update
  ```

- `scripts/replay_image_imports.py --latest N` replays the actual retained
  source images through the current importer and solver in isolated temporary
  archives. Use `--failures-only` to investigate new failures and `--output`
  to retain a machine-readable report.
- `reference_screenshot_corpus/` retains a local set of up to 500 byte-unique
  screenshots. Refresh it with `scripts/build_reference_corpus.py --limit 500`
  and use `scripts/replay_reference_corpus.py` to compare the full image
  pipeline with reviewed stable-result baselines. It is backed up with mutable
  data but intentionally not part of CI.
- `reference_camera_corpus/` is deliberately separate. Build deterministic
  off-angle JPEG variants with `scripts/build_camera_photo_corpus.py` and run
  them through the real API with `scripts/replay_camera_photo_corpus.py`.
  Reports compare topology, grid, modifiers, and endpoint pairs while ignoring
  harmless RGB sampling drift and pair-label ordering. Add labeled real photos
  by device/capture session before expanding the supported quality envelope.

The scene renderer (2026-09-24, `scene-v1`: 9 mock devices, 9 environments,
modeled display/glass light and camera) is considerably harsher than the fixed
variants. On 123 photos of 41 square-target screenshots with labeled screen
corners: easy 37/50, medium 14/50, hard 0/23 (all flagged); 46/77 inside the
supported envelope, with 3 wrong results left unflagged (a missed terminal
pair without an advertised flow count, and swapped color pairs under a mild
white-balance shift). With automatic corners: 25/123 overall, 23/77 inside
the envelope, 1 unflagged. Graph-mode boards with walls or warps from photos
matched 11/73 in the envelope with 16 unflagged errors, almost all from lost
warp adjacencies; hex boards from photos matched 0/30 (region-graph detection
finds no enclosed cells). A half-cell and a quarter-cell board-crop margin
were tried for warps and rejected: they fixed 5 and 1 of 18 warp failures
respectively while regressing the large-board set from 12/15 to 0/15 and 9/15.

A follow-up pass on 2026-09-23 added pipeline-evidence `auto` selection,
weak-outline candidate scoring, single uploads, HEIC decoding, real-photo
harvest/calibration tooling, and a 60-photo realistic set (sensor noise,
rotation, moire). With labeled corners the realistic set matched 58/60 (all
three new effects 10/10); with automatic corners 47/60. `auto` misrouted 0 of
219 screenshots and recognized 37 of 75 EXIF-less photos. All camera runs had
zero unflagged wrong results. The screenshot replay again matched on 218 of
219; the remaining graph board (`7679d40f3e188218`) exceeded the 60-second
region-seam inference deadline because the machine was at 100% CPU, and
committed `HEAD` produced the identical result under the same load. Large
graph boards therefore depend on host load, which is a pre-existing issue.

Camera-photo verification on 2026-09-23 (`camera-v3`) added a 15-photo set of
10+ flow levels (white and gray terminals) and an automatic-corner replay. With
labeled corners the main set matched 28/30 (mild 10/10, heavy blur/banding
10/10, glare 8/10) and the large-flow set 12/15 (previously 0/15, with 7 wrong
results left unflagged). With automatically detected corners the sets matched
22/30 and 10/15. Every mismatch in all four runs was routed to review, so zero
incorrect results were labeled high-confidence. Camera preparation takes a
median 0.43 s and at most 1.6 s per photo (previously up to 6.2 s); repeated
calls for the same upload return from cache in about 10 ms. The 219 retained
screenshots again produced identical puzzle hashes and solve outcomes before
and after the change.

Camera-photo verification on 2026-08-03 used 10 reviewed square sources with
three deterministic variants each. Mild perspective/blur/JPEG capture matched
10/10 source puzzle structures. The complete blur/glare/banding stress set
matched 25/30; all five mismatches required review, so zero incorrect results
were labeled high-confidence. The unchanged screenshot path produced identical
semantic signatures on all 219 locally retained screenshots before and after
the camera work.

Release verification on 2026-07-14 completed the following gates:

- backend: 128 tests and 10 subtests passed;
- curated baseline: 30/30 puzzles solved and independently verified;
- full saved-puzzle sweep: 741 checked, 458 solved+verified, 283 retained
  historical unsolvable detections, 0 parse failures, and 0 invalid solutions;
- latest-source replay: 500 uploads checked, 498 regenerated, 335 solved, and
  all 17 previously solved uploads remained solved (0 regressions); and
- the three newly recorded failures were confirmed as two identical all-white
  PNGs and one terminal-free empty grid, all correctly rejected.

## 4. Navigation and usability

- The in-progress solve document, name, and selected source tab are restored
  from `localStorage` after a mobile refresh.
- `GET /image-imports` implements server-side status/search filtering,
  ordering, limit, and offset pagination. The Screenshot Library requests only
  its current page and displays the server total.
- Odd-row-offset hex topology is rendered as game-style hexagonal cells with
  tinted paths, pipes, and terminals. Square and irregular graph rendering are
  unchanged.
- The unused Plotly interactive/3D view and its roughly 4.9 MB dependency were
  removed. Advanced graph inspection remains available without that download.
- Durable background batch submission is available from the Screenshot page.
  The last job id is stored locally so reopening the page resumes polling and
  restores completed results.

## Deployment checklist

The production Compose definition satisfies the checklist directly:

```bash
# PUBLIC_ORIGIN must be the public scheme + host, with no trailing slash.
PUBLIC_ORIGIN=https://flow.example.com docker compose -f docker-compose.prod.yml up -d --build
```

- [x] Nginx serves the frontend and proxies `/api/` to FastAPI on the same
      origin. `CORS_ORIGINS` is pinned to `PUBLIC_ORIGIN` rather than `*`.
- [x] Both containers use `restart: unless-stopped`; FastAPI runs with
      `RELOAD=0`, a health check, and `API_WORKERS` (default `2`).
- [x] `puzzles/` and `data/` are mounted outside the containers.
- [x] `scripts/backup_data.py` creates an atomic timestamped ZIP containing
      `puzzles/`, `data/image_imports/`, `data/image_jobs/`, and
      `reference_screenshot_corpus/`, plus a SHA-256 manifest, and applies
      retention. Schedule it with the host task runner:

      ```bash
      python scripts/backup_data.py --output-dir /srv/flow-backups --retain 14
      ```

- [x] CI runs `pytest`, historical validation, performance comparison, and
      `npm run build`; the nightly trigger adds the import sweep.
- [x] Deployments recreate/restart the backend so new code, startup recovery,
      and locking behavior take effect.

Before exposing a host, set `PUBLIC_ORIGIN`, choose backup storage outside the
repository/data volume, perform one restore drill, and confirm `/api/health`
through the public Nginx endpoint.
