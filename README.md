# Flow Solver v2

A **Python + React** toolkit for solving *Flow / Numberlink*-style puzzles on **arbitrary graph spaces**:

- **Square/rectangular grids** (with holes, walls, and bridges)
- **Hex grids** (6-neighbor adjacency)
- **Circular/ring boards** (radial + wrap-around connections)
- **Freeform graphs** (arbitrary node/edge structures via JSON)

The solver converts any board into a constraint problem (Z3 or DFS) and provides interactive visualization.

---

## Quickstart (Docker)

The fastest way to get started is with Docker Compose. This launches both the **FastAPI backend** and the **React frontend** with hot reload enabled:

```bash
docker compose up
```

Once running:
- **Frontend UI**: http://localhost:5173
- **Backend API**: http://localhost:8000

To start only one service:

```bash
docker compose up api   # backend only
docker compose up ui    # frontend only
```

That's it! Open http://localhost:5173 to start creating and solving puzzles.

---

## Using the UI

The React UI has three main tabs: **New Puzzle**, **Bulk Import**, and **Library**.

### New Puzzle Tab

Build puzzles interactively with a visual grid editor:

1. **Choose a space type**:
   - `square` — standard grid with 4-neighbor adjacency
   - `hex` — hexagonal grid with 6-neighbor adjacency
   - `circle` — concentric rings with radial + wrap-around connections

2. **Set dimensions** (width × height, or rings × sectors for circle)

3. **Place terminal pairs**: Select a color (A–J) from the palette, then click cells to place terminals. Each color must have **exactly 2** endpoints.

4. **Load into editor** or **Save to library** when ready.

The "Image Import" panel on the right lets you upload a screenshot and auto-detect the grid/terminals (see below).

### Bulk Import Tab

Process multiple puzzle images at once:

1. **Select images** — add one or more screenshots
2. **Choose a crop template** — reusable presets for specific devices/screenshots
3. **Configure the pipeline**:
   - **OCR**: detect level name from the image
   - **Grid**: auto-detect grid dimensions
   - **Terminals**: auto-detect colored dot positions
4. **Run pipeline** — processes all images in batch
5. **Review and save** — edit names, check for duplicates, and save to library

Advanced settings let you tune thresholds for grid line detection, terminal color detection, perspective correction, and more.

### Library Tab

Browse all saved puzzles (both user-created and built-in examples):

- **Thumbnails** show puzzle previews
- Click a puzzle to load it into the **Solve View**
- Filter by type, size, or search by name

### Solve View

After loading a puzzle (from any tab), you enter the Solve View:

1. **Edit the puzzle text** directly if needed
2. **Choose a solver**:
   - `Z3 (SMT)` — constraint-based solver (recommended)
   - `DFS` — depth-first search (experimental)
3. **Set timeout** (default 30 seconds)
4. Click **Solve** to find a solution
5. View the **graph preview** with solution overlay (toggle on/off)
6. **Save to library** with optional metadata (title, author, difficulty, tags)

Toggle **Plotly view** for interactive 2D/3D graph visualization.

---

## Puzzle Types

### Square Grid

The classic Flow Free board. Cells have 4 neighbors (up/down/left/right).

Special tiles:
- `.` — empty cell
- `#` — hole (blocked)
- `+` — bridge (allows paths to cross without connecting)
- `A-Z` — terminal dots (each letter appears exactly twice)

```text
# type: square
# fill: true
A...B
..#..
..+..
..#..
B...A
```

### Hex Grid

Hexagonal cells with 6-neighbor adjacency (odd-r offset layout).

```text
# type: hex
A.B
...
B.A
```

### Circle/Ring Board

Concentric rings where each row is a ring. Cells connect radially between rings and wrap around within each ring.

```text
# type: circle
# core: true
A.B.C.D.
........
........
D.C.B.A.
```

Set `# core: true` to add a center node connected to the innermost ring.

### Freeform Graph (JSON)

For arbitrary topologies, use JSON:

```json
{
  "space": {
    "type": "graph",
    "nodes": {
      "n0": { "pos": [0, 0] },
      "n1": { "pos": [1, 0] },
      "n2": { "pos": [2, 0] }
    },
    "edges": [
      ["n0", "n1"],
      ["n1", "n2"]
    ],
    "edge_overrides": {
      "add": [["n0", "n2"]],
      "remove": [["n1", "n2"]]
    },
    "warps": [["n0", "n2"]],
    "walls": [["n1", "n2"]]
  },
  "terminals": {
    "A": ["n0", "n2"]
  }
}
```

