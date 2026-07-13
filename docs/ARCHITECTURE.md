# Universal Flow Game Solver — architecture

This is the system-level map of the project: how a puzzle gets in (typed,
built, or photographed), how the universal model represents every Flow
variant on one graph abstraction, how the exact solver works, and where
everything is stored. For the deep dive on variant mechanics and the schema-v2
format, see [FLOW_VARIANTS_AND_ARCHITECTURE.md](FLOW_VARIANTS_AND_ARCHITECTURE.md);
for deployment posture, see [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

## Bird's-eye view

```mermaid
flowchart LR
    subgraph Frontend["React frontend (Vite + MUI)"]
        SV[Solve view]
        NV[Builder]
        IV[Screenshot importer<br/>single + batch]
        LV[Library<br/>puzzles + uploaded screenshots]
        GV[GameView renderer]
    end

    subgraph Backend["FastAPI backend (:8000)"]
        API["/parse /graph /solve /validate"]
        IMG["/image/* detection pipeline"]
        LIB["/puzzles CRUD"]
        ARC["/image-imports archive"]
    end

    subgraph Core["flow_solver (pure Python)"]
        P[Puzzle / Graph model]
        S2[schema_v2 parser + compiler]
        SOL[Exact edge solver<br/>python-sat preferred, Z3 fallback]
        VAL[Structural + solution validators]
        TOP[Topology registry<br/>cube, star, figure8, grid, hex, ring]
    end

    subgraph Storage["Disk (single node)"]
        PUZ["puzzles/ curated library"]
        IMP["data/image_imports/&lt;id&gt;/"]
        GOLD["tests/golden/puzzle_baseline.json"]
    end

    SV & NV & IV & LV --> API
    IV --> IMG
    LV --> ARC
    API --> P
    IMG --> TOP
    S2 --> P
    P --> VAL --> SOL --> VAL
    LIB --> PUZ
    ARC --> IMP
    GV -. "renders graph + path_edges" .-> SV & LV & IV
```

One node runs everything. Solves and image detection are CPU-bound but escape
the GIL (Z3 via ctypes, python-sat in a worker process, PIL/NumPy internally),
so FastAPI's threadpool gives real multi-core parallelism without extra
processes.

## The universal puzzle model

A level is **not** a rectangular matrix. It is a set of *physical cells*, one
or more *routing channels* per cell, named *ports* on each channel, and typed
*adjacencies* between ports. Every product variant is an instance of that one
model:

```mermaid
flowchart TB
    subgraph Cell_A["cell a (ordinary)"]
        A1["channel a:main<br/>ports: E"]
    end
    subgraph Cell_B["cell b (bridge)"]
        BH["channel b:h<br/>ports: W, E"]
        BV["channel b:v<br/>ports: N, S"]
    end
    subgraph Cell_C["cell c (ordinary)"]
        C1["channel c:main<br/>ports: W, warp"]
    end
    subgraph Cell_D["cell d (ordinary)"]
        D1["channel d:main<br/>ports: warp"]
    end

    A1 -- "local (open)" --- BH
    BH -- "local (blocked) = wall" --- C1
    C1 -- "warp (open, group w1)" --- D1
```

| Variant | What changes | In the model |
| --- | --- | --- |
| Classic / Jumbo | board size only | 4-neighbor `local` adjacencies |
| Hexes | 6 neighbors | more `local` adjacencies per cell |
| Bridges | two pipes cross one cell | one cell, two channels (`:h`, `:v`) |
| Walls | a border can't be crossed | adjacency `state: "blocked"` |
| Warps | pipe exits and re-enters elsewhere | `warp` adjacency (never inferred from distance) |
| Wrap / boundless | opposite edges join | `seam` adjacency |
| Shapes (cube, star, figure-8, circle) | non-planar board | topology template or explicit region graph |

Three input formats compile into the same runtime `Puzzle`/`Graph`:

```mermaid
flowchart LR
    FLOW[".flow text<br/>(square/hex/circle grids)"] --> LP[legacy parser]
    LJSON["legacy graph JSON<br/>(nodes, edges, walls, warps)"] --> LP
    V2["schema-v2 JSON<br/>format: flow-solver-puzzle"] --> SP[strict v2 parser] --> CC[compiler]
    LP --> PZ[Puzzle / Graph]
    CC --> PZ
    LP -. "migration.migrate_file()" .-> V2
```

Schema v2 stays attached as `Puzzle.source_spec`, so the API and renderer keep
typed adjacencies, display geometry, and catalog provenance that the compact
solver graph doesn't need.

## Solver pipeline

The public "z3" solver is an exact Boolean edge model. When the optional
`python-sat` runtime is installed (it is, by default), the identical model is
compiled to CNF and solved by a native SAT engine in a separate worker
process; Z3 remains the fallback (`FLOW_DISABLE_PYSAT=1` forces it).

```mermaid
flowchart TB
    IN[Puzzle] --> SV["Structural validation<br/>terminal pairs, connectivity,<br/>bipartite endpoint balance, ..."]
    SV -->|invalid| ERR[PuzzleValidationError<br/>stable error codes]
    SV --> PRE["Preprocessing<br/>per-color reachability pruning<br/>(foreign terminals removed)"]
    PRE --> ENC["Edge model<br/>x(v,c): channel v has color c<br/>y(e,c): edge e selected by color c<br/>degree 1 at terminals, 2 inside,<br/>bridge channel exclusivity,<br/>full coverage per physical cell"]
    ENC --> ENGINE{python-sat<br/>available?}
    ENGINE -->|yes| SAT["CNF + native SAT engine<br/>(worker process)"]
    ENGINE -->|no| Z3["Z3 QF_FD"]
    SAT --> CHECK
    Z3 --> CHECK
    CHECK["candidate model"] --> CYC{disconnected<br/>cycles?}
    CYC -->|yes| CUT["add exact cut-set constraint<br/>for each stray component"] --> CHECK
    CYC -->|no| EXT["extract ordered paths<br/>+ explicit path_edges"]
    EXT --> IV["Independent solution validator<br/>(solver output is untrusted)"]
    IV --> OUT["SolveResult<br/>paths, path_edges, stats, unique"]
    OUT -.optional.-> UNIQ["uniqueness: block exact assignment,<br/>search for a second connected model"]
```

Key properties:

- **One deadline** covers validation, preprocessing, model build, every
  incremental check, extraction, and uniqueness.
- Degree constraints alone admit closed loops, so connectivity is enforced
  **lazily**: each candidate is component-checked and stray cycles are removed
  with exact cut-set clauses. This keeps the initial model far smaller than an
  all-pairs reachability encoding.
- Every returned solution is re-verified by an independent validator before it
  leaves the core — encoding or extraction bugs surface as errors, not wrong
  answers.
- Renderers must draw `path_edges`, never "all edges whose endpoints share a
  color".

Current single-node baseline (`scripts/benchmark_solver.py`): 5x5 ≈ 42 ms,
8x8 ≈ 16 ms, 10x10 ≈ 43 ms, 15x15 with 16 colors ≈ 405 ms median.

## Screenshot detection pipeline

The importer turns a phone screenshot into a solvable document. Every upload
is archived first, so any screenshot can be reprocessed through a newer
pipeline later.

```mermaid
flowchart TB
    UP[Uploaded image] --> AC["Auto-crop<br/>edge- and color-based (OpenCV),<br/>optional saved crop template"]
    AC --> PSP["Perspective correction<br/>(optional, quad detection)"]
    PSP --> CLS["Level-type classifier<br/>geometry + modifier candidates<br/>with confidences"]
    PSP --> OCR["OCR title strip<br/>suggested name, size hint"]
    CLS --> BR{geometry}

    BR -->|square / hex| GRID["Grid detection<br/>Hough lines to lattice inference,<br/>thick-line merging, spacing vote"]
    BR -->|circle| CIRC["Circle grid detection<br/>ring + sector clustering"]
    BR -->|cube / star / figure8| TPL["Topology template<br/>(flow_solver.topologies)"]
    BR -->|free-form| REG["Region topology detection<br/>(see next section)"]

    GRID --> TERM["Terminal detection<br/>saturation/brightness filter,<br/>per-cell sampling, color clustering"]
    CIRC --> TERM
    TPL --> NTERM["Terminal detection on node positions<br/>(project nodes to pixels)"]
    REG --> NTERM

    TERM --> MODS["Modifier detectors<br/>bridges (cell glyphs),<br/>walls (thick shared borders),<br/>warps (paired border breaks)"]
    NTERM --> MODS
    MODS --> EMIT{output format}
    EMIT -->|plain square/hex/circle| FLOWT[".flow text"]
    EMIT -->|anything typed| V2J["schema-v2 JSON<br/>blocked/warp adjacencies,<br/>display geometry, catalog"]
    FLOWT --> ARCH["Archive: data/image_imports/&lt;id&gt;/<br/>original bytes + record.json"]
    V2J --> ARCH
    ARCH --> SOLVE["Auto-solve (z3, 30 s)<br/>result attached to the archive record"]
    SOLVE -->|unsat with auto target| RECOVER["Retry as region graph<br/>(regions layout)"]
```

Design rules the pipeline follows:

- **Evidence over inference.** A warp adjacency requires both members of the
  paired border-break glyph; distance, glow, or a pack name is never enough.
- **Detection settings are reproducible.** Crop templates store the crop
  rectangle *and* the pipeline toggles used, so a device's screenshots import
  the same way every time.
- **Failures are kept.** A failed import archives the original image plus the
  error and stage; the Library's *Uploaded screenshots* page can bulk-select
  and reprocess them after the pipeline improves. Each reprocess appends a run
  summary to the record (`runs[]`), preserving history.

## Free-form (region) detection

Shaped boards — the Shapes app's silhouettes, rings with holes, linked loops —
have no lattice to detect. The region path builds the graph directly from the
picture:

```mermaid
flowchart LR
    IMGF[Cropped board image] --> BARR["Barrier mask<br/>Otsu bright lines + chroma mask<br/>for dim colored grid lines"]
    BARR --> BLOB["Remove endpoint dots<br/>(compact round blobs are terminals,<br/>not barriers)"]
    BLOB --> SEG["Enclosed dark regions<br/>= physical cells<br/>(area-filtered components)"]
    SEG --> CENT["Region centroids become nodes<br/>(screenshot-pixel coordinates)"]
    SEG --> ADJ["Sufficiently long shared barrier<br/>between two regions = edge"]
    CENT --> GJSON["graph JSON<br/>nodes + edges + positions"]
    ADJ --> GJSON
    GJSON --> TERMN["terminals sampled at node positions"]
    TERMN --> OUTJ["legacy graph JSON or schema v2"]
```

Two properties matter downstream:

- Node coordinates are **screenshot pixels**, not grid units. `GameView`
  detects this (no integer lattice) and falls back to node/edge rendering with
  a capped scale, while true grids get the game-style board.
- Known solids don't go through region detection at all:
  `flow_solver.topologies` generates cube/star/figure-8 boards from `(width,
  height)` parameters with stable ids, exact edges, and display positions, so
  those imports are deterministic.

## Frontend rendering

`GameView` is the single board renderer used by the solve view, library
thumbnails, batch results, and the archive's inline solutions:

```mermaid
flowchart TB
    G[graph payload] --> LAT{"all nodes on integer lattice<br/>AND all non-warp edges are<br/>unit axis-aligned steps?"}
    LAT -->|yes| GAME["Game-style board<br/>cell squares, fat rounded pipes,<br/>tinted covered cells, big terminals,<br/>solid full-length walls,<br/>warp stubs at the border,<br/>bridge cross glyphs"]
    LAT -->|no| FALL["Node/edge fallback<br/>hex, circle, free-form regions"]
    GAME & FALL --> SOLQ{solution present?}
    SOLQ -->|yes| OVER["overlay path_edges + node_color"]
```

Graph-theoretic views (static graph, Plotly 2D/3D) remain available as
advanced modes in the solve view.

## Storage and regression safety

Everything persists as plain files under the repository root — trivially
backed up, no database:

```text
puzzles/                       curated library (.flow / .json), organized kind/size
puzzles/templates/crop/        saved crop templates (+ preview.png)
data/image_imports/<id>/       source.<ext>  original screenshot bytes (immutable)
                               record.json   detection result, solve result, runs[]
tests/golden/puzzle_baseline.json   golden outcomes for every library puzzle
```

`scripts/validate_puzzles.py` re-solves every library puzzle (and with
`--include-imports` every archived generation), re-verifies each solution
independently, and diffs stable outcomes against the golden baseline —
regressions exit nonzero. `scripts/benchmark_solver.py` tracks solve-time
drift. Together they are the release gate:

```mermaid
flowchart LR
    CHANGE[solver / parser / pipeline change] --> T["pytest tests/ (116 tests)"]
    T --> VP["validate_puzzles.py<br/>baseline diff, exit 2 on regression"]
    VP --> BM["benchmark_solver.py<br/>median / p95 solve times"]
    BM --> SHIP[ship]
```

## Concurrency model (single node)

| Layer | Mechanism |
| --- | --- |
| Frontend batch import | bounded pool, 3 screenshots in flight |
| FastAPI sync endpoints | threadpool workers |
| SAT solving | separate worker process (python-sat) or GIL-released ctypes (Z3) |
| Image ops | OpenCV/PIL/NumPy release the GIL internally |
| Archive record updates | atomic tmp-file rename + per-import `threading.Lock` |

The per-import locks are in-process; moving to multiple uvicorn workers
requires cross-process file locking first (tracked in
[PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)).
