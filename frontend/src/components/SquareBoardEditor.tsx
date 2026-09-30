import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { Box } from "@mui/material";
import { GameView } from "./GameView";
import { colorName } from "../colorNames";
import { editorGraph, pairStatus, type EditableBoard } from "../core/localBoardEdit";

type SquareBoardEditorProps = {
  grid: string[][];
  colorOf: (letter: string) => string;
  // Apply the current tool (color or eraser) to a cell.
  onCellActivate: (row: number, col: number) => void;
  // Keyboard shortcuts: a letter picks that color, "." / Backspace erases.
  onPickColor?: (letter: string) => void;
  onErase?: (row: number, col: number) => void;
  maxWidth?: number;
};

const srOnly = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap"
} as const;

// Game-style square board for building puzzles by pointer or keyboard.
export function SquareBoardEditor({ grid, colorOf, onCellActivate, onPickColor, onErase, maxWidth = 520 }: SquareBoardEditorProps) {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  const [cursor, setCursor] = useState(0);
  const [keyboard, setKeyboard] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const helpId = useId();

  const board: EditableBoard = useMemo(
    () => ({ width: cols, height: rows, cells: grid.flat(), fill: true }),
    [grid, rows, cols]
  );
  const pairs = useMemo(() => pairStatus(board), [board]);
  const colors = useMemo(
    () => Object.fromEntries(pairs.map(({ letter }) => [letter, colorOf(letter)])),
    [pairs, colorOf]
  );
  const graph = useMemo(() => editorGraph(board, colors), [board, colors]);
  const extent = useMemo(() => ({ minX: 0, maxX: cols - 1, minY: -(rows - 1), maxY: 0 }), [rows, cols]);
  const flaggedLetters = new Set(pairs.filter((pair) => pair.count !== 2).map((pair) => pair.letter));
  const flagged = board.cells.flatMap((cell, index) =>
    flaggedLetters.has(cell) ? [{ x: index % cols, y: -Math.floor(index / cols) }] : []
  );
  const scale = Math.min(96, (maxWidth - 36) / Math.max(rows, cols));
  const height = Math.round(rows * scale + 36);

  if (!rows || !cols) return null;
  const safeCursor = Math.min(cursor, rows * cols - 1);

  const describe = (index: number) => {
    const value = board.cells[index];
    const where = `Row ${Math.floor(index / cols) + 1}, column ${(index % cols) + 1}`;
    return `${where}: ${value === "." ? "empty" : `${colorName(colorOf(value))} dot, color ${value}`}`;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1]
    };
    const move = moves[event.key];
    const row = Math.floor(safeCursor / cols);
    const col = safeCursor % cols;
    if (move) {
      const next =
        Math.min(rows - 1, Math.max(0, row + move[1])) * cols + Math.min(cols - 1, Math.max(0, col + move[0]));
      setCursor(next);
      setAnnouncement(describe(next));
    } else if (event.key === "Enter" || event.key === " ") {
      onCellActivate(row, col);
    } else if ((event.key === "." || event.key === "Backspace" || event.key === "Delete") && onErase) {
      onErase(row, col);
    } else if (/^[a-z]$/i.test(event.key) && onPickColor && !event.ctrlKey && !event.metaKey && !event.altKey) {
      onPickColor(event.key.toUpperCase());
      setAnnouncement(`Color ${event.key.toUpperCase()} selected`);
    } else {
      return;
    }
    event.preventDefault();
    setKeyboard(true);
  };

  return (
    <Box sx={{ position: "relative" }}>
      <Box
        tabIndex={0}
        role="application"
        aria-roledescription="puzzle board"
        aria-label={`Puzzle board, ${rows} rows by ${cols} columns`}
        aria-describedby={helpId}
        onKeyDown={onKeyDown}
        onFocus={() => setAnnouncement(describe(safeCursor))}
        onPointerDown={() => setKeyboard(false)}
        onBlur={() => setKeyboard(false)}
        sx={{
          maxWidth,
          mx: "auto",
          borderRadius: "10px",
          outline: "none",
          WebkitTapHighlightColor: "transparent",
          "&:focus-visible": { boxShadow: "0 0 0 3px #82b1ff" }
        }}
      >
        <GameView
          graph={graph}
          height={height}
          editor={{
            extent,
            selected: keyboard ? { x: safeCursor % cols, y: -Math.floor(safeCursor / cols) } : null,
            flagged,
            onCellClick: (cell) => {
              setCursor(-cell.y * cols + cell.x);
              onCellActivate(-cell.y, cell.x);
            }
          }}
        />
      </Box>
      <Box id={helpId} sx={srOnly}>
        Use the arrow keys to move between cells and Enter to place the selected color. Type a letter to pick a
        color, or a period to clear a cell.
      </Box>
      <Box aria-live="polite" sx={srOnly}>
        {announcement}
      </Box>
    </Box>
  );
}
