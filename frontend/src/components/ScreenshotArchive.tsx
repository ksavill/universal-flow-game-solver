import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Collapse,
  FormControlLabel,
  IconButton,
  MenuItem,
  Modal,
  Stack,
  Switch,
  TextField,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  Close,
  DeleteOutline,
  ExpandLess,
  ExpandMore,
  Refresh,
  Replay,
  SaveOutlined
} from "@mui/icons-material";
import {
  bulkDeleteImageImports,
  deleteImageImport,
  getImageImport,
  imageImportImageUrl,
  ImageImportEntry,
  listImageImports,
  savePuzzle,
  SolveResponse
} from "../api";
import { GameView } from "./GameView";

type ScreenshotArchiveProps = {
  onOpenResult: (name: string, text: string) => void;
  onReprocess?: (entries: ImageImportEntry[]) => void;
};

type StatusFilter = "all" | "solved" | "unknown" | "failed";
type SortOrder = "newest" | "oldest";
type SolveCacheEntry = SolveResponse | "loading" | "error";

const PAGE_SIZE = 100;

function entryTimestamp(entry: ImageImportEntry): number {
  return entry.updated_at ?? entry.created_at;
}

function matchesStatus(entry: ImageImportEntry, filter: StatusFilter): boolean {
  switch (filter) {
    case "solved":
      return entry.solve_status === "solved";
    case "failed":
      return entry.status === "failed" || entry.solve_status === "failed";
    case "unknown":
      return entry.status !== "failed" && entry.solve_status !== "solved" && entry.solve_status !== "failed";
    default:
      return true;
  }
}