---

## Image Import Pipeline

The UI can extract puzzles from screenshots:

1. **Upload an image** (e.g., a Flow Free screenshot)
2. **Crop** to the puzzle area (or use auto-crop / saved templates)
3. **Run the pipeline**:
   - **OCR** detects level names/numbers (requires Tesseract)
   - **Grid detection** finds row/column lines
   - **Terminal detection** locates colored dots and assigns letters
4. **Apply to builder** populates the grid editor
5. **Save** the generated puzzle

### OCR Setup (Optional)

Install Tesseract for level name detection:
- **Windows**: https://github.com/UB-Mannheim/tesseract/wiki
- **macOS**: `brew install tesseract`
- **Ubuntu**: `sudo apt-get install tesseract-ocr`

Set `TESSERACT_CMD` environment variable if Tesseract isn't in your PATH.

---

## Manual Setup (Without Docker)

### Backend API

```bash
python app.py
```

Or with `uv`:

```bash
uv sync
uv run python app.py
```

Windows note (avoids file-lock issues on external drives):

```bash
uv --cache-dir .uv-cache sync --link-mode=copy
uv --cache-dir .uv-cache run --link-mode=copy python app.py
```

Environment variables:
- `HOST` (default `0.0.0.0`)
- `PORT` (default `8000`)
- `RELOAD` (default `1`)

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The UI runs at http://localhost:5173 and talks to the API at http://localhost:8000.

---

## CLI (Headless)

Solve puzzles from the command line without the UI:

```bash
# Create a virtual environment
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

# Solve a puzzle
.\.venv\Scripts\python -m flow_solver solve examples/puzzles/square_5x5_basic.flow --out out/solution.html

# Visualize the graph (no solving)
.\.venv\Scripts\python -m flow_solver visualize examples/puzzles/square_5x5_basic.flow --out out/graph.html
```

Open the generated `.html` file in your browser.

---

## Puzzle File Format (`.flow`)

A simple ASCII format with directives and grid data.

### Directives

Lines starting with `# key: value` set metadata:

| Directive | Description |
|-----------|-------------|
| `# type: square` | Square grid (4-neighbor) |
| `# type: hex` | Hex grid (6-neighbor) |
| `# type: circle` | Circular rings |
| `# fill: true` | Require all cells to be filled |
| `# core: true` | Add center node for circle boards |

### Cell Symbols

| Symbol | Meaning |
|--------|---------|
| `.` | Empty cell |
| `#` | Hole (no node) |
| `+` | Bridge (2 channels) |
| `A-Z` | Terminal (must appear exactly twice) |

### Examples

See `examples/puzzles/` for sample files:

- `square_5x5_basic.flow` — simple square grid
- `square_3x3_bridge_cross.flow` — bridge crossing
- `hex_4x4_pairs_8colors.flow` — hex grid
- `circle_rings_2x8_core_8colors.flow` — circular with core
- `graph_line_6.json` — freeform graph

---

## API Endpoints

### Puzzle Operations

| Endpoint | Description |
|----------|-------------|
| `GET /puzzles` | List all puzzles |
| `POST /puzzles` | Save a new puzzle |
| `POST /solve` | Solve a puzzle |
| `POST /parse` | Parse and validate |
| `POST /graph` | Build graph from text |

### Image Processing

| Endpoint | Description |
|----------|-------------|
| `POST /image/crop/auto` | Auto-detect crop region |
| `POST /image/classify` | Classify geometry/mode (square/hex/circle/graph + modifiers) |
| `POST /image/grid/detect` | Detect grid dimensions |
| `POST /image/terminals/detect` | Detect terminal positions |
| `POST /image/generate` | Generate puzzle from image |
| `POST /image/ocr` | Extract text from image |

### Crop Templates

| Endpoint | Description |
|----------|-------------|
| `GET /templates/crop` | List saved templates |
| `POST /templates/crop` | Save a new template |
| `GET /templates/crop/{id}/preview` | Template preview image |
| `DELETE /templates/crop/{id}` | Delete a template |

---

## What's Next

- Richer wall syntax in `.flow` files
- Step-by-step solving visualization (state space / search tree)
- Additional solver algorithms
- Puzzle dataset export for ML/RL research
