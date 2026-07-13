import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  FormControlLabel,
  LinearProgress,
  MenuItem,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { ArrowBack, AutoAwesome, ExpandMore, SaveOutlined, TuneOutlined, DataObjectOutlined, EditOutlined } from "@mui/icons-material";
import { graphFromText, parsePuzzle, savePuzzle, solvePuzzle, ParseResponse, SolveResponse } from "../api";
import { GAME_PALETTE, buildTerminalColorMaps } from "../colors";
import { GameView } from "../components/GameView";
import { GraphPreview } from "../components/GraphPreview";

type SolveViewProps = {
  puzzleName: string;
  puzzleText: string;
  onPuzzleNameChange: (value: string) => void;
  onPuzzleTextChange: (value: string) => void;
  autoSolveToken?: number;
  onBack?: () => void;
  backLabel?: string;
};

const MIN_TIMEOUT_MS = 100;
const MAX_TIMEOUT_MS = 1_000_000;
const GraphPlotly = lazy(async () => ({
  default: (await import("../components/GraphPlotly")).GraphPlotly
}));

function formatDuration(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms)) {
    return null;
  }
  if (ms < 1000) {
    return `${Math.max(1, Math.round(ms))} ms`;
  }
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function SolveView({
  puzzleName,
  puzzleText,
  onPuzzleNameChange,
  onPuzzleTextChange,
  autoSolveToken = 0,
  onBack,
  backLabel = "Back"
}: SolveViewProps) {
  const [fillAll, setFillAll] = useState(true);
  const [solver, setSolver] = useState<"z3" | "dfs">("z3");
  const [checkUnique, setCheckUnique] = useState(false);
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
  const [metaTitle, setMetaTitle] = useState("");
  const [metaAuthor, setMetaAuthor] = useState("");
  const [metaDifficulty, setMetaDifficulty] = useState("");
  const [metaTags, setMetaTags] = useState("");
  const [metaNotes, setMetaNotes] = useState("");
  const [viewMode, setViewMode] = useState<"game" | "graph" | "plotly">("game");
  const [use3d, setUse3d] = useState(false);
  const [showSolutionOverlay, setShowSolutionOverlay] = useState(false);
  const graphAbortRef = useRef<AbortController | null>(null);
  const handledSolveTokenRef = useRef(0);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  useEffect(() => {
    setSaveName(puzzleName);
  }, [puzzleName]);

  // Any edit invalidates the previous solution.
  useEffect(() => {
    setSolveResult(null);
    setShowSolutionOverlay(false);
    setParseResult(null);
  }, [puzzleText]);

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
      setError(null);
      const res = await solvePuzzle({
        name: puzzleName,
        text: puzzleText,
        fill: fillAll,
        solver,
        timeout_ms: timeoutMs,
        check_unique: checkUnique
      });
      setSolveResult(res);
      setGraphResult(res.graph);
      setShowSolutionOverlay(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to solve puzzle.");
    } finally {
      setBusy(false);
    }
  }

  // Auto-solve when a puzzle arrives from the screenshot importer or builder.
  useEffect(() => {
    if (autoSolveToken > 0 && autoSolveToken !== handledSolveTokenRef.current) {
      handledSolveTokenRef.current = autoSolveToken;
      void handleSolve();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSolveToken]);

  async function handleSave() {
    try {
      setBusy(true);
      const res = await savePuzzle({
        name: saveName || puzzleName,
        text: puzzleText,
        overwrite,
        drop_empty: true,
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

  const boardHeight = isMobile ? 340 : 520;

  const summary = useMemo(() => {
    if (!solveResult) {
      return null;
    }
    const colorMaps = buildTerminalColorMaps(solveResult.graph, solveResult.node_color, GAME_PALETTE);
    const totalMsRaw = solveResult.stats?.total_ms;
    const totalMs = typeof totalMsRaw === "number" ? totalMsRaw : null;
    const pathChips = Object.entries(solveResult.paths)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([color, nodes]) => ({
        color,
        hex: colorMaps.colorToHex[color] ?? "#ff5252",
        length: nodes.length
      }));
    return { totalMs, pathChips };
  }, [solveResult]);

  const cellCount = graphResult?.nodes.length ?? 0;
  const colorCount = graphResult?.terminals ? Object.keys(graphResult.terminals).length : 0;

  return (
    <Stack spacing={2} sx={{ maxWidth: 900, mx: "auto" }}>
      <Card
        sx={{
          background:
            "linear-gradient(135deg, rgba(255,82,82,0.16), rgba(130,177,255,0.08) 58%, rgba(22,26,34,0.96))"
        }}
      >
        <CardContent sx={{ pb: { xs: 2, sm: 2.5 } }}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              {onBack && (
                <Button startIcon={<ArrowBack />} size="small" onClick={onBack} sx={{ mb: 0.5, px: 0.5 }}>
                  {backLabel}
                </Button>
              )}
              <Typography variant="h5" noWrap title={puzzleName}>
                {puzzleName}
              </Typography>
              <Box display="flex" gap={0.75} flexWrap="wrap" mt={1}>
                {cellCount > 0 && <Chip label={`${cellCount} cells`} size="small" />}
                {colorCount > 0 && <Chip label={`${colorCount} colors`} size="small" />}
                {solveResult?.unique !== null && solveResult?.unique !== undefined && (
                  <Chip
                    label={solveResult.unique ? "Unique solution" : "Multiple solutions"}
                    size="small"
                    color="secondary"
                  />
                )}
              </Box>
            </Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ width: { xs: "100%", sm: "auto" } }}>
              <Button
                variant="contained"
                size="large"
                startIcon={busy ? <CircularProgress size={18} color="inherit" /> : <AutoAwesome />}
                onClick={handleSolve}
                disabled={busy}
                fullWidth={isMobile}
                sx={{ minHeight: 48, px: 3 }}
              >
                {busy ? "Solving…" : solveResult ? "Solve again" : "Solve puzzle"}
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack spacing={1.5}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              gap={1}
              flexWrap="wrap"
            >
              <ToggleButtonGroup
                exclusive
                size="small"
                value={viewMode}
                onChange={(_event, value) => value && setViewMode(value)}
                aria-label="Board view"
              >
                <ToggleButton value="game">Board</ToggleButton>
                <ToggleButton value="graph">Graph</ToggleButton>
                <ToggleButton value="plotly">Interactive</ToggleButton>
              </ToggleButtonGroup>
              <Box display="flex" gap={1} alignItems="center">
                {viewMode === "plotly" && (
                  <FormControlLabel
                    control={<Switch size="small" checked={use3d} onChange={(event) => setUse3d(event.target.checked)} />}
                    label="3D"
                  />
                )}
                {solveResult && (
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={showSolutionOverlay}
                        onChange={(event) => setShowSolutionOverlay(event.target.checked)}
                      />
                    }
                    label="Solution"
                  />
                )}
              </Box>
            </Box>

            {busy && <LinearProgress />}

            {graphResult ? (
              viewMode === "game" ? (
                <GameView
                  graph={graphResult}
                  nodeColor={solveResult?.node_color}
                  pathEdges={solveResult?.path_edges}
                  paths={solveResult?.paths}
                  showSolution={showSolutionOverlay}
                  height={boardHeight}
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
                    pathEdges={solveResult?.path_edges}
                    paths={solveResult?.paths}
                    showSolution={showSolutionOverlay}
                  />
                </Suspense>
              ) : (
                <GraphPreview
                  graph={graphResult}
                  height={280}
                  nodeColor={solveResult?.node_color}
                  pathEdges={solveResult?.path_edges}
                  paths={solveResult?.paths}
                  showSolution={showSolutionOverlay}
                />
              )
            ) : graphLoading ? (
              <Box
                sx={{
                  height: boardHeight,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center"
                }}
              >
                <CircularProgress size={28} />
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: "center" }}>
                The board preview appears here once the puzzle text parses.
              </Typography>
            )}
            {graphError && (
              <Alert severity="warning" variant="outlined">
                {graphError}
              </Alert>
            )}
            {error && <Alert severity="error">{error}</Alert>}

            {summary && (
              <Box>
                <Box display="flex" gap={0.75} flexWrap="wrap" alignItems="center">
                  <Chip
                    label={
                      summary.totalMs !== null
                        ? `Solved in ${formatDuration(summary.totalMs)}`
                        : "Solved"
                    }
                    color="success"
                    size="small"
                  />
                  {summary.pathChips.map((path) => (
                    <Chip
                      key={path.color}
                      label={`${path.color} · ${path.length}`}
                      size="small"
                      sx={{
                        backgroundColor: `${path.hex}26`,
                        border: `1px solid ${path.hex}`,
                        color: path.hex
                      }}
                    />
                  ))}
                </Box>
                <Typography variant="caption" color="text.secondary" display="block" mt={0.75}>
                  Each chip is one color with the number of cells its path covers.
                </Typography>
              </Box>
            )}
          </Stack>
        </CardContent>
      </Card>

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMore />}>
          <Box display="flex" gap={1} alignItems="center">
            <TuneOutlined fontSize="small" color="secondary" />
            <Typography variant="subtitle1">Solver options</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
            <TextField
              label="Solver"
              select
              value={solver}
              onChange={(event) => setSolver(event.target.value as "z3" | "dfs")}
              size="small"
              sx={{ minWidth: 160 }}
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
              sx={{ width: 150 }}
            />
            <FormControlLabel
              control={
                <Switch
                  checked={checkUnique}
                  onChange={(event) => setCheckUnique(event.target.checked)}
                  disabled={solver !== "z3"}
                />
              }
              label="Check uniqueness"
            />
            <FormControlLabel
              control={<Switch checked={fillAll} onChange={(event) => setFillAll(event.target.checked)} />}
              label="Fill all tiles"
            />
          </Box>
        </AccordionDetails>
      </Accordion>

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMore />}>
          <Box display="flex" gap={1} alignItems="center">
            <EditOutlined fontSize="small" color="secondary" />
            <Typography variant="subtitle1">Edit puzzle source</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
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
              minRows={isMobile ? 8 : 14}
              InputProps={{ sx: { fontFamily: "monospace", fontSize: 13 } }}
            />
            <Box>
              <Button variant="outlined" onClick={handleParse} disabled={busy}>
                Validate structure
              </Button>
            </Box>
            {parseResult?.validation && (
              <Alert severity={parseResult.validation.valid ? "success" : "error"}>
                {parseResult.validation.valid
                  ? `Structure valid: ${parseResult.validation.stats.nodes ?? "?"} nodes, ${parseResult.validation.stats.edges ?? "?"} edges.`
                  : parseResult.validation.errors
                      .map((issue) => `${issue.code}: ${issue.message}`)
                      .join(" ")}
                {parseResult.validation.warnings.length > 0
                  ? ` Warnings: ${parseResult.validation.warnings.map((issue) => issue.message).join(" ")}`
                  : ""}
              </Alert>
            )}
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMore />}>
          <Box display="flex" gap={1} alignItems="center">
            <SaveOutlined fontSize="small" color="secondary" />
            <Typography variant="subtitle1">Save to library</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Save as"
                value={saveName}
                onChange={(event) => setSaveName(event.target.value)}
                size="small"
                fullWidth
                helperText="Use .flow or .json"
              />
              <FormControlLabel
                sx={{ whiteSpace: "nowrap" }}
                control={<Switch checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />}
                label="Overwrite"
              />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Title"
                value={metaTitle}
                onChange={(event) => setMetaTitle(event.target.value)}
                size="small"
                fullWidth
              />
              <TextField
                label="Author"
                value={metaAuthor}
                onChange={(event) => setMetaAuthor(event.target.value)}
                size="small"
                fullWidth
              />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Difficulty"
                value={metaDifficulty}
                onChange={(event) => setMetaDifficulty(event.target.value)}
                size="small"
                fullWidth
              />
              <TextField
                label="Tags (comma-separated)"
                value={metaTags}
                onChange={(event) => setMetaTags(event.target.value)}
                size="small"
                fullWidth
              />
            </Stack>
            <TextField
              label="Notes"
              value={metaNotes}
              onChange={(event) => setMetaNotes(event.target.value)}
              multiline
              minRows={2}
            />
            <Box>
              <Button variant="contained" onClick={handleSave} disabled={busy}>
                Save puzzle
              </Button>
            </Box>
            {saveStatus && <Alert severity="success">{saveStatus}</Alert>}
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMore />}>
          <Box display="flex" gap={1} alignItems="center">
            <DataObjectOutlined fontSize="small" color="secondary" />
            <Typography variant="subtitle1">Raw results</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Solve result
              </Typography>
              {solveResult ? (
                <Box
                  component="pre"
                  sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 12, m: 0 }}
                >
                  {JSON.stringify(
                    {
                      paths: Object.fromEntries(
                        Object.entries(solveResult.paths).map(([k, v]) => [k, v.length])
                      ),
                      unique: solveResult.unique,
                      stats: solveResult.stats,
                      nodes: solveResult.graph.nodes.length,
                      edges: solveResult.graph.edges.length
                    },
                    null,
                    2
                  )}
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Solve the puzzle to see solver statistics.
                </Typography>
              )}
            </Box>
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Parse result
              </Typography>
              {parseResult ? (
                <Box
                  component="pre"
                  sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 12, m: 0 }}
                >
                  {JSON.stringify(parseResult, null, 2)}
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Run Validate structure to see parse metadata.
                </Typography>
              )}
            </Box>
          </Stack>
        </AccordionDetails>
      </Accordion>
    </Stack>
  );
}