export function ScreenshotArchive({ onOpenResult, onReprocess }: ScreenshotArchiveProps) {
  const [entries, setEntries] = useState<ImageImportEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState<"delete" | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const [page, setPage] = useState(1);
  const [saveNotes, setSaveNotes] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [showSolutions, setShowSolutions] = useState(true);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [solveCache, setSolveCache] = useState<Record<string, SolveCacheEntry>>({});
  const [lightbox, setLightbox] = useState<{ url: string; alt: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listImageImports(1000);
      setEntries(data);
      const availableIds = new Set(data.map((entry) => entry.id));
      setSelectedIds((current) => new Set([...current].filter((id) => availableIds.has(id))));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load uploaded screenshots.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const matched = entries.filter((entry) => {
      const matchesQuery =
        !query ||
        entry.original_name.toLocaleLowerCase().includes(query) ||
        entry.generated_name.toLocaleLowerCase().includes(query) ||
        (entry.geometry ?? "").toLocaleLowerCase().includes(query);
      return matchesQuery && matchesStatus(entry, statusFilter);
    });
    matched.sort((a, b) =>
      sortOrder === "newest"
        ? entryTimestamp(b) - entryTimestamp(a)
        : entryTimestamp(a) - entryTimestamp(b)
    );
    return matched;
  }, [search, statusFilter, sortOrder, entries]);

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((entry) => selectedIds.has(entry.id));
  const someFilteredSelected = filtered.some((entry) => selectedIds.has(entry.id));

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const paged = useMemo(
    () => filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [filtered, currentPage]
  );

  useEffect(() => {
    setPage(1);
  }, [search, statusFilter, sortOrder]);

  // With "show solutions" on, solved entries on the current page expand
  // automatically so results are visible without opening a dedicated view.
  useEffect(() => {
    if (!showSolutions) {
      return;
    }
    setExpandedIds((current) => {
      let changed = false;
      const next = new Set(current);
      paged.forEach((entry) => {
        if (entry.solve_status === "solved" && !next.has(entry.id)) {
          next.add(entry.id);
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [showSolutions, paged]);

  // Fetch solve results for expanded rows that aren't cached yet, a few at a
  // time so a 100-row page doesn't fire 100 concurrent requests.
  useEffect(() => {
    const targets = paged.filter(
      (entry) =>
        entry.solve_status === "solved" && expandedIds.has(entry.id) && !(entry.id in solveCache)
    );
    if (!targets.length) {
      return;
    }
    setSolveCache((current) => {
      const next = { ...current };
      targets.forEach((entry) => {
        next[entry.id] = "loading";
      });
      return next;
    });
    const queue = [...targets];
    const worker = async () => {
      for (let entry = queue.shift(); entry; entry = queue.shift()) {
        const id = entry.id;
        let value: SolveCacheEntry = "error";
        try {
          const record = await getImageImport(id);
          value = record.solve?.result ?? "error";
        } catch {
          value = "error";
        }
        setSolveCache((current) => ({ ...current, [id]: value }));
      }
    };
    void Promise.all(Array.from({ length: Math.min(4, queue.length) }, worker));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paged, expandedIds]);

  function toggleSelection(importId: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(importId)) {
        next.delete(importId);
      } else {
        next.add(importId);
      }
      return next;
    });
  }

  function toggleAllFiltered() {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allFilteredSelected) {
        filtered.forEach((entry) => next.delete(entry.id));
      } else {
        filtered.forEach((entry) => next.add(entry.id));
      }
      return next;
    });
  }

  function toggleExpanded(importId: string) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(importId)) {
        next.delete(importId);
      } else {
        next.add(importId);
      }
      return next;
    });
  }

  function handleShowSolutionsChange(value: boolean) {
    setShowSolutions(value);
    if (!value) {
      setExpandedIds(new Set());
    }
  }

  function handleReprocessSelected() {
    if (!onReprocess) {
      return;
    }
    const targets = filtered.filter((entry) => selectedIds.has(entry.id));
    if (!targets.length) {
      return;
    }
    setSelectedIds(new Set());
    onReprocess(targets);
  }

  async function handleDeleteSelected() {
    const ids = [...selectedIds];
    if (!ids.length || bulkBusy) {
      return;
    }
    if (!window.confirm(`Delete ${ids.length} archived screenshot${ids.length === 1 ? "" : "s"}? This cannot be undone.`)) {
      return;
    }
    setBulkBusy("delete");
    try {
      const result = await bulkDeleteImageImports(ids);
      const removed = new Set([...result.deleted, ...result.missing]);
      setEntries((current) => current.filter((entry) => !removed.has(entry.id)));
      setSelectedIds(new Set());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete selected screenshots.");
    } finally {
      setBulkBusy(null);
    }
  }

  async function handleOpen(entry: ImageImportEntry) {
    setBusyId(entry.id);
    try {
      const record = await getImageImport(entry.id);
      if (!record.result) {
        throw new Error(record.error ?? "This import failed before a puzzle was generated.");
      }
      onOpenResult(record.result.name, record.result.text);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open processed screenshot.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSaveToLibrary(entry: ImageImportEntry) {
    setBusyId(entry.id);
    try {
      const record = await getImageImport(entry.id);
      if (!record.result) {
        throw new Error(record.error ?? "This import failed before a puzzle was generated.");
      }
      const res = await savePuzzle({
        name: record.result.name,
        text: record.result.text,
        overwrite: false,
        metadata: { source_image: entry.original_name, generated: "image_import" }
      });
      setSaveNotes((current) => ({
        ...current,
        [entry.id]: { ok: true, message: `Saved to ${res.path}` }
      }));
    } catch (err) {
      setSaveNotes((current) => ({
        ...current,
        [entry.id]: { ok: false, message: err instanceof Error ? err.message : "Save failed." }
      }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(entry: ImageImportEntry) {
    setBusyId(entry.id);
    try {
      await deleteImageImport(entry.id);
      setEntries((current) => current.filter((item) => item.id !== entry.id));
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(entry.id);
        return next;
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete processed screenshot.");
    } finally {
      setBusyId(null);
    }
  }

  const paginationControls =
    pageCount > 1 ? (
      <Box display="flex" justifyContent="center" alignItems="center" gap={1} py={0.5}>
        <Button
          size="small"
          onClick={() => setPage((previous) => Math.max(1, previous - 1))}
          disabled={currentPage <= 1}
        >
          Previous
        </Button>
        <Typography variant="caption" color="text.secondary">
          Page {currentPage} of {pageCount} · {filtered.length} screenshots
        </Typography>
        <Button
          size="small"
          onClick={() => setPage((previous) => Math.min(pageCount, previous + 1))}
          disabled={currentPage >= pageCount}
        >
          Next
        </Button>
      </Box>
    ) : null;

  return (
    <Stack spacing={1.5}>
      <Box display="flex" alignItems="center" justifyContent="space-between" gap={1}>
        <Typography variant="body2" color="text.secondary">
          Every uploaded screenshot is retained once and can be reprocessed through the current
          pipeline at any time.
        </Typography>
        <Button
          size="small"
          startIcon={loading ? <CircularProgress size={16} /> : <Refresh />}
          onClick={() => void refresh()}
          disabled={loading}
        >
          Refresh
        </Button>
      </Box>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
        <TextField
          label="Search screenshots"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          size="small"
          fullWidth
        />
        <TextField
          label="Status"
          select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          size="small"
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="all">All</MenuItem>
          <MenuItem value="solved">Solved</MenuItem>
          <MenuItem value="unknown">Unknown</MenuItem>
          <MenuItem value="failed">Failed</MenuItem>
        </TextField>
        <TextField
          label="Sort"
          select
          value={sortOrder}
          onChange={(event) => setSortOrder(event.target.value as SortOrder)}
          size="small"
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="newest">Newest first</MenuItem>
          <MenuItem value="oldest">Oldest first</MenuItem>
        </TextField>
      </Stack>
      <Box display="flex" alignItems="center" gap={1} flexWrap="wrap">
        <FormControlLabel
          control={
            <Checkbox
              checked={allFilteredSelected}
              indeterminate={someFilteredSelected && !allFilteredSelected}
              onChange={toggleAllFiltered}
              disabled={!filtered.length || bulkBusy !== null}
            />
          }
          label={`Select all matching (${filtered.length})`}
        />
        {selectedIds.size > 0 && (
          <Chip label={`${selectedIds.size} selected`} size="small" color="primary" />
        )}
        <Box flexGrow={1} />
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={showSolutions}
              onChange={(event) => handleShowSolutionsChange(event.target.checked)}
            />
          }
          label="Show solutions"
        />
        {onReprocess && (
          <Button
            size="small"
            variant="contained"
            startIcon={<Replay />}
            onClick={handleReprocessSelected}
            disabled={!selectedIds.size || bulkBusy !== null}
          >
            Reprocess selected
          </Button>
        )}
        <Button
          size="small"
          color="error"
          startIcon={bulkBusy === "delete" ? <CircularProgress size={16} /> : <DeleteOutline />}
          onClick={() => void handleDeleteSelected()}
          disabled={!selectedIds.size || bulkBusy !== null}
        >
          Delete selected
        </Button>
      </Box>
      {error && <Alert severity="error">{error}</Alert>}
      {!loading && entries.length === 0 && (
        <Alert severity="info">
          Uploaded screenshots will appear here after you import one on the Screenshot page.
        </Alert>
      )}
      {!loading && entries.length > 0 && filtered.length === 0 && (
        <Alert severity="info">No screenshots match the current filters.</Alert>
      )}
      {filtered.length > 0 && (
        <Stack spacing={1}>
          {paginationControls}
          {paged.map((entry) => {
            const rows = entry.grid?.rows;
            const cols = entry.grid?.cols;
            const size = rows && cols ? `${cols} × ${rows}` : null;
            const solved = entry.solve_status === "solved";
            const expanded = solved && expandedIds.has(entry.id);
            const solve = solveCache[entry.id];
            return (
              <Box
                key={entry.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "32px 64px minmax(0, 1fr)",
                    sm: "32px 72px minmax(0, 1fr) auto"
                  },
                  alignItems: "center",
                  gap: 1.25,
                  p: 1,
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 1.5,
                  backgroundColor: "rgba(255,255,255,0.02)"
                }}
              >
                <Checkbox
                  checked={selectedIds.has(entry.id)}
                  onChange={() => toggleSelection(entry.id)}
                  disabled={bulkBusy !== null}
                  inputProps={{ "aria-label": `Select ${entry.original_name}` }}
                  sx={{ p: 0.5 }}
                />
                <Box
                  component="img"
                  src={imageImportImageUrl(entry.id)}
                  alt={entry.original_name}
                  loading="lazy"
                  onClick={() =>
                    setLightbox({ url: imageImportImageUrl(entry.id), alt: entry.original_name })
                  }
                  sx={{
                    width: { xs: 64, sm: 72 },
                    height: 58,
                    objectFit: "cover",
                    borderRadius: 1,
                    cursor: "zoom-in"
                  }}
                />
                <Box minWidth={0}>
                  <Typography variant="subtitle2" noWrap title={entry.generated_name}>
                    {entry.generated_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" display="block" noWrap>
                    {entry.original_name} ·{" "}
                    {new Date(entryTimestamp(entry) * 1000).toLocaleString()}
                  </Typography>
                  <Box display="flex" gap={0.5} flexWrap="wrap" mt={0.5}>
                    {entry.geometry && <Chip label={entry.geometry} size="small" />}
                    {size && <Chip label={size} size="small" />}
                    {entry.status === "failed" ? (
                      <Chip label="Failed" size="small" color="error" />
                    ) : (
                      <Chip label={`${entry.terminal_count} endpoints`} size="small" />
                    )}
                    {solved && (
                      <Chip
                        label={
                          typeof entry.solve_ms === "number"
                            ? `Solved in ${Math.max(1, Math.round(entry.solve_ms))} ms`
                            : "Solved"
                        }
                        size="small"
                        color="success"
                      />
                    )}
                    {entry.solve_status === "failed" && (
                      <Chip label="Solve failed" size="small" color="warning" />
                    )}
                    {(entry.run_count ?? 1) > 1 && (
                      <Chip label={`${entry.run_count} runs`} size="small" variant="outlined" />
                    )}
                  </Box>
                  {(entry.error || entry.solve_error) && (
                    <Typography
                      variant="caption"
                      color="error.main"
                      display="block"
                      noWrap
                      title={entry.error ?? entry.solve_error ?? undefined}
                    >
                      {entry.error ?? entry.solve_error}
                    </Typography>
                  )}
                  {saveNotes[entry.id] && (
                    <Typography
                      variant="caption"
                      color={saveNotes[entry.id].ok ? "success.main" : "error.main"}
                      display="block"
                      noWrap
                      title={saveNotes[entry.id].message}
                    >
                      {saveNotes[entry.id].message}
                    </Typography>
                  )}
                </Box>
                <Stack
                  direction="row"
                  spacing={0.5}
                  sx={{ gridColumn: { xs: "1 / -1", sm: "auto" }, justifyContent: "flex-end" }}
                >
                  {solved && (
                    <IconButton
                      size="small"
                      aria-label={expanded ? "Hide solution" : "Show solution"}
                      title={expanded ? "Hide solution" : "Show solution"}
                      onClick={() => toggleExpanded(entry.id)}
                    >
                      {expanded ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
                    </IconButton>
                  )}
                  <Button
                    size="small"
                    variant="contained"
                    onClick={() => void handleOpen(entry)}
                    disabled={busyId === entry.id || bulkBusy !== null || entry.status === "failed"}
                  >
                    {entry.status === "failed" ? "No result" : "Open"}
                  </Button>
                  <Button
                    size="small"
                    aria-label={`Save ${entry.generated_name} to library`}
                    title="Save to library"
                    onClick={() => void handleSaveToLibrary(entry)}
                    disabled={
                      busyId === entry.id ||
                      bulkBusy !== null ||
                      entry.status === "failed" ||
                      saveNotes[entry.id]?.ok === true
                    }
                    sx={{ minWidth: 36, px: 1 }}
                  >
                    <SaveOutlined fontSize="small" />
                  </Button>
                  <Button
                    size="small"
                    color="inherit"
                    aria-label={`Delete ${entry.generated_name} import`}
                    onClick={() => void handleDelete(entry)}
                    disabled={busyId === entry.id || bulkBusy !== null}
                    sx={{ minWidth: 36, px: 1 }}
                  >
                    <DeleteOutline fontSize="small" />
                  </Button>
                </Stack>
                {solved && (
                  <Box sx={{ gridColumn: "1 / -1" }}>
                    <Collapse in={expanded} unmountOnExit>
                      {solve === "loading" || solve === undefined ? (
                        <Box display="flex" justifyContent="center" py={2}>
                          <CircularProgress size={20} />
                        </Box>
                      ) : solve === "error" ? (
                        <Typography variant="caption" color="error.main" display="block" py={1}>
                          The stored solution could not be loaded — open the result to re-solve it.
                        </Typography>
                      ) : (
                        <GameView
                          graph={solve.graph}
                          nodeColor={solve.node_color}
                          pathEdges={solve.path_edges}
                          paths={solve.paths}
                          showSolution
                          height={isMobile ? 240 : 300}
                        />
                      )}
                    </Collapse>
                  </Box>
                )}
              </Box>
            );
          })}
          {paginationControls}
        </Stack>
      )}

      <Modal
        open={Boolean(lightbox)}
        onClose={() => setLightbox(null)}
        sx={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <Box
          onClick={() => setLightbox(null)}
          sx={{
            position: "relative",
            width: "100vw",
            height: "100dvh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "rgba(8,9,14,0.94)",
            outline: "none"
          }}
        >
          {lightbox && (
            <Box
              component="img"
              src={lightbox.url}
              alt={lightbox.alt}
              sx={{
                maxWidth: "100vw",
                maxHeight: "100dvh",
                objectFit: "contain"
              }}
            />
          )}
          <IconButton
            aria-label="Close full screen image"
            onClick={() => setLightbox(null)}
            sx={{
              position: "absolute",
              top: "calc(env(safe-area-inset-top) + 12px)",
              right: 12,
              width: 44,
              height: 44,
              backgroundColor: "rgba(20,22,30,0.8)",
              "&:hover": { backgroundColor: "rgba(40,44,58,0.9)" }
            }}
          >
            <Close />
          </IconButton>
        </Box>
      </Modal>
    </Stack>
  );
}
