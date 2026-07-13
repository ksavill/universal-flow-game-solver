import { createTheme } from "@mui/material/styles";

export const darkTheme = createTheme({
  palette: {
    mode: "dark",
    primary: {
      main: "#ff5252"
    },
    secondary: {
      main: "#82b1ff"
    },
    success: {
      main: "#4caf7d"
    },
    background: {
      default: "#0b0d12",
      paper: "#151922"
    }
  },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h5: { letterSpacing: "-0.02em", fontWeight: 750 },
    h6: { letterSpacing: "-0.01em", fontWeight: 700 },
    subtitle1: { fontWeight: 650 },
    button: { fontWeight: 700, textTransform: "none" }
  },
  shape: {
    borderRadius: 14
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          border: "1px solid rgba(255,255,255,0.08)",
          backgroundImage: "none",
          boxShadow: "0 14px 42px rgba(0,0,0,0.16)"
        }
      }
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 10, minHeight: 40 },
        sizeSmall: { minHeight: 32 },
        sizeLarge: { minHeight: 48 }
      }
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: "none", fontWeight: 650, minHeight: 56 }
      }
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600 }
      }
    },
    MuiAccordion: {
      defaultProps: { disableGutters: true },
      styleOverrides: {
        root: {
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 14,
          backgroundImage: "none",
          "&:before": { display: "none" },
          "&.Mui-expanded": { margin: 0 }
        }
      }
    },
    MuiAccordionSummary: {
      styleOverrides: {
        root: { minHeight: 52 }
      }
    },
    MuiBottomNavigation: {
      styleOverrides: {
        root: { backgroundColor: "transparent", height: 64 }
      }
    },
    MuiBottomNavigationAction: {
      styleOverrides: {
        root: { minWidth: 64, paddingInline: 4 },
        label: { fontSize: "0.7rem" }
      }
    }
  }
});
