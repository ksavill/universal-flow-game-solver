import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CardActions,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  InputAdornment,
  MenuItem,
  Select,
  SelectChangeEvent,
  Skeleton,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  AutoAwesome,
  Clear,
  DeleteOutline,
  EditOutlined,
  GridView,
  LibraryBooks,
  PhotoCamera,
  Refresh,
  Search,
  ViewList
} from "@mui/icons-material";
import {
  deletePuzzle,
  getPuzzle,
  getPuzzleGraph,
  ImageImportEntry,
  listPuzzles,
  PuzzleEntry,
  renamePuzzle,
  SolveResponse
} from "../api";
import { GameView } from "../components/GameView";
import { ScreenshotArchive } from "../components/ScreenshotArchive";

const entryKey = (entry: PuzzleEntry) => `${entry.source}:${entry.rel_path}:${entry.mtime ?? 0}`;

type LibraryViewProps = {
  onLoadPuzzle: (name: string, text: string, opts?: { autoSolve?: boolean }) => void;
  onImportScreenshot?: () => void;
  onReprocessImports?: (entries: ImageImportEntry[]) => void;
};

export function LibraryView({ onLoadPuzzle, onImportScreenshot, onReprocessImports }: LibraryViewProps) {
  const [section, setSection] = useState<"puzzles" | "screenshots">("puzzles");
  const [entries, setEntries] = useState<PuzzleEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sizeFilter, setSizeFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [page, setPage] = useState(1);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [activeEntry, setActiveEntry] = useState<PuzzleEntry | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [graphCache, setGraphCache] = useState<Record<string, SolveResponse["graph"]>>({});
  const [graphLoading, setGraphLoading] = useState<Record<string, boolean>>({});
  const pendingRef = useRef<Record<string, boolean>>({});
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

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
      return [entry.name, entry.rel_path, entry.meta?.title, entry.meta?.pack, entry.meta?.tags]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    });
  }, [entries, search, typeFilter, sizeFilter]);

  const PAGE_SIZE = isMobile ? 12 : 24;
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));

  useEffect(() => {
    if (page > pageCount) {
      setPage(pageCount);
    } else if (page < 1) {
      setPage(1);
    }
  }, [page, pageCount]);

  useEffect(() => {
    setPage(1);
  }, [search, typeFilter, sizeFilter]);

  const pagedEntries = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [PAGE_SIZE, filtered, page]);

  const effectiveViewMode = isMobile ? "grid" : viewMode;

  useEffect(() => {
    if (effectiveViewMode !== "grid" || loading) {
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
  }, [effectiveViewMode, loading, pagedEntries]);

  const handleTypeChange = (event: SelectChangeEvent<string>) => {
    setTypeFilter(event.target.value);
  };

  const handleSizeChange = (event: SelectChangeEvent<string>) => {
    setSizeFilter(event.target.value);
  };

  const pageButtons = useMemo(() => {
    const first = Math.max(1, Math.min(page - 2, pageCount - 4));
    const last = Math.min(pageCount, first + 4);
    return Array.from({ length: last - first + 1 }, (_, idx) => first + idx);
  }, [page, pageCount]);

  const hasFilters = Boolean(search.trim()) || typeFilter !== "all" || sizeFilter !== "all";
  const userCount = entries.filter((entry) => entry.source === "user").length;

  const clearFilters = () => {
    setSearch("");
    setTypeFilter("all");
    setSizeFilter("all");
  };

  async function handleLoad(entry: PuzzleEntry, opts?: { autoSolve?: boolean }) {
    try {
      const data = await getPuzzle(entry.source, entry.rel_path);
      onLoadPuzzle(data.name, data.text, opts);
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

  const paginationControls = pageCount > 1 ? (
    <Box display="flex" justifyContent="center" alignItems="center" gap={0.75} flexWrap="wrap" py={1}>
      <Button
        variant="text"
        size="small"
        onClick={() => setPage((previous) => Math.max(1, previous - 1))}
        disabled={page <= 1}
      >
        Previous
      </Button>
      {pageButtons.map((pageNumber) => (
        <Button
          key={`page-${pageNumber}`}
          size="small"
          variant={pageNumber === page ? "contained" : "text"}
          onClick={() => setPage(pageNumber)}
          sx={{ minWidth: 36 }}
        >
          {pageNumber}
        </Button>
      ))}
      <Button
        variant="text"
        size="small"
        onClick={() => setPage((previous) => Math.min(pageCount, previous + 1))}
        disabled={page >= pageCount}
      >
        Next
      </Button>
      <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
        Page {page} of {pageCount}
      </Typography>
    </Box>
  ) : null;

  return (
    <Box display="flex" flexDirection="column" gap={2.5}>
      <Card
        sx={{
          background:
            "linear-gradient(135deg, rgba(130,177,255,0.16), rgba(255,82,82,0.08) 60%, rgba(22,26,34,0.95))"
        }}
      >
        <CardContent>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
            <Box sx={{ flex: 1 }}>
              <Box display="flex" gap={1} alignItems="center" mb={0.5}>
                <LibraryBooks color="secondary" />
                <Typography variant="h5" fontWeight={750}>
                  Puzzle library
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary">
                Open an example, continue one of your saved puzzles, or import a new screenshot.
              </Typography>
              <Box display="flex" gap={1} flexWrap="wrap" mt={1.5}>
                <Chip label={`${entries.length} total`} size="small" />
                <Chip label={`${userCount} saved by you`} size="small" color="secondary" variant="outlined" />
              </Box>
            </Box>
            {onImportScreenshot && (
              <Button
                variant="contained"
                startIcon={<PhotoCamera />}
                onClick={onImportScreenshot}
                sx={{ minHeight: 44, alignSelf: { xs: "stretch", sm: "center" } }}
              >
                Import screenshot
              </Button>
            )}
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <Tabs
          value={section}
          onChange={(_event, value) => value && setSection(value)}
          variant="fullWidth"
        >
          <Tab value="puzzles" label="Puzzles" />
          <Tab value="screenshots" label="Uploaded screenshots" />
        </Tabs>
      </Card>

      {section === "screenshots" && (
        <Card>
          <CardContent>
            <ScreenshotArchive
              onOpenResult={(name, text) => onLoadPuzzle(name, text)}
              onReprocess={onReprocessImports}
            />
          </CardContent>
        </Card>
      )}

      {section === "puzzles" && (
        <>
      <Card>
        <CardContent sx={{ py: 2 }}>
          <Stack direction={{ xs: "column", md: "row" }} gap={1.25} alignItems={{ md: "center" }}>
        <TextField
          placeholder="Search names, packs, or tags"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          size="small"
          fullWidth
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Search fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: search ? (
              <InputAdornment position="end">
                <IconButton size="small" aria-label="Clear search" onClick={() => setSearch("")}>
                  <Clear fontSize="small" />
                </IconButton>
              </InputAdornment>
            ) : undefined
          }}
        />
        <Select size="small" value={typeFilter} onChange={handleTypeChange} sx={{ minWidth: 150 }}>
          {types.map((type) => (
            <MenuItem key={type} value={type}>
              {type === "all" ? "All types" : type}
            </MenuItem>
          ))}
        </Select>
        <Select size="small" value={sizeFilter} onChange={handleSizeChange} sx={{ minWidth: 130 }}>
          {sizes.map((size) => (
            <MenuItem key={size} value={size}>
              {size === "all" ? "All sizes" : size}
            </MenuItem>
          ))}
        </Select>
            {!isMobile && (
              <ToggleButtonGroup
                exclusive
                size="small"
                value={viewMode}
                onChange={(_event, value) => value && setViewMode(value)}
                aria-label="Library view"
              >
                <ToggleButton value="grid" aria-label="Grid view"><GridView fontSize="small" /></ToggleButton>
                <ToggleButton value="list" aria-label="List view"><ViewList fontSize="small" /></ToggleButton>
              </ToggleButtonGroup>
            )}
            <Tooltip title="Refresh library">
              <span>
                <IconButton onClick={fetchEntries} disabled={loading} aria-label="Refresh library">
                  <Refresh fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
          <Box display="flex" gap={1} alignItems="center" flexWrap="wrap" mt={1.5}>
            <Typography variant="caption" color="text.secondary">
              Showing {filtered.length} of {entries.length}
            </Typography>
            {hasFilters && (
              <Button size="small" variant="text" startIcon={<Clear />} onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </Box>
        </CardContent>
      </Card>

      {error && <Alert severity="error">{error}</Alert>}

      {effectiveViewMode === "list" && (
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
                          <Button variant="contained" size="small" onClick={() => handleLoad(entry, { autoSolve: true })}>
                            Solve
                          </Button>
                          <Button variant="outlined" size="small" onClick={() => handleLoad(entry)}>
                            Open
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

      {effectiveViewMode === "grid" && (
        <Box
          display="grid"
          gridTemplateColumns={{
            xs: "1fr",
            sm: "repeat(2, minmax(0, 1fr))",
            lg: "repeat(3, minmax(0, 1fr))",
            xl: "repeat(4, minmax(0, 1fr))"
          }}
          gap={{ xs: 1.5, sm: 2 }}
        >
          {(loading ? [] : pagedEntries).map((entry) => {
            const key = entryKey(entry);
            const graph = graphCache[key];
            const isLoading = graphLoading[key];
            return (
              <Card
                key={`thumb-${key}`}
                sx={{
                  minWidth: 0,
                  transition: "transform 160ms ease, border-color 160ms ease",
                  "&:hover": { transform: "translateY(-2px)", borderColor: "rgba(130,177,255,0.42)" }
                }}
              >
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
                            <Skeleton variant="rounded" width="100%" height={160} animation="wave" />
                          ) : (
                            <Typography variant="caption" color="text.secondary">
                              Preview unavailable
                            </Typography>
                          )}
                        </Box>
                      )}
                    </Box>
                    <Box mt={1.25}>
                      <Typography variant="subtitle2" fontWeight={700} noWrap title={entry.name}>
                        {entry.meta?.title || entry.name}
                      </Typography>
                      {entry.meta?.title && (
                        <Typography variant="caption" color="text.secondary" noWrap display="block">
                          {entry.name}
                        </Typography>
                      )}
                      <Box display="flex" gap={0.75} flexWrap="wrap" mt={1}>
                        <Chip label={entry.type_label} size="small" variant="outlined" />
                        {entry.size_label && <Chip label={entry.size_label} size="small" />}
                        <Chip
                          label={entry.source === "user" ? "Saved" : "Example"}
                          size="small"
                          color={entry.source === "user" ? "secondary" : "default"}
                        />
                      </Box>
                      <Typography variant="caption" color="text.secondary" display="block" mt={1}>
                        {entry.meta?.pack ? `${entry.meta.pack} · ` : ""}{entry.colors ?? "?"} colors
                      </Typography>
                    </Box>
                  </CardContent>
                </CardActionArea>
                <CardActions sx={{ px: 2, pb: 1.5, pt: 0, justifyContent: "space-between" }}>
                  <Box display="flex" gap={0.5}>
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={<AutoAwesome fontSize="small" />}
                      onClick={() => handleLoad(entry, { autoSolve: true })}
                    >
                      Solve
                    </Button>
                    <Button size="small" onClick={() => handleLoad(entry)}>Open</Button>
                  </Box>
                  {entry.source === "user" && (
                    <Box>
                      <Tooltip title="Rename">
                        <IconButton size="small" onClick={() => openRename(entry)} aria-label={`Rename ${entry.name}`}>
                          <EditOutlined fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton size="small" color="error" onClick={() => openDelete(entry)} aria-label={`Delete ${entry.name}`}>
                          <DeleteOutline fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  )}
                </CardActions>
              </Card>
            );
          })}
          {!loading && !pagedEntries.length && (
            <Card sx={{ gridColumn: "1 / -1" }}>
              <CardContent sx={{ textAlign: "center", py: 6 }}>
                <Typography variant="h6" gutterBottom>No puzzles found</Typography>
                <Typography variant="body2" color="text.secondary" mb={2}>
                  Try a broader search or clear the active filters.
                </Typography>
                {hasFilters && <Button variant="outlined" onClick={clearFilters}>Clear filters</Button>}
              </CardContent>
            </Card>
          )}
          {loading && (
            Array.from({ length: isMobile ? 3 : 8 }, (_, index) => (
              <Card key={`loading-card-${index}`}>
                <CardContent>
                  <Skeleton variant="rounded" height={160} />
                  <Skeleton width="72%" sx={{ mt: 1.5 }} />
                  <Skeleton width="48%" />
                </CardContent>
              </Card>
            ))
          )}
        </Box>
      )}

      {paginationControls}
        </>
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
