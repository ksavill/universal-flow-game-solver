# Flow variants and solver architecture

Research snapshot: 2026-07-11

This document turns the Flow Free product family into a small set of solver
mechanics.  That distinction matters: pack names such as *Mania*, *Extreme*,
or *Jumbo* describe catalog, size, or difficulty, while *Bridges*, *Hexes*,
*Warps*, and shaped surfaces change the graph that must be solved.

The architectural conclusion is that a level is not fundamentally a bitmap or
a rectangular matrix.  It is a set of physical cells, one or more routing
channels in each cell, and typed adjacencies between channel ports.  Square,
hex, circular, warped, bridge, cube, star, and linked-track boards are all
instances of that model.

## Product and mode survey

Big Duck Games' current developer-maintained store listings identify the full
mechanical product family below.  Counts and pack inventories change with app
updates, so they are catalog metadata rather than format or solver constants.

| Product | Distinguishing level structure | Canonical solver representation |
| --- | --- | --- |
| [Flow Free](https://apps.apple.com/us/app/flow-free/id526641427) | Primarily orthogonal square/rectangular boards. The current app also advertises Classic, Bonus, Bridges, Mania, Extreme, and Jumbo packs. | Four-neighbor cells, with masks or bridge cells when a particular pack requires them. |
| [Flow Free+](https://apps.apple.com/us/app/flow-free/id6746876060) | Apple Arcade catalog edition of Flow Free. Its listing changes content volume and distribution, not the advertised connection rules. | Same mechanics as the represented level; record `app`/`variant` separately. |
| [Flow Free: Bridges](https://play.google.com/store/apps/details?id=com.bigduckgames.flowbridges) | A bridge permits two pipes to cross without joining. | One physical cell containing independent horizontal and vertical channels. |
| [Flow Free: Hexes](https://play.google.com/store/apps/details?id=com.bigduckgames.flowhexes) | Hexagonal cells instead of squares. | A cell graph with up to six local neighbors. |
| [Flow Free: Warps](https://play.google.com/store/apps/details?id=com.bigduckgames.flowwarps) | Paths can leave one location and re-enter elsewhere; current packs also use wrap/boundless and shaped presentations. | Explicit nonlocal `warp` edges and boundary `seam` edges. Never infer these from screen distance. |
| [Flow Free: Shapes](https://apps.apple.com/us/app/flow-free-shapes/id6642642713) | The board is a shaped cell complex rather than one rectangular face. Current repository fixtures include cubes, radial stars, circles, and linked-loop/figure-eight tracks. | A topology template when known, otherwise an explicit region-adjacency graph. |

All five mechanical apps advertise the same base objective: connect each color
pair, do not let ordinary pipes cross or overlap, and cover the board.  Free
Play, Daily Puzzles, Time Trial, hints, move scoring, pack completion, and
color-blind labels change selection, presentation, or scoring; they do not
change path feasibility.  They belong in `catalog` or application state, not
in the constraint model.

The official listings do not publish a machine-readable definition of every
pack.  Pack names must therefore not be used to guess mechanics.  Import the
actual board structure, retain the pack name as provenance, and flag uncertain
topology for review.

Official Warps screenshots show selective ports as aligned breaks in opposing
board borders, normally with short dotted guides continuing outside the grid.
The importer requires both members of that visual pair before creating a
`warp` adjacency.  A distant cell, glow, perspective effect, or pack name alone
is not evidence of a nonlocal edge.  `Boundless` provenance may describe a full
opposite-edge wrap, but explicit detected or authored adjacencies remain the
canonical representation.

## Complete mechanic taxonomy

The product variants reduce to the following independent dimensions.

| Dimension | Level types | Representation |
| --- | --- | --- |
| Cell geometry | square, rectangle, hex, ring sector, irregular region | Cell display geometry plus explicit channel graph. Geometry never implies an edge after import. |
| Board topology | plane, annulus/circle, multi-face cube/star, linked loops, arbitrary silhouette | Named topology template or explicit cells and adjacencies. |
| Local adjacency | four-neighbor, six-neighbor, radial/angular, irregular shared boundary | Open `local` adjacency between named ports. |
| Boundary adjacency | wrap-around, face join, folded surface | `seam` adjacency, optionally grouped by seam identity. |
| Nonlocal adjacency | portal/warp, boundless re-entry | `warp` adjacency. A visual curve is display metadata only. |
| Removed capacity | hole, cutout, wall | Omit the cell/channel, or retain a typed adjacency with `state: blocked`. |
| Added capacity | bridge/crossover | Multiple independent channels owned by one physical cell. |
| Coverage | normal Flow full cover; general Numberlink partial cover | `rules.coverage.mode` is `all-cells` or `optional`. |
| Terminals | one pair per color in current Flow rules | Exactly two channel ids per terminal label. |
| Catalog/mode | Classic, Bonus, Mania, Extreme, Jumbo, Daily, Time Trial, pack and level ids | `catalog`; it never creates graph edges. |

Two distinctions prevent common modeling bugs:

1. A **cell** is a physical board location and a **channel** is a path-capacity
   unit.  They are one-to-one on an ordinary board but one-to-many at a bridge.
2. An **adjacency** says movement is possible and a **selected path edge** says
   a solution actually uses it.  Adjacent nodes with the same color need not
   use their connecting edge; the edge can be a chord beside a winding path.

## Runtime architecture

```text
schema-v2 JSON -> strict PuzzleSpec parser -> compiler --+
legacy .flow/JSON -> legacy parser ----------------------+-> Puzzle/Graph
image -> known template or region adjacency -> JSON -----+       |
                                                                validate
                                                                   |
                                                      exact edge-variable solver
                                                                   |
                                                      validate selected solution
                                                                   |
                                                ordered paths + explicit path_edges
```

The compiled `Puzzle` and `Graph` stay deliberately small so both Z3 and DFS
can consume them.  Schema v2 remains attached as `Puzzle.source_spec`, allowing
the API and renderer to preserve typed adjacencies, display geometry, and
catalog provenance that the compact runtime graph does not need.

### Canonical schema v2

A canonical document is marked with:

```json
{
  "format": "flow-solver-puzzle",
  "schema_version": 2
}
```

Its major sections have distinct responsibilities:

| Section | Responsibility |
| --- | --- |
| `topology.cells` | Physical cells and kinds such as ordinary or bridge. |
| `topology.channels` | Routable capacity, owning cell, kind, and named ports. |
| `topology.adjacencies` | Stable id; two channel/port endpoints; `local`, `seam`, `warp`, or `custom`; `open` or `blocked`; optional group. |
| `topology.template` | Reproducible generator id and parameters, when applicable. Explicit expanded topology remains authoritative. |
| `terminals` | Exactly two endpoint channels per color, plus optional display color. |
| `rules` | Coverage, path degree/connectivity contract, and multi-channel policy. |
| `display` | 2D/3D cell, channel, port, and edge geometry. It cannot alter feasibility. |
| `catalog` | App, variant, pack, level, mode, displayed size, and mechanics tags. |
| `meta` / `extensions` | Provenance and namespaced future data. |

The parser is intentionally strict.  It rejects unknown keys in core objects,
unknown references, reused connected ports, parallel enabled adjacencies,
invalid rules, and non-finite/non-JSON data.  If either v2 marker is present,
an unsupported version is an error rather than a silent legacy fallback.
Serialization sorts ids and produces deterministic JSON.

The schema can describe some rules ahead of runtime support.  The current
compiler supports full or optional coverage and the standard endpoint-degree
1/internal-degree 2 connected-path contract.  It deliberately rejects
per-cell coverage overrides and a non-`distinct` multi-channel color policy
until their solver semantics are implemented.

### Exact edge-variable solver

The Z3 backend uses a pure Boolean finite-domain model:

- `x[v,c]`: channel `v` is occupied by color `c`.
- `y[e,c]`: adjacency `e` is selected by color `c`.
- Each channel has at most one color.
- Selecting `y[e,c]` selects both endpoint channels for `c`.
- A selected terminal channel has degree one; another selected channel has
  degree two.
- A color cannot traverse another color's terminal.
- At most one channel in a multi-channel cell may be used by the same color,
  while independent colors may cross through a bridge.
- Full coverage requires at least one used channel per physical cell, not all
  internal channels of a bridge.

Local degree constraints can initially admit disconnected cycles.  The solver
checks each candidate's selected-edge components and incrementally adds exact
cut-set constraints until every color is one terminal-to-terminal path.  This
keeps the initial model smaller than an all-pairs reachability encoding.

Before model construction, exact domain pruning computes each color's
reachability with foreign terminals removed, then repeatedly removes
nonterminal assignments that cannot have two compatible neighbors.  A single
deadline covers import, structural validation, preprocessing, model building,
all Z3 checks, extraction, result validation, and optional uniqueness.

Uniqueness is defined over the complete edge-colored assignment.  After the
first solution, the session blocks that exact assignment and searches for a
second connected one.  The result therefore exposes:

- `paths`: ordered channels from terminal to terminal;
- `path_edges`: exact selected adjacencies for safe rendering;
- `stats`: stage times, variable counts, checks, and connectivity cuts;
- `unique`: `true`, `false`, or `null` when not checked.

Consumers must render `path_edges` (or consecutive pairs from ordered paths),
never every graph edge whose endpoints happen to have the same color.

### Validation layers

Structural validation runs before constraint construction and reports stable
codes rather than a generic solve failure.  It checks, among other invariants:

- nonempty graph and terminal set;
- cell/channel ownership and unknown references;
- exactly two distinct, non-shared endpoints per color;
- isolated terminals and insufficient degree on required cells;
- connected components and terminal pairs split across components;
- required full-cover components with no terminal;
- the necessary endpoint balance for full-cover bipartite, single-channel
  components.

These are inexpensive necessary checks, not a proof of solvability.  A second,
independent validator checks returned paths, selected edges, degrees, terminal
ownership, disjointness, physical-cell coverage, and consistency between all
result views.  Treating solver output as untrusted catches encoding and
extraction regressions early.

### Topology registry

`flow_solver.topologies` is backend-independent.  Each builder returns stable
cell ids, display positions, exact undirected edges, family, parameters, and an
explicit reason for any intentional degree above four.  Every generated
topology validates itself for unknown endpoints, loops, duplicate edges,
connectivity, and unexplained high degree.

Current reference contracts are:

| Template | Meaning | Verified reference size |
| --- | --- | --- |
| `grid` | Orthogonal rectangular board | 3x3 = 9 cells / 12 edges |
| `hex_grid` | Odd-row-offset six-neighbor board | 3x3 = 9 cells / 16 edges |
| `ring` | Angular wrap plus radial neighbors, optional core | Parameterized rings and sectors |
| `cube` | Three square faces joined around the visible corner | size 2 = 12 cells / 18 edges |
| `radial_star` | Cyclic fan of five or six rhombus/square faces | 5x size 2 = 20/30; 6x size 3 = 54/90 |
| `figure8` | Faithful linked-loop reference track | 31 cells / 43 edges |

The legacy space builders additionally support square, hex, circle, holes, and
bridges.  Moving hex and any newly verified Shapes families into the registry
will give all import paths one source of truth.  A silhouette is not enough to
invent a generic lattice: a new knot, animal, or multi-face layout should be a
new verified template or an explicit region graph.

### Legacy migration

Use the migration command to preserve old puzzles in canonical form:

```bash
python -m flow_solver migrate old.flow --out migrated.json
python -m flow_solver validate migrated.json --solve --json
```

Migration preserves the compiled graph, physical tile grouping, terminals,
positions, fill rule, and available metadata.  Because legacy formats do not
name ports, it deterministically synthesizes one port per edge endpoint.  It
infers a conservative template id and treats unknown legacy edges as `local`;
callers can supply typed edge kinds when provenance identifies seams or warps.

Migration recovers explicit legacy warps, custom additions, and wall/removal
annotations, and identifies wrap seams in circular boards. It cannot recover
author intent absent from the source. In particular, screen distance is not
proof of a warp, a wall omitted from a legacy graph cannot be reconstructed,
and a pack name is not a topology specification. Validate and visually review
imported levels.

### Image-to-region graph extraction

Known layouts should use the registry.  For arbitrary Shapes screenshots, the
region extractor provides a topology fallback:

1. Bright or sufficiently chromatic lines become barriers.
2. Morphological closing and a small dilation repair antialiased gaps.
3. Four-connected dark components not touching the image border become cell
   candidates, subject to relative-area safety limits.
4. Each retained region produces a centroid and simplified contour polygon.
5. Nearby regions are dilated; a shared-contact threshold creates an edge
   while rejecting tiny corner-only contacts.
6. The importer reports barrier fraction, region/edge counts, adjacency gap,
   maximum degree, and warnings for empty or suspicious results.

This should remain reviewable, not silently authoritative.  Perspective,
cropping, glow, already-drawn pipes, open cell boundaries, and decorative text
can merge or split components.  The structural validator catches many bad
graphs, but it cannot prove that an image-derived adjacency matches the game.

## Efficiency measurement

Run the checked-in benchmark from the repository root:

```bash
# Representative 5x5, 8x8, 10x10, and 15x15 stored levels
python scripts/benchmark_solver.py

# Stable repeated sample and machine-readable output
python scripts/benchmark_solver.py puzzles/square/10x10 \
  --warmup 1 --repeat 5 --timeout-ms 30000 \
  --json-out out/benchmark.json

# Include the exact second-solution search
python scripts/benchmark_solver.py path/to/puzzle.json --unique
```

The harness parses each puzzle once outside the measured interval.  Every
measured solve still includes the solver's own validation and full deadline.
It reports wall-clock min/median/p95/max, internal solver time, node/color and
edge/color Boolean counts, Z3 checks, and lazy connectivity cuts.  Generated
JSON should normally stay out of version control because timings are specific
to the CPU, Python, Z3 build, and background load.

For useful performance comparisons:

- pin the same Python and Z3 versions;
- use at least one warm-up and five repeats for small boards;
- compare median and variable/cut counts, not a single fastest time;
- retain topology, color count, and coverage because board dimensions alone
  do not predict difficulty;
- use the same end-to-end timeout, including uniqueness when that is the
  feature being compared.

## Capability roadmap

The graph-first model now covers every current product family's fundamental
mechanics.  The next additions should extend explicit semantics rather than add
special cases based on app or pack names:

1. Add verified registry templates for additional Shapes/Warps boards, with
   edge/count fixtures taken from actual levels.
2. Carry importer confidence and manual edge corrections into schema-v2
   extensions, then validate before saving.
3. Compile per-cell coverage overrides and additional declared path rules only
   after exact solver constraints and result validation exist for them.
4. Add articulation, separator-capacity, and bridge-aware preprocessing for
   large irregular graphs; benchmark variable and cut reductions.
5. Cache canonical topology preprocessing by a deterministic schema hash for
   batches that share a board but change terminals.
6. Keep a small representative performance corpus in CI and alert on both
   correctness and material median-time/model-size regressions.

## Sources and confidence

The product table uses developer-authored Apple App Store and Google Play
listings, accessed 2026-07-11.  Those are authoritative for product names,
advertised base rules, modes, and broad mechanical differences.  Exact pack
topologies are not published there.  The cube, radial-star, and figure-eight
contracts are therefore implementation fixtures derived from the reference
captures in this repository and are intentionally narrower than claims about
every level in those apps.
