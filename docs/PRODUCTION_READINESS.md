# Production readiness — single-node roadmap

Status as of July 2026. The deployment target is one node (backend `python app.py`
on :8000, frontend static build). This document tracks what is already in place
and what remains, grouped by the four production axes.

## 1. Parallel processing (single node)

**In place**

- The screenshot batch pipeline in the frontend runs items through a bounded
  worker pool (`BATCH_CONCURRENCY = 3` in `ImageView`) instead of sequentially.
  Each item is an independent chain of backend calls, so wall-clock drops
  roughly by the pool size.
- FastAPI runs sync endpoints (`/solve`, puzzle CRUD) on a threadpool, and both
  solver backends escape the GIL: z3 via ctypes, and the preferred python-sat
  engine in a separate worker process (`pysat_solver._sat_worker`). Concurrent
  solves genuinely use multiple cores on one node.
- Image-import records are one directory per import with atomic
  tmp-file-then-rename writes. Read-modify-write paths (replace on reprocess,
  solve attach, failure append) serialize on per-import `threading.Lock`s.
- `scripts/validate_puzzles.py --jobs N` demonstrates the scaling: 652 puzzles
  solve + verify in ~79 s at 8 workers.

**Next**

- The per-import locks are in-process only. Before running `uvicorn --workers N`
  (multi-process), record updates need cross-process file locking (e.g.
  `msvcrt.locking`/`fcntl` on `record.json`) or a move to SQLite.
- Consider a small server-side job queue for batch imports so a phone can
  submit a batch, disconnect, and collect results later; today the browser tab
  orchestrates the batch and must stay open.
- Cap concurrent heavy image-pipeline requests server-side (semaphore) so a
  large batch cannot starve interactive solves.

## 2. Algorithmic efficiency

**In place**

- The "z3" solver transparently prefers an exact CNF encoding on python-sat's
  native engines (dramatically faster on sparse boards), falling back to Z3
  when python-sat is missing (`FLOW_DISABLE_PYSAT=1` forces the fallback).
- Baseline (this machine, `scripts/benchmark_solver.py`): 5x5 ≈ 42 ms,
  8x8 ≈ 16 ms, 10x10 ≈ 43 ms, 15x15/16-color ≈ 405 ms median. Interactive-grade
  through 15x15.
- Every solver result is independently re-verified
  (`flow_solver.solver.validation.validate_solution`) before it is returned.

**Next**

- Wire `scripts/benchmark_solver.py` into CI with stored medians and fail on
  >2x drift, so encoding changes can't silently regress solve times.
- Profile the 15x15 case: most time is incremental cut-set elimination; try
  stronger connectivity encodings (e.g. spanning-tree or reachability ladder)
  before adding cuts lazily.
- The DFS solver is experimental and orders of magnitude slower; either invest
  in pruning (dead-end detection, corridor forcing) or drop it from the UI to
  reduce a confusing choice.

## 3. Automated validation of historical puzzles

**In place**

- `scripts/validate_puzzles.py` re-solves every puzzle in the curated `puzzles/`
  library (and, with `--include-imports`, every generated puzzle in the
  screenshot archive), independently validates each solution, and compares
  stable outcomes (parses / solvable / solution-valid / node & color counts)
  against a golden baseline at `tests/golden/puzzle_baseline.json`.
  Exit codes: 0 ok, 2 regression vs baseline, 3 invalid solution.
- Current findings: the release corpus has been deduplicated and known
  historical misdetections and mechanic-only POCs have been removed. Recent
  screenshot imports are promoted only after a recorded successful solve and
  an independent validation pass.
- Curated corpus: 30 unique, solved, independently verified puzzles (23 square,
  6 irregular graph, 1 circle). The latest promotion contributed 14
  screenshot-backed levels: 6 bridge boards, 6 irregular-region boards, and 2
  warp boards. The cleanup removed 14 demonstration POCs, 4 semantic
  duplicates, and 3 unsolvable historical misdetections.
- Full archive sweep: 652 puzzles, 393 solved+verified, 0 invalid solutions.
  The 259 unsolvable archive entries are old pipeline misdetections that the
  archive intentionally retains for reprocessing.

**Next**

- Run `python scripts/validate_puzzles.py` in CI on every PR (library-only mode
  is sub-second) and the `--include-imports` sweep nightly.
- Regenerate the baseline (`--write-baseline`) only as a deliberate, reviewed
  action when puzzles are added or fixed.

## 4. Navigation and usability

**In place**

- Boards render like the actual game (grid cells, fat pipes, tinted path
  cells, solid walls) everywhere; node/edge graph views are advanced-mode only.
- Navigation is three destinations (Screenshot / Create / Library); the Batch
  tab was consolidated into the Screenshot page's batch mode, which now also
  warns about duplicate names/source images before saving.
- The Library hosts the Uploaded screenshots cache (search, status filter,
  pagination, save-to-library, bulk reprocess/delete). Reprocess hands off to
  the Screenshot page's pipeline.

**Next**

- Persist the in-progress puzzle (solve view) to `localStorage` so a mobile
  refresh doesn't lose state.
- Server-side pagination for `/image-imports` (the UI paginates client-side but
  still downloads all summaries).
- Game-style rendering for hex boards (square-lattice only today).
- Optional: replace the Plotly "Interactive" view (4.9 MB lazy chunk) with
  `plotly.js-basic-dist` or drop 3D if usage stays low.

## Deployment checklist (single node)

- [ ] Serve the frontend `dist/` behind the same origin as the API (or pin
      CORS to the real origin instead of `*`).
- [ ] Run the backend under a process supervisor with `RELOAD=0`.
- [ ] Back up `puzzles/` and `data/image_imports/` (both are plain directories).
- [ ] CI: `pytest tests`, `npm run build`, `python scripts/validate_puzzles.py`.
- [ ] Restart the backend after deploying to pick up the per-import locking.
