import { lazy, Suspense, useState } from "react";
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
import { AddCircleOutline, LibraryBooks, MenuBookOutlined, PhotoCamera } from "@mui/icons-material";
import type { ImageImportEntry } from "./api";

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
const DocsView = lazy(async () => ({
  default: (await import("./views/DocsView")).DocsView
}));

const DEFAULT_TEXT = `# type: square
# fill: true
A...B
.....
.....
.....
B...A
`;

function ViewFallback() {
  return (
    <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}>
      <CircularProgress size={28} />
    </Box>
  );
}

export default function App() {
  type PrimaryView = "import" | "new" | "library";
  const [tab, setTab] = useState<PrimaryView>("import");
  const [view, setView] = useState<PrimaryView | "solve" | "docs">("import");
  const [puzzleName, setPuzzleName] = useState("puzzle.flow");
  const [puzzleText, setPuzzleText] = useState(DEFAULT_TEXT);
  const [solveRequestId, setSolveRequestId] = useState(0);
  const [reprocessRequest, setReprocessRequest] = useState<{
    token: number;
    entries: ImageImportEntry[];
  } | null>(null);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  const handleLoadPuzzle = (name: string, text: string, opts?: { autoSolve?: boolean }) => {
    setPuzzleName(name);
    setPuzzleText(text);
    if (opts?.autoSolve) {
      setSolveRequestId((prev) => prev + 1);
    }
    setView("solve");
  };

  const handleTabChange = (_: unknown, value: PrimaryView) => {
    setTab(value);
    setView(value);
  };

  // Library hands archived screenshots to the importer's batch pipeline.
  const handleReprocessImports = (entries: ImageImportEntry[]) => {
    setReprocessRequest((prev) => ({ token: (prev?.token ?? 0) + 1, entries }));
    setTab("import");
    setView("import");
  };

  const viewLabel: Record<PrimaryView, string> = {
    import: "Solve a screenshot",
    new: "Create puzzle",
    library: "Puzzle library"
  };

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
          <Typography
            variant="h6"
            sx={{
              fontWeight: 700,
              flexGrow: 1,
              pr: 2,
              alignSelf: "center",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis"
            }}
          >
            {isMobile
              ? view === "solve"
                ? "Solver"
                : view === "docs"
                  ? "Docs"
                  : viewLabel[view]
              : "Universal Flow Game Solver"}
          </Typography>
          {!isMobile && (
            <Tabs
              value={view === "docs" ? false : tab}
              onChange={handleTabChange}
              textColor="inherit"
              scrollButtons={false}
            >
              <Tab value="import" icon={<PhotoCamera fontSize="small" />} iconPosition="start" label="Screenshot" />
              <Tab value="new" icon={<AddCircleOutline fontSize="small" />} iconPosition="start" label="Create" />
              <Tab value="library" icon={<LibraryBooks fontSize="small" />} iconPosition="start" label="Library" />
            </Tabs>
          )}
          <Button
            aria-label="Documentation"
            color={view === "docs" ? "primary" : "inherit"}
            startIcon={<MenuBookOutlined />}
            onClick={() => setView("docs")}
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
          {(view === "import" || (view === "solve" && tab === "import")) && (
            <Box sx={{ maxWidth: 860, mx: "auto", display: view === "import" ? "block" : "none" }}>
              <ImageView
                onGenerated={(name, text) => handleLoadPuzzle(name, text, { autoSolve: true })}
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
              onImportScreenshot={() => handleTabChange(null, "import")}
              onReprocessImports={handleReprocessImports}
            />
          )}
          {view === "docs" && (
            <DocsView onBack={() => setView(tab)} backLabel={`Back to ${viewLabel[tab]}`} />
          )}
          {view === "solve" && (
            <SolveView
              puzzleName={puzzleName}
              puzzleText={puzzleText}
              onPuzzleNameChange={setPuzzleName}
              onPuzzleTextChange={setPuzzleText}
              autoSolveToken={solveRequestId}
              onBack={() => setView(tab)}
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
          <BottomNavigation value={tab} onChange={handleTabChange} showLabels>
            <BottomNavigationAction value="import" label="Screenshot" icon={<PhotoCamera />} />
            <BottomNavigationAction value="new" label="Create" icon={<AddCircleOutline />} />
            <BottomNavigationAction value="library" label="Library" icon={<LibraryBooks />} />
          </BottomNavigation>
        </Paper>
      )}
    </Box>
  );
}
