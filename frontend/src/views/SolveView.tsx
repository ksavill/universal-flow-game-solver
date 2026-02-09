import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  FormControlLabel,
  Grid,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography
} from "@mui/material";
import { graphFromText, parsePuzzle, savePuzzle, solvePuzzle, ParseResponse, SolveResponse } from "../api";
import { GameView } from "../components/GameView";
import { GraphPreview } from "../components/GraphPreview";

type SolveViewProps = {
  puzzleName: string;
  puzzleText: string;
  onPuzzleNameChange: (value: string) => void;
  onPuzzleTextChange: (value: string) => void;
  onBack?: () => void;
  backLabel?: string;
};

const MIN_TIMEOUT_MS = 100;
const MAX_TIMEOUT_MS = 1_000_000;
const GraphPlotly = lazy(async () => ({
  default: (await import("../components/GraphPlotly")).GraphPlotly
}));

export function SolveView({
  puzzleName,
  puzzleText,
  onPuzzleNameChange,
  onPuzzleTextChange,
  onBack,
  backLabel = "Back"
}: SolveViewProps) {
  const [fillAll, setFillAll] = useState(true);
  const [solver, setSolver] = useState<"z3" | "dfs">("z3");
  const [timeoutMs, setTimeoutMs] = useState(30000);
  const [parseResult, setParseResult] = useState<ParseResponse | null>(null);
  const [solveResult, setSolveResult] = useState<SolveResponse | null>(null);
  const [graphResult, setGraphResult] = useState<SolveResponse["graph"] | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saveName, setSaveName] = useState(puzzleName);
  const [overwrite, setOverwrite] = useState(false);
  const [dropEmpty, setDropEmpty] = useState(true);
  const [metaTitle, setMetaTitle] = useState("");
  const [metaAuthor, setMetaAuthor] = useState("");
  const [metaDifficulty, setMetaDifficulty] = useState("");
  const [metaTags, setMetaTags] = useState("");
  const [metaNotes, setMetaNotes] = useState("");
  const [viewMode, setViewMode] = useState<"game" | "graph" | "plotly">("game");
  const [use3d, setUse3d] = useState(false);
  const [showSolutionOverlay, setShowSolutionOverlay] = useState(false);
  const graphAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setSaveName(puzzleName);
  }, [puzzleName]);

  useEffect(() => {
    if (!puzzleText.trim()) {
      setGraphResult(null);
      return;
    }
    const timer = setTimeout(async () => {
      graphAbortRef.current?.abort();
      const controller = new AbortController();
      graphAbortRef.current = controller;
      setGraphLoading(true);
      setGraphError(null);
      try {
        const res = await graphFromText(
          {
            name: puzzleName,
            text: puzzleText,
            fill: fillAll
          },
          controller.signal
        );
        setGraphResult(res.graph);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setGraphError(err instanceof Error ? err.message : "Failed to update graph preview.");
      } finally {
        setGraphLoading(false);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [puzzleName, puzzleText, fillAll]);

  async function handleParse() {
    try {
      setBusy(true);
      const res = await parsePuzzle({
        name: puzzleName,
        text: puzzleText,
        fill: fillAll
      });
      setParseResult(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to parse puzzle.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSolve() {
    try {
      setBusy(true);
      const res = await solvePuzzle({
        name: puzzleName,
        text: puzzleText,
        fill: fillAll,
        solver,
        timeout_ms: timeoutMs
      });
      setSolveResult(res);
      setGraphResult(res.graph);
      setShowSolutionOverlay(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to solve puzzle.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGraphPreview() {
    try {
      setBusy(true);
      const res = await graphFromText({
        name: puzzleName,
        text: puzzleText,
        fill: fillAll
      });
      setGraphResult(res.graph);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to build graph preview.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    try {
      setBusy(true);
      const res = await savePuzzle({
        name: saveName || puzzleName,
        text: puzzleText,
        overwrite,
        drop_empty: dropEmpty,
        metadata: {
          title: metaTitle.trim(),
          author: metaAuthor.trim(),
          difficulty: metaDifficulty.trim(),
          tags: metaTags.trim(),
          notes: metaNotes.trim()
        }
      });
      setSaveStatus(`Saved to ${res.path}`);
      if (res.text) {
        onPuzzleTextChange(res.text);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save puzzle.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Grid container spacing={3}>
      <Grid item xs={12} md={7}>
        {onBack && (
          <Box mb={2}>
            <Button variant="outlined" size="small" onClick={onBack}>
              {backLabel}
            </Button>
          </Box>
        )}
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Editor
            </Typography>
            <Stack spacing={2}>
              <TextField
                label="Name"
                value={puzzleName}
                onChange={(event) => onPuzzleNameChange(event.target.value)}
                size="small"
              />
              <TextField
                label="Puzzle text"
                value={puzzleText}
                onChange={(event) => onPuzzleTextChange(event.target.value)}
                multiline
                minRows={16}
              />
              <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
                <FormControlLabel
                  control={<Switch checked={fillAll} onChange={(event) => setFillAll(event.target.checked)} />}
                  label="Fill all tiles"
                />
                <TextField
                  label="View"
                  select
                  value={viewMode}
                  onChange={(event) => setViewMode(event.target.value as "game" | "graph" | "plotly")}
                  size="small"
                  sx={{ minWidth: 120 }}
                >
                  <MenuItem value="game">Game</MenuItem>
                  <MenuItem value="graph">Graph</MenuItem>
                  <MenuItem value="plotly">Plotly</MenuItem>
                </TextField>
                {viewMode === "plotly" && (
                  <FormControlLabel
                    control={<Switch checked={use3d} onChange={(event) => setUse3d(event.target.checked)} />}
                    label="3D"
                  />
                )}
                <TextField
                  label="Solver"
                  select
                  value={solver}
                  onChange={(event) => setSolver(event.target.value as "z3" | "dfs")}
                  size="small"
                >
                  <MenuItem value="z3">Z3 (SMT)</MenuItem>
                  <MenuItem value="dfs">DFS (experimental)</MenuItem>
                </TextField>
                <TextField
                  label="Timeout (ms)"
                  type="number"
                  value={timeoutMs}
                  onChange={(event) => setTimeoutMs(Number(event.target.value))}
                  size="small"
                  inputProps={{ min: MIN_TIMEOUT_MS, max: MAX_TIMEOUT_MS, step: 1000 }}
                />
                <Button variant="outlined" onClick={handleParse} disabled={busy}>
                  Parse
                </Button>
                <Button variant="contained" onClick={handleSolve} disabled={busy}>
                  Solve
                </Button>
              </Box>
              {error && <Alert severity="error">{error}</Alert>}
              {saveStatus && <Alert severity="success">{saveStatus}</Alert>}
            </Stack>
          </CardContent>
        </Card>
        <Card sx={{ mt: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Save to Library
            </Typography>
            <Stack spacing={2}>
              <TextField
                label="Save as"
                value={saveName}
                onChange={(event) => setSaveName(event.target.value)}
                size="small"
                helperText="Use .flow or .json"
              />
              <FormControlLabel
                control={<Switch checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />}
                label="Overwrite if exists"
              />
              <FormControlLabel
                control={<Switch checked={dropEmpty} onChange={(event) => setDropEmpty(event.target.checked)} />}
                label="Drop empty metadata fields"
              />
              <TextField
                label="Title"
                value={metaTitle}
                onChange={(event) => setMetaTitle(event.target.value)}
                size="small"
              />
              <TextField
                label="Author"
                value={metaAuthor}
                onChange={(event) => setMetaAuthor(event.target.value)}
                size="small"
              />
              <TextField
                label="Difficulty"
                value={metaDifficulty}
                onChange={(event) => setMetaDifficulty(event.target.value)}
                size="small"
              />
              <TextField
                label="Tags (comma-separated)"
                value={metaTags}
                onChange={(event) => setMetaTags(event.target.value)}
                size="small"
              />
              <TextField
                label="Notes"
                value={metaNotes}
                onChange={(event) => setMetaNotes(event.target.value)}
                multiline
                minRows={3}
              />
              <Button variant="outlined" onClick={handleSave} disabled={busy}>
                Save puzzle
              </Button>
            </Stack>
          </CardContent>
        </Card>
      </Grid>
      <Grid item xs={12} md={5}>
        <Stack spacing={2}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Graph preview
              </Typography>
              {graphResult ? (
                viewMode === "game" ? (
                  <GameView
                    graph={graphResult}
                    nodeColor={solveResult?.node_color}
                    showSolution={showSolutionOverlay}
                    height={320}
                  />
                ) : viewMode === "plotly" ? (
                  <Suspense
                    fallback={
                      <Box sx={{ py: 6, display: "flex", justifyContent: "center" }}>
                        <CircularProgress size={24} />
                      </Box>
                    }
                  >
                    <GraphPlotly
                      graph={graphResult}
                      use3d={use3d}
                      nodeColor={solveResult?.node_color}
                      showSolution={showSolutionOverlay}
                    />
                  </Suspense>
                ) : (
                  <GraphPreview
                    graph={graphResult}
                    height={220}
                    nodeColor={solveResult?.node_color}
                    showSolution={showSolutionOverlay}
                  />
                )
              ) : graphLoading ? (
                <Typography variant="body2" color="text.secondary">
                  Updating graph preview...
                </Typography>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Build a graph preview to see nodes and edges.
                </Typography>
              )}
              {graphError && (
                <Typography variant="caption" color="error">
                  {graphError}
                </Typography>
              )}
              {solveResult && (
                <Box mt={2} display="flex" gap={1} flexWrap="wrap">
                  {showSolutionOverlay ? (
                    <Button size="small" variant="outlined" onClick={() => setShowSolutionOverlay(false)}>
                      Reset preview
                    </Button>
                  ) : (
                    <Button size="small" variant="outlined" onClick={() => setShowSolutionOverlay(true)}>
                      Show solution
                    </Button>
                  )}
                </Box>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <details open>
                <summary> Solve result </summary>
                {solveResult ? (
                  <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
                    {JSON.stringify(
                      {
                        paths: Object.fromEntries(
                          Object.entries(solveResult.paths).map(([k, v]) => [k, v.length])
                        ),
                        nodes: solveResult.graph.nodes.length,
                        edges: solveResult.graph.edges.length
                      },
                      null,
                      2
                    )}
                  </Box>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    Solve the puzzle to see path lengths.
                  </Typography>
                )}
              </details>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <details>
                <summary> Parse result </summary>
                {parseResult ? (
                  <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
                    {JSON.stringify(parseResult, null, 2)}
                  </Box>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    Parse the puzzle to see metadata.
                  </Typography>
                )}
              </details>
            </CardContent>
          </Card>
        </Stack>
      </Grid>
    </Grid>
  );
}
