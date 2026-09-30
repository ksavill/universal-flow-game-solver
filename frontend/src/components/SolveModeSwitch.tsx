import type { ReactNode } from "react";
import { Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { CloudOutlined, LockOutlined } from "@mui/icons-material";

export type SolveMode = "server" | "device";

const SOLVE_MODE_KEY = "flow-solver.solve-mode.v1";
// Landscape phones: keep the caption from pushing the board off screen.
const SHORT_SCREEN = "@media (max-height: 520px)";

// Which Solve page the navigation opens. The two pages stay separate documents
// because the on-device solver needs cross-origin isolation.
export function readSolveMode(): SolveMode {
  try {
    return window.localStorage.getItem(SOLVE_MODE_KEY) === "device" ? "device" : "server";
  } catch {
    return "server";
  }
}

export function writeSolveMode(mode: SolveMode) {
  try {
    window.localStorage.setItem(SOLVE_MODE_KEY, mode);
  } catch {
    // Storage can be unavailable in private/restricted browser contexts.
  }
}

const DESCRIPTIONS: Record<SolveMode, string> = {
  server: "All puzzle types, batches and camera photos. Images are sent to the solver server.",
  device: "Private: images never leave this device. Square boards only (beta)."
};

type SolveModeSwitchProps = {
  mode: SolveMode;
  onChange: (mode: SolveMode) => void;
  trailing?: ReactNode;
};

export function SolveModeSwitch({ mode, onChange, trailing }: SolveModeSwitchProps) {
  if (import.meta.env.MODE === "static") {
    return <Stack direction="row" alignItems="center" spacing={1.5} mb={2}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography fontWeight={650}><LockOutlined fontSize="small" sx={{ verticalAlign: "middle", mr: 0.75 }} />Processing on this device</Typography>
        <Typography variant="body2" color="text.secondary">Your images stay here. Square boards only.</Typography>
      </Box>
      {trailing}
    </Stack>;
  }
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={1.5}
      mb={{ xs: 2, md: 3 }}
      sx={{ [SHORT_SCREEN]: { mb: 1.5 } }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={mode}
          onChange={(_, value: SolveMode | null) => value && value !== mode && onChange(value)}
          aria-label="Where to process images"
          sx={{ width: { xs: "100%", sm: "auto" } }}
        >
          <ToggleButton value="server" sx={{ flex: { xs: 1, sm: "none" }, px: 2, gap: 0.75, minHeight: 40 }}>
            <CloudOutlined fontSize="small" />
            Server
          </ToggleButton>
          <ToggleButton value="device" sx={{ flex: { xs: 1, sm: "none" }, px: 2, gap: 0.75, minHeight: 40 }}>
            <LockOutlined fontSize="small" />
            This device
          </ToggleButton>
        </ToggleButtonGroup>
        <Typography variant="body2" color="text.secondary" mt={0.75} sx={{ [SHORT_SCREEN]: { display: "none" } }}>
          {DESCRIPTIONS[mode]}
        </Typography>
      </Box>
      {trailing}
    </Stack>
  );
}
