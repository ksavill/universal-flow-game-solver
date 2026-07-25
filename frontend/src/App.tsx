import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  AppBar,
  Box,
  BottomNavigation,
  BottomNavigationAction,
  Button,
  CircularProgress,
  Container,
  Paper,
  Tab,
  Tabs,
  Toolbar,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { AddCircleOutline, Flag, LibraryBooks, MenuBookOutlined, PhotoCamera } from "@mui/icons-material";
import { getImageImport, getPuzzle, type ImageImportEntry } from "./api";
import {
  AppRoute,
  DocPageId,
  LibrarySection,
  PrimaryView,
  appRoutePath,
  parseAppRoute,
  primaryRoute,
  routePrimaryView
} from "./routes";

const LibraryView = lazy(async () => ({
  default: (await import("./views/LibraryView")).LibraryView
}));
const NewPuzzleView = lazy(async () => ({
  default: (await import("./views/NewPuzzleView")).NewPuzzleView
}));
const ImageView = lazy(async () => ({
  default: (await import("./views/ImageView")).ImageView
}));
const SolveView = lazy(async () => ({
  default: (await import("./views/SolveView")).SolveView
}));
const FlaggedView = lazy(async () => ({
  default: (await import("./views/FlaggedView")).FlaggedView
}));
const DocsView = lazy(async () => ({
  default: (await import("./views/DocsView")).DocsView
}));
const NotFoundView = lazy(async () => ({
  default: (await import("./views/NotFoundView")).NotFoundView
}));

const DEFAULT_TEXT = `# type: square
# fill: true
A...B
.....
.....
.....
B...A
`;

const SOLVE_DRAFT_KEY = "flow-solver.solve-draft.v1";

function clearSolveDraft() {
  try {
    window.localStorage.removeItem(SOLVE_DRAFT_KEY);
  } catch {
    // Storage can be unavailable in private/restricted browser contexts.
  }
}

function loadSolveDraft(): {
  name: string;
  text: string;
  tab: "import" | "new" | "library" | "flagged";
  importId: string | null;
} | null {
  try {
    const raw = window.localStorage.getItem(SOLVE_DRAFT_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as {
      name?: unknown;
      text?: unknown;
      tab?: unknown;
      importId?: unknown;
    };
    if (typeof value.name !== "string" || typeof value.text !== "string" || !value.text.trim()) return null;
    const tab =
      value.tab === "new" || value.tab === "library" || value.tab === "flagged"
        ? value.tab
        : "import";
    const importId = typeof value.importId === "string" && value.importId ? value.importId : null;
    return { name: value.name, text: value.text, tab, importId };
  } catch {
    return null;
  }
}

function ViewFallback() {
  return (
    <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}>
      <CircularProgress size={28} />
    </Box>
  );
}

type AppView = PrimaryView | "solve" | "docs" | "not-found" | "route-loading";

function initialViewForRoute(route: AppRoute): AppView {
  if (route.kind === "primary") return route.view;
  if (route.kind === "library") return "library";
  if (route.kind === "docs") return "docs";
  if (route.kind === "draft") return "solve";
  if (route.kind === "not-found") return "not-found";
  return "route-loading";
}

