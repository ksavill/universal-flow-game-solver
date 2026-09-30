import React from "react";
import ReactDOM from "react-dom/client";
import { CssBaseline, ThemeProvider } from "@mui/material";
import { darkTheme } from "./theme";

// Vite removes the unused branch and its imports from each deployment.
const App = React.lazy(() => import.meta.env.MODE === "static" ? import("./StaticApp") : import("./App"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <React.Suspense fallback={<p role="status">Loading…</p>}><App /></React.Suspense>
    </ThemeProvider>
  </React.StrictMode>
);
