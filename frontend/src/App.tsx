import { lazy, Suspense, useState } from "react";
import {
  AppBar,
  Box,
  CircularProgress,
  Container,
  Tab,
  Tabs,
  Toolbar,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";

const BulkImportView = lazy(async () => ({
  default: (await import("./views/BulkImportView")).BulkImportView
}));
const LibraryView = lazy(async () => ({
  default: (await import("./views/LibraryView")).LibraryView
}));
const NewPuzzleView = lazy(async () => ({
  default: (await import("./views/NewPuzzleView")).NewPuzzleView
}));
const SolveView = lazy(async () => ({
  default: (await import("./views/SolveView")).SolveView
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
  const [tab, setTab] = useState<"new" | "bulk" | "library">("new");
  const [view, setView] = useState<"new" | "bulk" | "library" | "solve">("new");
  const [puzzleName, setPuzzleName] = useState("puzzle.flow");
  const [puzzleText, setPuzzleText] = useState(DEFAULT_TEXT);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  const handleLoadPuzzle = (name: string, text: string) => {
    setPuzzleName(name);
    setPuzzleText(text);
    setView("solve");
  };

  const handleTabChange = (_: unknown, value: "new" | "bulk" | "library") => {
    setTab(value);
    setView(value);
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
          backdropFilter: "blur(10px)"
        }}
      >
        <Toolbar
          sx={{
            minHeight: isMobile ? 56 : 64,
            py: isMobile ? 1 : 0.5,
            alignItems: isMobile ? "stretch" : "center",
            flexDirection: isMobile ? "column" : "row",
            gap: isMobile ? 1 : 0
          }}
        >
          <Typography
            variant="h6"
            sx={{
              fontWeight: 700,
              flexGrow: isMobile ? 0 : 1,
              pr: isMobile ? 0 : 2,
              alignSelf: isMobile ? "flex-start" : "center"
            }}
          >
            Flow Solver
          </Typography>
          <Tabs
            value={tab}
            onChange={handleTabChange}
            textColor="inherit"
            variant={isMobile ? "fullWidth" : "standard"}
            scrollButtons={false}
            sx={{ width: isMobile ? "100%" : "auto", minHeight: isMobile ? 36 : 48 }}
          >
            <Tab value="new" label="New Puzzle" />
            <Tab value="bulk" label="Bulk Import" />
            <Tab value="library" label="Library" />
          </Tabs>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ pb: isMobile ? 6 : 4, pt: isMobile ? 2 : 3 }}>
        <Suspense fallback={<ViewFallback />}>
          {view === "new" && <NewPuzzleView onCreatePuzzle={handleLoadPuzzle} />}
          {view === "bulk" && <BulkImportView />}
          {view === "library" && <LibraryView onLoadPuzzle={handleLoadPuzzle} />}
          {view === "solve" && (
            <SolveView
              puzzleName={puzzleName}
              puzzleText={puzzleText}
              onPuzzleNameChange={setPuzzleName}
              onPuzzleTextChange={setPuzzleText}
              onBack={() => setView(tab)}
              backLabel={`Back to ${
                tab === "new" ? "New Puzzle" : tab === "bulk" ? "Bulk Import" : "Library"
              }`}
            />
          )}
        </Suspense>
      </Container>
    </Box>
  );
}