export default function App() {
  const [restoredDraft] = useState(loadSolveDraft);
  const [route, setRoute] = useState<AppRoute>(() => parseAppRoute(window.location.pathname));
  const [tab, setTab] = useState<PrimaryView>(
    () => routePrimaryView(route) ?? (route.kind === "draft" ? restoredDraft?.tab : null) ?? "import"
  );
  const [view, setView] = useState<AppView>(() => initialViewForRoute(route));
  const [puzzleName, setPuzzleName] = useState(restoredDraft?.name ?? "puzzle.flow");
  const [puzzleText, setPuzzleText] = useState(restoredDraft?.text ?? DEFAULT_TEXT);
  const [puzzleImportId, setPuzzleImportId] = useState<string | null>(
    restoredDraft?.importId ?? null
  );
  const [routeError, setRouteError] = useState<string | null>(null);
  const [solveRequestId, setSolveRequestId] = useState(0);
  const [reprocessRequest, setReprocessRequest] = useState<{
    token: number;
    entries: ImageImportEntry[];
  } | null>(null);
  const autoSolvePathRef = useRef<string | null>(null);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  const navigateRoute = useCallback(
    (
      nextRoute: AppRoute,
      options?: { replace?: boolean; state?: Record<string, unknown> }
    ) => {
      const path = appRoutePath(nextRoute);
      const method = options?.replace ? "replaceState" : "pushState";
      window.history[method](options?.state ?? null, "", path);
      setRoute(nextRoute);
      window.scrollTo({ top: 0, behavior: "auto" });
    },
    []
  );

  useEffect(() => {
    const handlePopState = () => setRoute(parseAppRoute(window.location.pathname));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (route.kind === "not-found") return;
    const canonicalPath = appRoutePath(route);
    if (window.location.pathname !== canonicalPath) {
      window.history.replaceState(window.history.state, "", canonicalPath);
    }
  }, [route]);

  useEffect(() => {
    let active = true;
    setRouteError(null);

    if (route.kind === "primary") {
      setTab(route.view);
      setView(route.view);
      return () => {
        active = false;
      };
    }
    if (route.kind === "library") {
      setTab("library");
      setView("library");
      return () => {
        active = false;
      };
    }
    if (route.kind === "docs") {
      setView("docs");
      return () => {
        active = false;
      };
    }
    if (route.kind === "not-found") {
      setView("not-found");
      return () => {
        active = false;
      };
    }
    if (route.kind === "draft") {
      if (restoredDraft) {
        setPuzzleName(restoredDraft.name);
        setPuzzleText(restoredDraft.text);
        setPuzzleImportId(restoredDraft.importId);
        setTab(restoredDraft.tab);
      }
      setView("solve");
      return () => {
        active = false;
      };
    }

    const historyOrigin = window.history.state?.origin;
    const origin: PrimaryView =
      historyOrigin === "import" ||
      historyOrigin === "new" ||
      historyOrigin === "library" ||
      historyOrigin === "flagged"
        ? historyOrigin
        : "library";
    setTab(origin);
    setView("route-loading");

    void (async () => {
      try {
        if (route.kind === "puzzle") {
          const data = await getPuzzle(route.source, route.relPath);
          if (!active) return;
          setPuzzleName(data.name);
          setPuzzleText(data.text);
          setPuzzleImportId(null);
        } else {
          const record = await getImageImport(route.importId);
          if (!record.result) {
            throw new Error(record.error ?? "This imported puzzle is unavailable.");
          }
          if (!active) return;
          setPuzzleName(record.result.name);
          setPuzzleText(record.result.text);
          setPuzzleImportId(route.importId);
        }
        if (autoSolvePathRef.current === appRoutePath(route)) {
          autoSolvePathRef.current = null;
          setSolveRequestId((previous) => previous + 1);
        }
        setView("solve");
      } catch (error) {
        if (!active) return;
        setRouteError(error instanceof Error ? error.message : "The requested puzzle is unavailable.");
        setView("not-found");
      }
    })();

    return () => {
      active = false;
    };
  }, [restoredDraft, route]);

  useEffect(() => {
    if (view !== "solve") return;
    try {
      window.localStorage.setItem(
        SOLVE_DRAFT_KEY,
        JSON.stringify({
          name: puzzleName,
          text: puzzleText,
          tab,
          importId: puzzleImportId,
          savedAt: Date.now()
        })
      );
    } catch {
      // Storage can be unavailable in private/restricted browser contexts.
    }
  }, [puzzleImportId, puzzleName, puzzleText, tab, view]);

  const handleLoadPuzzle = (
    name: string,
    text: string,
    opts?: {
      autoSolve?: boolean;
      importId?: string | null;
      source?: "examples" | "user";
      relPath?: string;
    }
  ) => {
    setPuzzleName(name);
    setPuzzleText(text);
    setPuzzleImportId(opts?.importId ?? null);
    const nextRoute: AppRoute =
      opts?.source && opts.relPath
        ? { kind: "puzzle", source: opts.source, relPath: opts.relPath }
        : opts?.importId
          ? { kind: "import", importId: opts.importId }
          : { kind: "draft" };
    if (opts?.autoSolve) autoSolvePathRef.current = appRoutePath(nextRoute);
    navigateRoute(nextRoute, { state: { origin: tab } });
  };

  const handleTabChange = (_: unknown, value: PrimaryView) => {
    if (view === "solve") clearSolveDraft();
    navigateRoute(primaryRoute(value));
  };

  // Library hands archived screenshots to the importer's batch pipeline.
  const handleReprocessImports = (entries: ImageImportEntry[]) => {
    setReprocessRequest((prev) => ({ token: (prev?.token ?? 0) + 1, entries }));
    navigateRoute(primaryRoute("import"));
  };

  const viewLabel: Record<PrimaryView, string> = {
    import: "Solve a screenshot",
    new: "Create puzzle",
    library: "Puzzle library",
    flagged: "Flagged reviews"
  };
  const librarySection: LibrarySection =
    route.kind === "library" ? route.section : "puzzles";
  const docsPageId: DocPageId = route.kind === "docs" ? route.pageId : "architecture";
  const mobileTitle =
    view === "solve"
      ? "Solver"
      : view === "docs"
        ? "Docs"
        : view === "not-found"
          ? "Page not found"
          : view === "route-loading"
            ? "Loading"
            : viewLabel[view];

  return (
    <Box
      sx={{
        minHeight: "100vh",
        background:
          "radial-gradient(circle at top, rgba(38,45,58,0.5) 0%, rgba(17,19,27,1) 44%, rgba(10,11,16,1) 100%)"
      }}
    >
      <AppBar
        position="sticky"
        color="transparent"
        elevation={0}
        sx={{
          borderBottom: "1px solid rgba(255,255,255,0.08)",
          backgroundColor: "rgba(14,16,24,0.72)",
          backdropFilter: "blur(10px)",
          paddingTop: "env(safe-area-inset-top)"
        }}
      >
        <Toolbar
          sx={{
            minHeight: isMobile ? 56 : 64,
            py: isMobile ? 1 : 0.5,
            alignItems: "center",
            flexDirection: "row",
            gap: 1
          }}
        >
          <Button
            aria-label="Home"
            color="inherit"
            onClick={() => navigateRoute(primaryRoute("import"))}
            sx={{
              justifyContent: "flex-start",
              textTransform: "none",
              fontWeight: 700,
              flexGrow: 1,
              minWidth: 0,
              px: 0,
              mr: 2,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis"
            }}
          >
            <Typography variant="h6" noWrap>
              {isMobile ? mobileTitle : "Universal Flow Game Solver"}
            </Typography>
          </Button>
          {!isMobile && (
            <Tabs
              value={view === "docs" || view === "not-found" ? false : tab}
              onChange={handleTabChange}
              textColor="inherit"
              scrollButtons={false}
            >
              <Tab value="import" icon={<PhotoCamera fontSize="small" />} iconPosition="start" label="Screenshot" />
              <Tab value="new" icon={<AddCircleOutline fontSize="small" />} iconPosition="start" label="Create" />
              <Tab value="library" icon={<LibraryBooks fontSize="small" />} iconPosition="start" label="Library" />
              <Tab value="flagged" icon={<Flag fontSize="small" />} iconPosition="start" label="Flagged" />
            </Tabs>
          )}
          <Button
            aria-label="Documentation"
            color={view === "docs" ? "primary" : "inherit"}
            startIcon={<MenuBookOutlined />}
            onClick={() => {
              if (view === "solve") clearSolveDraft();
              navigateRoute(
                { kind: "docs", pageId: "architecture" },
                { state: { origin: tab } }
              );
            }}
            sx={{ ml: 0.5, whiteSpace: "nowrap", flexShrink: 0 }}
          >
            Docs
          </Button>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ pb: isMobile ? 12 : 4, pt: isMobile ? 2 : 3 }}>
        <Suspense fallback={<ViewFallback />}>
          {/* Keep the importer mounted while its puzzle is open in the solver so a
              processed batch isn't lost when opening one of its results. */}
          {(view === "import" ||
            ((view === "solve" || view === "route-loading") && tab === "import")) && (
            <Box sx={{ maxWidth: 860, mx: "auto", display: view === "import" ? "block" : "none" }}>
              <ImageView
                onGenerated={(name, text, importId) =>
                  handleLoadPuzzle(name, text, { autoSolve: true, importId })
                }
                reprocessRequest={reprocessRequest}
                onReprocessHandled={() => setReprocessRequest(null)}
              />
            </Box>
          )}
          {view === "new" && (
            <NewPuzzleView
              onCreatePuzzle={(name, text, opts) => handleLoadPuzzle(name, text, opts)}
            />
          )}
          {view === "library" && (
            <LibraryView
              onLoadPuzzle={handleLoadPuzzle}
              section={librarySection}
              onSectionChange={(section) => navigateRoute({ kind: "library", section })}
              onImportScreenshot={() => navigateRoute(primaryRoute("import"))}
              onReprocessImports={handleReprocessImports}
            />
          )}
          {view === "flagged" && (
            <FlaggedView
              onOpenResult={(name, text, importId) =>
                handleLoadPuzzle(name, text, { importId })
              }
              onReprocess={handleReprocessImports}
            />
          )}
          {view === "docs" && (
            <DocsView
              pageId={docsPageId}
              onPageChange={(pageId) =>
                navigateRoute({ kind: "docs", pageId }, { state: { origin: tab } })
              }
              onBack={() => navigateRoute(primaryRoute(tab))}
              backLabel={`Back to ${viewLabel[tab]}`}
            />
          )}
          {view === "route-loading" && <ViewFallback />}
          {view === "not-found" && (
            <NotFoundView
              requestedPath={
                route.kind === "not-found" ? route.requestedPath : window.location.pathname
              }
              message={routeError ?? undefined}
              onBack={() => {
                if (window.history.length > 1) {
                  window.history.back();
                } else {
                  navigateRoute(primaryRoute("import"), { replace: true });
                }
              }}
              onHome={() => navigateRoute(primaryRoute("import"))}
            />
          )}
          {view === "solve" && (
            <SolveView
              puzzleName={puzzleName}
              puzzleText={puzzleText}
              importId={puzzleImportId}
              onPuzzleNameChange={setPuzzleName}
              onPuzzleTextChange={setPuzzleText}
              autoSolveToken={solveRequestId}
              onBack={() => {
                clearSolveDraft();
                navigateRoute(primaryRoute(tab));
              }}
              backLabel={`Back to ${viewLabel[tab]}`}
            />
          )}
        </Suspense>
      </Container>
      {isMobile && (
        <Paper
          elevation={12}
          sx={{
            position: "fixed",
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: (currentTheme) => currentTheme.zIndex.appBar,
            borderTop: "1px solid rgba(255,255,255,0.1)",
            paddingBottom: "env(safe-area-inset-bottom)",
            backgroundColor: "rgba(18,21,29,0.96)",
            backdropFilter: "blur(14px)"
          }}
        >
          <BottomNavigation
            value={view === "docs" || view === "not-found" ? false : tab}
            onChange={handleTabChange}
            showLabels
          >
            <BottomNavigationAction value="import" label="Screenshot" icon={<PhotoCamera />} />
            <BottomNavigationAction value="new" label="Create" icon={<AddCircleOutline />} />
            <BottomNavigationAction value="library" label="Library" icon={<LibraryBooks />} />
            <BottomNavigationAction value="flagged" label="Flagged" icon={<Flag />} />
          </BottomNavigation>
        </Paper>
      )}
    </Box>
  );
}
