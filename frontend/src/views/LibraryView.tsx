import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CardActions,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  Select,
  SelectChangeEvent,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography
} from "@mui/material";
import { deletePuzzle, getPuzzle, getPuzzleGraph, listPuzzles, PuzzleEntry, renamePuzzle, SolveResponse } from "../api";
import { GameView } from "../components/GameView";

const entryKey = (entry: PuzzleEntry) => `${entry.source}:${entry.rel_path}:${entry.mtime ?? 0}`;

type LibraryViewProps = {
  onLoadPuzzle: (name: string, text: string) => void;
};

export function LibraryView({ onLoadPuzzle }: LibraryViewProps) {
  const [entries, setEntries] = useState<PuzzleEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sizeFilter, setSizeFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [activeEntry, setActiveEntry] = useState<PuzzleEntry | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [graphCache, setGraphCache] = useState<Record<string, SolveResponse["graph"]>>({});
  const [graphLoading, setGraphLoading] = useState<Record<string, boolean>>({});
  const pendingRef = useRef<Record<string, boolean>>({});

  const fetchEntries = useCallback(async () => {
    try {
      setLoading(true);
      const data = await listPuzzles();
      setEntries(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load puzzles.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEntries();
  }, [fetchEntries]);

  const types = useMemo(() => {
    const uniq = new Set(entries.map((entry) => entry.type_label));
    return ["all", ...Array.from(uniq).sort()];
  }, [entries]);

  const sizes = useMemo(() => {
    const uniq = new Set(entries.map((entry) => entry.size_label).filter(Boolean));
    return ["all", ...Array.from(uniq).sort()];
  }, [entries]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return entries.filter((entry) => {
      if (typeFilter !== "all" && entry.type_label !== typeFilter) {
        return false;
      }
      if (sizeFilter !== "all" && entry.size_label !== sizeFilter) {
        return false;
      }
      if (!query) {
        return true;
      }
      return entry.name.toLowerCase().includes(query) || (entry.meta?.title ?? "").toLowerCase().includes(query);
    });
  }, [entries, search, typeFilter, sizeFilter]);

  const PAGE_SIZE = 50;
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));

  useEffect(() => {
    if (page > pageCount) {
      setPage(pageCount);
    } else if (page < 1) {
      setPage(1);
    }
  }, [page, pageCount]);

  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

  useEffect(() => {
    setPage(1);
  }, [search, typeFilter, sizeFilter]);

  const pagedEntries = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, page]);

  useEffect(() => {
    if (viewMode !== "grid" || loading) {
      return;
    }
    pagedEntries.forEach((entry) => {
      const key = entryKey(entry);
      // Skip if already cached or pending
      if (graphCache[key] || pendingRef.current[key]) {
        return;
      }
      pendingRef.current[key] = true;
      setGraphLoading((p) => ({ ...p, [key]: true }));
      getPuzzleGraph(entry.source, entry.rel_path)
        .then((res) => {
          setGraphCache((c) => ({ ...c, [key]: res.graph }));
        })
        .catch(() => {
          // Mark as failed by not caching; will show "Preview unavailable"
        })
        .finally(() => {
          delete pendingRef.current[key];
          setGraphLoading((p) => ({ ...p, [key]: false }));
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, pagedEntries, viewMode]);

  const handleTypeChange = (event: SelectChangeEvent<string>) => {
    setTypeFilter(event.target.value);
  };

  const handleSizeChange = (event: SelectChangeEvent<string>) => {
    setSizeFilter(event.target.value);
  };

  const handleViewMode = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setViewMode(event.target.value as "grid" | "list");
  };

  const applyPageInput = () => {
    const next = Number(pageInput);
    if (Number.isFinite(next) && next >= 1 && next <= pageCount) {
      setPage(next);
    } else {
      setPageInput(String(page));
    }
  };

  const pageButtons = useMemo(() => {
    return Array.from({ length: pageCount }, (_, idx) => idx + 1);
  }, [pageCount]);

  async function handleLoad(entry: PuzzleEntry) {
    try {
      const data = await getPuzzle(entry.source, entry.rel_path);
      onLoadPuzzle(data.name, data.text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load puzzle.");
    }
  }

  const openRename = (entry: PuzzleEntry) => {
    setActiveEntry(entry);
    setRenameValue(entry.name);
    setRenameDialogOpen(true);
  };

  const openDelete = (entry: PuzzleEntry) => {
    setActiveEntry(entry);
    setDeleteDialogOpen(true);
  };

  const handleRename = async () => {
    if (!activeEntry) {
      return;
    }
    try {
      await renamePuzzle({
        source: activeEntry.source,
        old_name: activeEntry.rel_path,
        new_name: renameValue
      });
      setRenameDialogOpen(false);
      setActiveEntry(null);
      await fetchEntries();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed.");
    }
  };

  const handleDelete = async () => {
    if (!activeEntry) {
      return;
    }
    try {
      await deletePuzzle(activeEntry.source, activeEntry.rel_path);
      setDeleteDialogOpen(false);
      setActiveEntry(null);
      await fetchEntries();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    }
  };

  return (
    <Box display="flex" flexDirection="column" gap={2}>
      <Box display="flex" gap={2} flexWrap="wrap" alignItems="center">
        <TextField
          label="Search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          size="small"
        />
        <Select size="small" value={typeFilter} onChange={handleTypeChange} sx={{ minWidth: 160 }}>
          {types.map((type) => (
            <MenuItem key={type} value={type}>
              {type === "all" ? "All types" : type}
            </MenuItem>
          ))}
        </Select>
        <Select size="small" value={sizeFilter} onChange={handleSizeChange} sx={{ minWidth: 140 }}>
          {sizes.map((size) => (
            <MenuItem key={size} value={size}>
              {size === "all" ? "All sizes" : size}
            </MenuItem>
          ))}
        </Select>
        <Typography variant="body2" color="text.secondary">
          {filtered.length} puzzle(s)
        </Typography>
        <TextField
          label="View"
          select
          value={viewMode}
          onChange={handleViewMode}
          size="small"
          sx={{ width: 140 }}
        >
          <MenuItem value="grid">Grid</MenuItem>
          <MenuItem value="list">List</MenuItem>
        </TextField>
        <Button variant="outlined" size="small" onClick={fetchEntries} disabled={loading}>
          Refresh
        </Button>
      </Box>

      <Box display="flex" flexDirection="column" gap={1}>
        <Box display="flex" gap={1} alignItems="center" flexWrap="wrap">
          <Button
            variant="outlined"
            size="small"
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            disabled={page <= 1}
          >
            Prev
          </Button>
          <Button
            variant="outlined"
            size="small"
            onClick={() => setPage((prev) => Math.min(pageCount, prev + 1))}
            disabled={page >= pageCount}
          >
            Next
          </Button>
          <TextField
            label="Page"
            size="small"
            value={pageInput}
            onChange={(event) => setPageInput(event.target.value)}
            onBlur={applyPageInput}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                applyPageInput();
              }
            }}
            sx={{ width: 120 }}
          />
          <Typography variant="body2" color="text.secondary">
            / {pageCount}
          </Typography>
        </Box>
        <Box display="flex" gap={1} sx={{ overflowX: "auto", pb: 1 }}>
          {pageButtons.map((p) => (
            <Button
              key={`page-${p}`}
              size="small"
              variant={p === page ? "contained" : "outlined"}
              onClick={() => setPage(p)}
            >
              {p}
            </Button>
          ))}
        </Box>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      {viewMode === "list" && (
        <Card>
          <CardContent>
            {loading ? (
              <Box display="flex" justifyContent="center" py={4}>
                <CircularProgress />
              </Box>
          ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell>Size</TableCell>
                    <TableCell>Colors</TableCell>
                    <TableCell>Source</TableCell>
                    <TableCell align="right">Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pagedEntries.map((entry) => (
                    <TableRow key={`row-${entryKey(entry)}`}>
                      <TableCell>
                        <Button variant="text" size="small" onClick={() => handleLoad(entry)}>
                          {entry.name}
                        </Button>
                        {entry.meta?.title && (
                          <Typography variant="caption" color="text.secondary">
                            {entry.meta.title}
                          </Typography>
                        )}
                        {entry.error && (
                          <Typography variant="caption" color="error">
                            {entry.error}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>{entry.type_label}</TableCell>
                      <TableCell>{entry.size_label}</TableCell>
                      <TableCell>{entry.colors ?? "-"}</TableCell>
                      <TableCell>{entry.source}</TableCell>
                      <TableCell align="right">
                        <Box display="flex" gap={1} justifyContent="flex-end">
                          <Button variant="outlined" size="small" onClick={() => handleLoad(entry)}>
                            Load
                          </Button>
                          {entry.source === "user" && (
                            <>
                              <Button variant="outlined" size="small" onClick={() => openRename(entry)}>
                                Rename
                              </Button>
                              <Button
                                variant="outlined"
                                color="error"
                                size="small"
                                onClick={() => openDelete(entry)}
                              >
                                Delete
                              </Button>
                            </>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  ))}
                {!pagedEntries.length && (
                    <TableRow>
                      <TableCell colSpan={6}>
                        <Typography variant="body2" color="text.secondary">
                          No puzzles match the filters.
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {viewMode === "grid" && (
        <Box display="grid" gridTemplateColumns="repeat(auto-fit, minmax(240px, 1fr))" gap={2}>
          {(loading ? [] : pagedEntries).map((entry) => {
            const key = entryKey(entry);
            const graph = graphCache[key];
            const isLoading = graphLoading[key];
            return (
              <Card key={`thumb-${key}`}>
                <CardActionArea onClick={() => handleLoad(entry)}>
                  <CardContent>
                    <Box
                      sx={{
                        width: "100%",
                        minHeight: 160,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center"
                      }}
                    >
                      {graph ? (
                        <GameView graph={graph} compact />
                      ) : (
                        <Box
                          sx={{
                            width: "100%",
                            height: 160,
                            borderRadius: 1,
                            border: "1px solid rgba(255,255,255,0.1)",
                            backgroundColor: "rgba(10,10,16,0.6)",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center"
                          }}
                        >
                          {isLoading ? (
                            <CircularProgress size={24} />
                          ) : (
                            <Typography variant="caption" color="text.secondary">
                              Preview unavailable
                            </Typography>
                          )}
                        </Box>
                      )}
                    </Box>
                    <Box mt={1}>
                      <Typography variant="subtitle2">{entry.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {entry.type_label} · {entry.size_label}
                      </Typography>
                      {entry.meta?.title && (
                        <Typography variant="caption" color="text.secondary">
                          {entry.meta.title}
                        </Typography>
                      )}
                    </Box>
                  </CardContent>
                </CardActionArea>
                {entry.source === "user" && (
                  <CardActions sx={{ px: 2, pb: 2 }}>
                    <Button variant="outlined" size="small" onClick={() => openRename(entry)}>
                      Rename
                    </Button>
                    <Button variant="outlined" color="error" size="small" onClick={() => openDelete(entry)}>
                      Delete
                    </Button>
                  </CardActions>
                )}
              </Card>
            );
          })}
          {!loading && !pagedEntries.length && (
            <Card>
              <CardContent>
                <Typography variant="body2" color="text.secondary">
                  No puzzles match the filters.
                </Typography>
              </CardContent>
            </Card>
          )}
          {loading && (
            <Card>
              <CardContent>
                <Box display="flex" justifyContent="center" py={4}>
                  <CircularProgress />
                </Box>
              </CardContent>
            </Card>
          )}
        </Box>
      )}

      <Dialog open={renameDialogOpen} onClose={() => setRenameDialogOpen(false)}>
        <DialogTitle>Rename puzzle</DialogTitle>
        <DialogContent>
          <DialogContentText>Enter a new file name (must end in .flow or .json).</DialogContentText>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleRename} variant="contained">
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete puzzle</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete {activeEntry?.name}? This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleDelete} color="error" variant="contained">
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
