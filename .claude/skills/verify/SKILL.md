---
name: verify
description: Build, launch, and drive the Universal Flow Game Solver app (FastAPI backend + React frontend) to verify changes end-to-end.
---

# Verifying Universal Flow Game Solver changes

## Launch (no Docker needed)

Backend deps live in the system Python (fastapi, uvicorn, z3, PIL all importable).

```bash
# backend on :8000 (repo root)
python app.py

# frontend: build once, then serve the dist
cd frontend && npm run build        # tsc -b && vite build (~90s)
npx vite preview --host --port 4173
```

Gotchas:
- Port 4173 is often already taken by an unrelated app on this machine — vite
  falls back to 4174. **Read the preview output for the real port** before
  driving; a wrong port serves someone else's page (body renders just "☰").
- **Restart `vite preview` after every rebuild** — it snapshots dist at
  startup and keeps serving the old hashed bundle otherwise.
- The backend picks its puzzle parser from the file-name extension (.flow vs
  .json). A solve that fails with "All grid rows must have the same width in
  .flow" usually means JSON text was submitted under a .flow name.
- Batch-mode "Save all" writes real files into `puzzles/` — delete test
  artifacts afterwards (`find puzzles -mmin -30`).
- The frontend resolves the API as `http://<hostname>:8000` automatically; no
  VITE_API_URL needed for localhost.

## Drive (Playwright, no browser download)

`playwright-core` + installed Edge works headless: 

```js
const { chromium } = require("playwright-core");
const browser = await chromium.launch({ channel: "msedge", headless: true });
```

Install `playwright-core` into a scratch dir (`npm i playwright-core`), not the repo.

Flows worth driving:
- **Screenshot import**: `input[type=file]` ← a file from
  `reference_puzzle_images/` (IMG_3202.PNG / IMG_3203.PNG are square 5x5 and
  detect reliably). With auto-process on, the app crops, detects, opens the
  solver, and auto-solves; wait for text `/Solved in/`.
- **Builder**: Create tab; square-grid cells expose `data-cell="r-c"`. A
  solvable fill-all 5x5 layout: A at 0-0 & 3-0, B at 4-0 & 4-4. (A at four
  corners is unsolvable by parity — good for the error-path probe.)
- **Library**: cards have a `Solve` button (exact match — plain `getByText`
  on "Solve"/"Solution" also matches z3's `solution_blocks` stat text in Raw
  results; use `{ exact: true }`).

Mobile emulation gotcha: with `isMobile: true`, Playwright's `.tap()`
actionability check false-positives "element intercepts pointer events" on the
builder cells even when `elementFromPoint` returns the cell. Tap via
coordinates instead:

```js
const box = await loc.boundingBox();
await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
```

Solves are fast (tens of ms for 5x5–10x10); a 120s wait is generous. The
image pipeline (classify + OCR + grid + terminals) takes ~5–20s per image.
