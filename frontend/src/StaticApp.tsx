import { useEffect, useState } from "react";
import { AddCircleOutline, LibraryBooks, MenuBookOutlined, PhotoCamera } from "@mui/icons-material";
import { AppBar, Box, Button, Container, Stack, Toolbar, Typography } from "@mui/material";
import { LocalView, type LocalNavigation } from "./views/LocalView";
import { NotFoundView } from "./views/NotFoundView";
import { staticPaths, staticRoute } from "./staticRoutes";

function initialNavigation(): LocalNavigation {
  const route = staticRoute(window.location.pathname);
  return { kind: route === "create" || route === "library" ? route : "solve", token: 0 };
}

export default function StaticApp() {
  const [path, setPath] = useState(window.location.pathname);
  const [navigation, setNavigation] = useState(initialNavigation);
  const route = staticRoute(path);

  function showPath(nextPath: string) {
    setPath(nextPath);
    const next = staticRoute(nextPath);
    // Closing dialogs also prevents hidden overlays from covering Help or 404.
    setNavigation(value => ({ kind: next === "create" || next === "library" ? next : "solve", token: value.token + 1 }));
    window.scrollTo({ top: 0 });
  }

  function navigate(next: keyof typeof staticPaths) {
    const nextPath = staticPaths[next];
    if (window.location.pathname !== nextPath) window.history.pushState(null, "", nextPath);
    showPath(nextPath);
  }

  useEffect(() => {
    const pop = () => showPath(window.location.pathname);
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);

  return <Box sx={{ minHeight: "100vh", background: "radial-gradient(circle at top, #262d3a 0%, #11131b 44%, #0a0b10 100%)" }}>
    <AppBar position="sticky" color="transparent" elevation={0} sx={{ bgcolor: "rgba(14,16,24,.95)", borderBottom: "1px solid rgba(255,255,255,.08)", pt: "env(safe-area-inset-top)" }}>
      <Toolbar sx={{ flexWrap: "wrap", gap: 1, py: 1 }}>
        <Button aria-label="Home" color="inherit" onClick={() => navigate("solve")} sx={{ mr: "auto", textTransform: "none", px: 0 }}>
          <Typography variant="h6">Flow Puzzle Solver</Typography>
        </Button>
        <Stack component="nav" aria-label="Main navigation" direction="row" sx={{ width: { xs: "100%", sm: "auto" }, justifyContent: "space-between" }}>
          {([
            ["solve", "Solve", <PhotoCamera fontSize="small" />],
            ["create", "Create", <AddCircleOutline fontSize="small" />],
            ["library", "Library", <LibraryBooks fontSize="small" />],
            ["help", "Help", <MenuBookOutlined fontSize="small" />]
          ] as const).map(([key, label, icon]) => <Button key={key} color={route === key ? "primary" : "inherit"} aria-current={route === key ? "page" : undefined} onClick={() => navigate(key)} startIcon={icon} sx={{ minHeight: 44, px: 1 }}>{label}</Button>)}
        </Stack>
      </Toolbar>
    </AppBar>
    <Container maxWidth="xl" sx={{ py: { xs: 2, sm: 3 } }}>
      <Box sx={{ display: route === "help" || route === "not-found" ? "none" : "block" }}>
        <LocalView navigation={navigation} onLibraryClose={() => { if (staticRoute(window.location.pathname) === "library") navigate("solve"); }} />
      </Box>
      {route === "help" && <Stack spacing={2} sx={{ maxWidth: 760, mx: "auto", py: 2 }}>
        <Typography variant="h4" component="h1">Solve on your device</Typography>
        <Typography>Select a JPEG or PNG screenshot or photo. Check the board outline, adjust its four corners if needed, then review the detected dots and solve. You can also start a blank board or open a .flow file.</Typography>
        <Typography variant="h6" component="h2">Supported puzzles</Typography>
        <Typography>Square-grid boards from 2 × 2 to 20 × 20, including rectangular grids and manually entered holes. Each color needs exactly two dots. Bridges, warps, walls, other geometries and OCR are not supported yet. A timeout means the solver needs more time; it does not prove a puzzle is impossible.</Typography>
        <Typography variant="h6" component="h2">Progress and stopping</Typography>
        <Typography>During processing, follow the current stage and elapsed time, or expand Processing details for timings and statistics. Download and image stages show measured progress. Search has no reliable percentage or finish-time estimate; solver counters update between checks. Stop ends the current operation and keeps your board available to retry.</Typography>
        <Typography variant="h6" component="h2">Privacy and saved puzzles</Typography>
        <Typography>Images and puzzles are processed in your browser. The solver downloads when needed. Saved puzzles stay in this browser on this device; clearing site data removes them. Export .flow files to keep a copy or move them to another device.</Typography>
        <Typography variant="h6" component="h2">Problem reports</Typography>
        <Typography>Use More options to preview a report and optionally attach a resized image. Reports are saved or downloaded locally. Nothing is submitted automatically. The local inbox keeps up to 50 reports and 5 MB, and removes reports older than 30 days when opened.</Typography>
        <Typography variant="h6" component="h2">Browser requirements</Typography>
        <Typography>Use a current browser with WebAssembly, shared memory and gzip decompression support. Large puzzles may need more time and memory, especially on phones. If local storage is unavailable, export your puzzle instead.</Typography>
        <Button onClick={() => navigate("solve")} sx={{ alignSelf: "flex-start" }}>Back to solver</Button>
        <Button component="a" href="/guide/" target="_blank" rel="noopener noreferrer" sx={{ alignSelf: "flex-start" }}>Read the full puzzle guide (opens a new tab)</Button>
      </Stack>}
      {route === "not-found" && <NotFoundView requestedPath={path} message="This page is not available in the device-only edition. Open a .flow file or choose a puzzle saved in this browser." onBack={() => window.history.length > 1 ? window.history.back() : navigate("solve")} onHome={() => navigate("solve")} />}
    </Container>
    <Stack component="footer" direction="row" justifyContent="center" spacing={2} sx={{ px: 2, pt: 2, pb: 20 }}>
      <Button component="a" href="/guide/" target="_blank" rel="noopener noreferrer" color="inherit">Puzzle guide ↗</Button>
      <Button component="a" href="/privacy/" target="_blank" rel="noopener noreferrer" color="inherit">Privacy ↗</Button>
    </Stack>
  </Box>;
}
