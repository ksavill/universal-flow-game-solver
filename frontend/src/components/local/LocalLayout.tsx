import type { ReactNode } from "react";
import { Box, Button, Paper, Stack, Typography } from "@mui/material";
import { AddPhotoAlternateOutlined, PhotoCamera, UploadFile } from "@mui/icons-material";

export type LocalAction = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  icon?: ReactNode;
};

function FileButton({
  label,
  icon,
  variant,
  capture,
  disabled,
  onFile
}: {
  label: string;
  icon: ReactNode;
  variant: "contained" | "outlined";
  capture?: boolean;
  disabled?: boolean;
  onFile: (file: File) => void;
}) {
  return (
    <Button
      component="label"
      size="large"
      variant={variant}
      color={variant === "contained" ? "primary" : "inherit"}
      startIcon={icon}
      disabled={disabled}
      sx={{ minWidth: 180 }}
    >
      {label}
      <input
        aria-label={label}
        type="file"
        accept="image/png,image/jpeg"
        capture={capture ? "environment" : undefined}
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) onFile(file);
        }}
      />
    </Button>
  );
}

type DropZoneProps = {
  touch: boolean;
  dragging: boolean;
  disabled: boolean;
  onFile: (file: File) => void;
  extras: LocalAction[];
};

export function DropZone({ touch, dragging, disabled, onFile, extras }: DropZoneProps) {
  return (
    <Paper
      component="section"
      aria-label="Choose a puzzle image"
      sx={{
        p: { xs: 3, md: 6 },
        minHeight: { md: 420 },
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        border: "2px dashed",
        borderColor: dragging ? "primary.main" : "rgba(255,255,255,0.14)",
        bgcolor: dragging ? "rgba(255,82,82,0.06)" : "background.paper",
        transition: "border-color 120ms, background-color 120ms"
      }}
    >
      <Box
        sx={{
          width: 72,
          height: 72,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          bgcolor: "rgba(255,255,255,0.06)",
          mb: 2
        }}
      >
        <AddPhotoAlternateOutlined sx={{ fontSize: 36 }} />
      </Box>
      <Typography variant="h5" component="h2">
        Solve a Flow puzzle
      </Typography>
      <Typography color="text.secondary" mt={1} mb={3} maxWidth={420}>
        {touch
          ? "Take a photo of an unplayed board, or pick a screenshot."
          : "Drop a screenshot here, paste one with Ctrl+V, or choose a file."}
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems="stretch" width={{ xs: "100%", sm: "auto" }}>
        {touch && (
          <FileButton label="Take photo" icon={<PhotoCamera />} variant="contained" capture disabled={disabled} onFile={onFile} />
        )}
        <FileButton
          label="Choose image"
          icon={<UploadFile />}
          variant={touch ? "outlined" : "contained"}
          disabled={disabled}
          onFile={onFile}
        />
      </Stack>
      <Stack direction="row" flexWrap="wrap" justifyContent="center" useFlexGap gap={0.5} mt={2}>
        {extras.map((action) => (
          <Button key={action.label} color="inherit" disabled={disabled || action.disabled} onClick={action.onClick} sx={{ minHeight: 44, opacity: 0.85 }}>
            {action.label}
          </Button>
        ))}
      </Stack>
      <Typography variant="body2" color="text.secondary" mt={3} maxWidth={440}>
        Works with regular square boards up to 20 × 20. Bridges, walls and warps aren't supported here yet.
      </Typography>
    </Paper>
  );
}

// Primary action pinned above the phone bottom navigation. `children` (such as
// the cell color picker) temporarily replaces the actions.
export function ActionBar({ primary, secondary, children }: { primary?: LocalAction; secondary?: LocalAction; children?: ReactNode }) {
  if (!primary && !secondary && !children) return null;
  return (
    <Paper
      elevation={8}
      sx={{
        position: "fixed",
        left: 0,
        right: 0,
        bottom: "calc(64px + env(safe-area-inset-bottom))",
        zIndex: (theme) => theme.zIndex.appBar - 1,
        px: 2,
        py: 1.25,
        borderRadius: 0,
        borderTop: "1px solid rgba(255,255,255,0.08)",
        bgcolor: "rgba(21,25,34,0.96)",
        backdropFilter: "blur(12px)"
      }}
    >
      {children ?? <Stack direction="row" spacing={1}>
        {secondary && (
          <Button color="inherit" variant="outlined" onClick={secondary.onClick} disabled={secondary.disabled} startIcon={secondary.icon} sx={{ minHeight: 48, flexShrink: 0 }}>
            {secondary.label}
          </Button>
        )}
        {primary && (
          <Button variant="contained" size="large" fullWidth onClick={primary.onClick} disabled={primary.disabled} startIcon={primary.icon} sx={{ minHeight: 48 }}>
            {primary.label}
          </Button>
        )}
      </Stack>}
    </Paper>
  );
}
