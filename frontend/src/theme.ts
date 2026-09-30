import { createTheme } from "@mui/material/styles";

// Touch screens get 44 px targets; mouse layouts keep their density.
const COARSE = "@media (pointer: coarse)";
const NEUTRAL_TEXT = "rgba(255,255,255,0.9)";
const FILLED_PRIMARY = "#d93636";
const FILLED_PRIMARY_HOVER = "#c42e2e";

export const darkTheme = createTheme({
  palette: {
    mode: "dark",
    primary: {
      main: "#ff5252",
      contrastText: "#ffffff"
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
        root: { borderRadius: 10, minHeight: 40, [COARSE]: { minHeight: 44 } },
        sizeSmall: { minHeight: 32, [COARSE]: { minHeight: 40 } },
        sizeLarge: { minHeight: 48 },
        // The bright accent is for text on dark surfaces; filled buttons use a
        // deeper red so white labels meet 4.5:1 contrast.
        containedPrimary: {
          backgroundColor: FILLED_PRIMARY,
          "&:hover": { backgroundColor: FILLED_PRIMARY_HOVER }
        },
        // Only filled buttons carry the accent color; text and outlined
        // buttons stay neutral so each screen has one obvious main action.
        textPrimary: {
          color: NEUTRAL_TEXT,
          "&:hover": { backgroundColor: "rgba(255,255,255,0.06)" }
        },
        outlinedPrimary: {
          color: NEUTRAL_TEXT,
          borderColor: "rgba(255,255,255,0.24)",
          "&:hover": { borderColor: "rgba(255,255,255,0.45)", backgroundColor: "rgba(255,255,255,0.05)" }
        }
      }
    },
    MuiToggleButton: {
      styleOverrides: {
        root: { [COARSE]: { minHeight: 44, minWidth: 44 } }
      }
    },
    MuiFormControlLabel: {
      styleOverrides: {
        root: { [COARSE]: { minHeight: 44 } }
      }
    },
    MuiCheckbox: {
      styleOverrides: {
        root: { [COARSE]: { minWidth: 44, minHeight: 44 } }
      }
    },
    MuiIconButton: {
      styleOverrides: {
        root: { [COARSE]: { minWidth: 44, minHeight: 44 } },
        sizeSmall: { [COARSE]: { minWidth: 40, minHeight: 40 } }
      }
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: "none", fontWeight: 650, minHeight: 56 }
      }
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600 },
        filledPrimary: { backgroundColor: FILLED_PRIMARY, color: "#fff" }
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
