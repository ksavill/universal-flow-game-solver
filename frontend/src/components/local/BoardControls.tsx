import type { ReactNode } from "react";
import { Box, ButtonBase, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { Add, CheckCircle, Close, Remove, WarningAmber } from "@mui/icons-material";
import type { PairStatus } from "../../core/localBoardEdit";

const SWATCH = 44;

type SwatchProps = {
  label: string;
  selected?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
};

function Swatch({ label, selected, disabled, onClick, children }: SwatchProps) {
  return (
    <Tooltip title={label} disableInteractive>
      <span>
        <ButtonBase
          aria-label={label}
          aria-pressed={selected}
          disabled={disabled}
          onClick={onClick}
          sx={{
            width: SWATCH,
            height: SWATCH,
            borderRadius: "50%",
            outline: selected ? "3px solid #fff" : "none",
            outlineOffset: 2,
            opacity: disabled ? 0.35 : 1,
            "&.Mui-focusVisible": { outline: "3px solid #82b1ff" }
          }}
        >
          {children}
        </ButtonBase>
      </span>
    </Tooltip>
  );
}

type CellPaletteProps = {
  pairs: PairStatus[];
  colorOf: (letter: string) => string;
  labelOf: (letter: string) => string;
  current: string;
  newColor: string | null;
  onPick: (value: string) => void;
  onClose: () => void;
};

// Shown for a selected cell: pick one of the board's colors, a new color,
// an empty cell or a hole.
export function CellPalette({ pairs, colorOf, labelOf, current, newColor, onPick, onClose }: CellPaletteProps) {
  return (
    <Box
      data-cell-palette=""
      role="group"
      aria-label="Set this cell"
      sx={{
        p: 1.5,
        borderRadius: "16px",
        bgcolor: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.1)"
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1}>
        <Typography variant="subtitle2">Set this cell</Typography>
        <IconButton aria-label="Close cell editor" size="small" onClick={onClose}>
          <Close fontSize="small" />
        </IconButton>
      </Stack>
      <Stack direction="row" flexWrap="wrap" useFlexGap gap={1.25}>
        {pairs.map(({ letter, count }) => (
          <Swatch
            key={letter}
            label={`${labelOf(letter)}${count !== 2 ? `, ${count} of 2 dots` : ""}`}
            selected={current === letter}
            onClick={() => onPick(letter)}
          >
            <Box sx={{ width: 34, height: 34, borderRadius: "50%", bgcolor: colorOf(letter) }} />
          </Swatch>
        ))}
        {newColor && (
          <Swatch label="New color" onClick={() => onPick("new")}>
            <Box
              sx={{
                width: 34,
                height: 34,
                borderRadius: "50%",
                border: `2px dashed ${newColor}`,
                display: "grid",
                placeItems: "center",
                color: newColor
              }}
            >
              <Add fontSize="small" />
            </Box>
          </Swatch>
        )}
        <Swatch label="Empty cell" selected={current === "."} onClick={() => onPick(".")}>
          <Box sx={{ width: 34, height: 34, borderRadius: 1.5, bgcolor: "#0b0b10", border: "2px solid #3d5488" }} />
        </Swatch>
        <Swatch label="Hole (not part of the board)" selected={current === "#"} onClick={() => onPick("#")}>
          <Box sx={{ width: 34, height: 34, borderRadius: 1.5, border: "2px dashed rgba(255,255,255,0.4)" }} />
        </Swatch>
      </Stack>
    </Box>
  );
}

type PairListProps = { pairs: PairStatus[]; colorOf: (letter: string) => string; labelOf: (letter: string) => string };

export function PairList({ pairs, colorOf, labelOf }: PairListProps) {
  if (!pairs.length) {
    return <Typography color="text.secondary">No dots yet. Tap a cell to add one.</Typography>;
  }
  return (
    <Stack direction="row" flexWrap="wrap" useFlexGap gap={1} role="list" aria-label="Color pairs">
      {pairs.map(({ letter, count }) => {
        const ok = count === 2;
        return (
          <Stack
            key={letter}
            direction="row"
            alignItems="center"
            spacing={0.75}
            role="listitem"
            aria-label={`${labelOf(letter)}: ${count} of 2 dots`}
            sx={{
              pl: 0.75,
              pr: 1,
              py: 0.5,
              borderRadius: 99,
              border: "1px solid",
              borderColor: ok ? "rgba(255,255,255,0.1)" : "rgba(255,183,77,0.6)",
              bgcolor: ok ? "transparent" : "rgba(255,183,77,0.08)"
            }}
          >
            <Box sx={{ width: 18, height: 18, borderRadius: "50%", bgcolor: colorOf(letter) }} />
            {ok ? (
              <CheckCircle sx={{ fontSize: 16, color: "success.main" }} />
            ) : (
              <>
                <WarningAmber sx={{ fontSize: 16, color: "#ffb74d" }} />
                <Typography variant="caption" sx={{ color: "#ffb74d", fontWeight: 700 }}>
                  {count}/2
                </Typography>
              </>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

type StepperProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  onChange: (value: number) => void;
};

export function Stepper({ label, value, min, max, disabled, onChange }: StepperProps) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Typography sx={{ minWidth: 64 }} color="text.secondary">
        {label}
      </Typography>
      <IconButton
        aria-label={`Fewer ${label.toLowerCase()}`}
        disabled={disabled || value <= min}
        onClick={() => onChange(value - 1)}
        sx={{ width: 44, height: 44, border: "1px solid rgba(255,255,255,0.12)" }}
      >
        <Remove />
      </IconButton>
      <Typography
        aria-live="polite"
        aria-label={`${value} ${label.toLowerCase()}`}
        sx={{ minWidth: 32, textAlign: "center", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </Typography>
      <IconButton
        aria-label={`More ${label.toLowerCase()}`}
        disabled={disabled || value >= max}
        onClick={() => onChange(value + 1)}
        sx={{ width: 44, height: 44, border: "1px solid rgba(255,255,255,0.12)" }}
      >
        <Add />
      </IconButton>
    </Stack>
  );
}
