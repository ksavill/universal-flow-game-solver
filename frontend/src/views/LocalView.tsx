import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Snackbar,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery
} from "@mui/material";
import { keyframes, useTheme } from "@mui/material/styles";
import {
  BugReportOutlined,
  CheckCircle,
  CropFree,
  Download,
  EditOutlined,
  ErrorOutline,
  FolderOpenOutlined,
  HourglassBottom,
  InfoOutlined,
  MoreVert,
  NotesOutlined,
  PlayArrow,
  RestartAlt,
  SaveOutlined,
  Stop,
  TuneOutlined,
  UploadFileOutlined,
  ZoomIn,
  ZoomOut
} from "@mui/icons-material";
import { GameView, type GameViewCell } from "../components/GameView";
import { SolveModeSwitch, type SolveMode } from "../components/SolveModeSwitch";
import { CellPalette, PairList, Stepper } from "../components/local/BoardControls";
import { FeedbackDialog, LibraryDialog, SettingsDialog, TextDialog } from "../components/local/LocalDialogs";
import { ActionBar, DropZone, type LocalAction } from "../components/local/LocalLayout";
import { ProcessingPanel } from "../components/local/ProcessingPanel";
import { OutlineEditor, defaultCorners } from "../components/local/OutlineEditor";
import { GAME_PALETTE } from "../colors";
import { capitalized, colorName } from "../colorNames";
import {
  MAX_SIDE,
  MIN_SIDE,
  blankBoard,
  cellIndex,
  cellPoint,
  editorGraph,
  nextLetter,
  pairStatus,
  readBoard,
  resizeBoard,
  setCell,
  writeBoard,
  type EditableBoard
} from "../core/localBoardEdit";
import { displayPaths, parseLocalPuzzle } from "../core/localPuzzle";
import { decodeLocalImage, detectLocally, pixelsCanvas, reportAttachment, solveLocally } from "../processing/localClient";
import type { Pixels, Point } from "../processing/localImage";
import { finishTrace, startTrace, updateTrace, type ProcessingStage, type ProcessingTrace, type ProgressReporter } from "../processing/localProgress";
import type { LocalSolveResult } from "../solver/localSolver";
import {
  deleteLocalPuzzle,
  deleteLocalReport,
  listLocalPuzzles,
  listLocalReports,
  makeFeedbackReport,
  saveLocalPuzzle,
  saveLocalReport,
  type FeedbackReport,
  type SavedLocalPuzzle
} from "../storage/localStore";

const DEMO_COLORS = ["#ff3030", "#30df30", "#3030ff", "#ffff20", "#ff9820"];

type Phase = "empty" | "outline" | "review" | "result";
type DialogName = "text" | "settings" | "library" | "feedback" | null;

const reveal = keyframes`
  from { opacity: 0; transform: scale(0.985); }
  to { opacity: 1; transform: none; }
`;

function download(name: string, text: string, mime = "application/json") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const srOnly = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap"
} as const;

function firstImage(files: FileList | null | undefined): File | null {
  return Array.from(files ?? []).find((file) => /^image\/(png|jpeg)$/.test(file.type)) ?? null;
}

export type LocalNavigation = { kind: "solve" | "create" | "library"; token: number };

export function LocalView({ onSwitchMode = () => {}, navigation, onLibraryClose }: {
  onSwitchMode?: (mode: SolveMode) => void;
  navigation?: LocalNavigation;
  onLibraryClose?: () => void;
}) {
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const touch = useMediaQuery("(pointer: coarse)");
  // Side by side on desktops and on landscape phones/tablets.
  const sideBySide = useMediaQuery("(min-width: 900px), (orientation: landscape) and (min-width: 600px)");
  // Landscape phones: very little height, so the main action moves up.
  const isShort = useMediaQuery("(max-height: 520px)");
  const isolated = window.crossOriginIsolated;

  const [phase, setPhase] = useState<Phase>("empty");
  const [text, setText] = useState("");
  const [colors, setColors] = useState<Record<string, string>>({});
  const [pixels, setPixels] = useState<Pixels | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [corners, setCorners] = useState<Point[]>([]);
  const [rows, setRows] = useState(5);
  const [cols, setCols] = useState(5);
  const [preparedUrl, setPreparedUrl] = useState("");
  const [showPhoto, setShowPhoto] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [result, setResult] = useState<LocalSolveResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState<ProcessingTrace | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [stage, setStage] = useState("manual-entry");
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [saved, setSaved] = useState<SavedLocalPuzzle[]>([]);
  const [reports, setReports] = useState<FeedbackReport[]>([]);
  const [dialog, setDialog] = useState<DialogName>(null);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [zoom, setZoom] = useState<boolean | null>(null);
  const [viewportHeight, setViewportHeight] = useState(() => window.innerHeight);
  const [stageWidth, setStageWidth] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const stageRef = useRef<HTMLDivElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef(0);
  const importRef = useRef<HTMLInputElement>(null);
  const attemptRef = useRef<"not-run" | "running" | "error" | "cancelled" | LocalSolveResult["status"]>("not-run");
  const diagnostics = useRef<Record<string, unknown>>({});

  useEffect(() => {
    mounted.current = true;
    Promise.all([listLocalPuzzles(), listLocalReports()])
      .then(([puzzles, inbox]) => {
        if (mounted.current) {
          setSaved(puzzles);
          setReports(inbox);
        }
      })
      .catch((reason) => {
        if (mounted.current) setError(String(reason.message));
      });
    return () => {
      mounted.current = false;
      // Cancel only on a real unmount: StrictMode's simulated remount runs this
      // cleanup too, and an image chosen right after first paint must survive it.
      const controller = controllerRef.current;
      setTimeout(() => {
        if (!mounted.current) controller?.abort();
      }, 0);
    };
  }, []);

  const board = useMemo(() => readBoard(text), [text]);
  const pairs = useMemo(() => (board ? pairStatus(board) : []), [board]);
  const parsed = useMemo(() => {
    try {
      return { puzzle: parseLocalPuzzle(text), error: "" };
    } catch (reason) {
      return { puzzle: null, error: (reason as Error).message };
    }
  }, [text]);
  // Stable per-letter colors, so adding a color never repaints the others.
  const colorOf = useCallback(
    (letter: string) => colors[letter] ?? GAME_PALETTE[(letter.charCodeAt(0) - 65) % GAME_PALETTE.length],
    [colors]
  );
  const resolvedColors = useMemo(
    () => Object.fromEntries(pairs.map(({ letter }) => [letter, colorOf(letter)])),
    [pairs, colorOf]
  );
  const graph = useMemo(() => (board ? editorGraph(board, resolvedColors) : null), [board, resolvedColors]);
  const paths =
    parsed.puzzle && result?.status === "solved" ? displayPaths(parsed.puzzle, result.paths) : undefined;
  const incomplete = pairs.filter((pair) => pair.count !== 2);
  const extent = useMemo(
    () => (board ? { minX: 0, maxX: board.width - 1, minY: -(board.height - 1), maxY: 0 } : null),
    [board?.width, board?.height]
  );
  // Size the canvas to the board so it isn't letterboxed (GameView caps cells at 96 px).
  const canvas = useMemo(() => {
    const rowsCount = board?.height ?? rows;
    const colsCount = board?.width ?? cols;
    const scale = Math.min(96, (620 - 36) / rowsCount, (700 - 36) / colsCount);
    return { width: Math.round(colsCount * scale + 36), height: Math.round(rowsCount * scale + 36) };
  }, [board?.width, board?.height, rows, cols]);
  const newColor = useMemo(() => {
    const used = new Set(Object.values(resolvedColors).map((hex) => hex.toLowerCase()));
    return GAME_PALETTE.find((hex) => !used.has(hex.toLowerCase())) ?? GAME_PALETTE[0];
  }, [resolvedColors]);

  const solveBlocker = !isolated
    ? "Solving needs this page to reload."
    : !board
      ? "Add a board first."
      : !pairs.length
        ? "Add at least one pair of dots."
        : incomplete.length
          ? "Each color needs exactly two dots."
          : parsed.error;

  async function perform(operationStage: ProcessingStage, action: (signal: AbortSignal, report: ProgressReporter) => Promise<void>) {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError("");
    setStage(operationStage);
    setTrace(startTrace(operationStage, performance.now()));
    attemptRef.current = "running";
    const report: ProgressReporter = update => {
      if (controllerRef.current === controller && !controller.signal.aborted && mounted.current) {
        const now = performance.now();
        setTrace(previous => previous && updateTrace(previous, update, now));
      }
    };
    let failed = false;
    try {
      await action(controller.signal, report);
    } catch (reason) {
      if (controllerRef.current === controller && !controller.signal.aborted && mounted.current) {
        failed = true;
        setError(reason instanceof Error ? reason.message : String(reason));
        attemptRef.current = "error";
      }
    } finally {
      if (controllerRef.current === controller && mounted.current) {
        if (controller.signal.aborted) attemptRef.current = "cancelled";
        else if (attemptRef.current === "running") attemptRef.current = "not-run";
        setBusy(false);
        const now = performance.now();
        setTrace(previous => previous && finishTrace(previous, controller.signal.aborted ? "stopped" : failed ? "error" : "finished", now));
        controllerRef.current = null;
      }
    }
  }

  function cancel() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    attemptRef.current = "cancelled";
    setBusy(false);
    const now = performance.now();
    setTrace(previous => previous && finishTrace(previous, "stopped", now));
    setNotice("Stopped. Your image stayed on this device.");
  }

  function clearImage() {
    setPixels(null);
    setSourceUrl("");
    setPreparedUrl("");
    setCorners([]);
    setShowPhoto(false);
    diagnostics.current = {};
  }

  function changeText(value: string) {
    setTrace(null);
    setText(value);
    setResult(null);
    setError("");
    setStage("manual-entry");
    attemptRef.current = "not-run";
    // Preserve detection evidence, but don't associate an old solution with edited input.
    const { solve: _old, ...rest } = diagnostics.current;
    diagnostics.current = rest;
  }

  function editBoard(next: EditableBoard) {
    changeText(writeBoard(next));
    setPhase("review");
  }

  function startOver() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    setTrace(null);
    clearImage();
    setText("");
    setColors({});
    setResult(null);
    setSelected(null);
    setError("");
    setStage("manual-entry");
    attemptRef.current = "not-run";
    setPhase("empty");
  }

  async function runDetection(source: Pixels, outline: Point[], r: number, c: number, signal: AbortSignal, report: ProgressReporter) {
    setStage("terminal-detection");
    const response = await detectLocally(source, signal, { corners: outline, rows: r, cols: c }, report);
    if (response.kind !== "detected" || signal.aborted) return;
    const { detection } = response;
    setText(detection.text);
    setColors(detection.colors);
    setResult(null);
    setSelected(null);
    setShowPhoto(false);
    setPreparedUrl(pixelsCanvas(response.prepared).toDataURL("image/png"));
    diagnostics.current = {
      ...diagnostics.current,
      rows: r,
      cols: c,
      corners: outline,
      endpoints: detection.endpoints,
      warnings: detection.warnings
    };
    setPhase("review");
    if (!detection.endpoints) setNotice("No dots found. Adjust the outline, or tap cells to add them.");
  }

  async function chooseImage(file: File) {
    await perform("reading", async (signal, report) => {
      clearImage();
      setPhase("empty");
      setText("");
      setResult(null);
      setSelected(null);
      const source = await decodeLocalImage(file, signal, report);
      if (signal.aborted) return;
      setPixels(source);
      setCorners(defaultCorners(source.width, source.height));
      setSourceUrl(pixelsCanvas(source).toDataURL("image/png"));
      diagnostics.current = { imageWidth: source.width, imageHeight: source.height };
      setPhase("outline");
      const response = await detectLocally(source, signal, undefined, report);
      if (signal.aborted) return;
      if (response.kind === "outline" && response.board) {
        const found = response.board;
        setCorners(found.corners);
        setRows(found.rows);
        setCols(found.cols);
        await runDetection(source, found.corners, found.rows, found.cols, signal, report);
      } else {
        setCorners(defaultCorners(source.width, source.height));
      }
    });
  }

  async function findDots() {
    if (!pixels) return;
    await perform("rectifying", (signal, report) => runDetection(pixels, corners, rows, cols, signal, report));
  }

  async function demoImage() {
    const canvas = document.createElement("canvas");
    canvas.width = 520;
    canvas.height = 650;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#080808";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.font = "24px sans-serif";
    ctx.fillText("Local processing demo", 20, 45);
    ctx.strokeStyle = "#446644";
    ctx.lineWidth = 2;
    for (let i = 0; i <= 5; i++) {
      ctx.beginPath();
      ctx.moveTo(10 + i * 100, 85);
      ctx.lineTo(10 + i * 100, 585);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(10, 85 + i * 100);
      ctx.lineTo(510, 85 + i * 100);
      ctx.stroke();
    }
    DEMO_COLORS.forEach((color, row) =>
      [0, 4].forEach((col) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(60 + col * 100, 135 + row * 100, 32, 0, Math.PI * 2);
        ctx.fill();
      })
    );
    const blob = await new Promise<Blob>((resolve) => canvas.toBlob((value) => resolve(value!), "image/png"));
    await chooseImage(new File([blob], "local-demo.png", { type: "image/png" }));
  }

  async function solve(seconds = timeoutSeconds) {
    await perform("validation", async (signal, report) => {
      setResult(null);
      setPhase("review");
      setSelected(null);
      const puzzle = parseLocalPuzzle(text);
      setStage("solving");
      const solved = await solveLocally(puzzle, Math.round(seconds * 1000), signal, report);
      if (signal.aborted) return;
      setResult(solved);
      attemptRef.current = solved.status;
      diagnostics.current = { ...diagnostics.current, solve: solved };
      setShowPhoto(false);
      setPhase("result");
      stageRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  function startBlank() {
    startOver();
    changeText(writeBoard(blankBoard(5, 5)));
    setPhase("review");
  }

  useEffect(() => {
    if (!navigation) return;
    if (navigation.kind === "create") startBlank();
    setDialog(navigation.kind === "library" ? "library" : null);
    // Navigation is an explicit user command, not a reaction to board edits.
  }, [navigation]);

  function loadText(value: string, nextColors: Record<string, string> = {}) {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    setTrace(null);
    clearImage();
    setColors(nextColors);
    setSelected(null);
    changeText(value);
    setPhase(readBoard(value) ? "review" : "empty");
  }

  async function importFlow(file: File) {
    if (file.size > 10000) {
      setError("Puzzle files must be smaller than 10 KB.");
      return;
    }
    const content = await file.text();
    if (!readBoard(content)) {
      try {
        parseLocalPuzzle(content);
        setError("This file isn't a square board this page can open.");
      } catch (reason) {
        setError((reason as Error).message);
      }
      return;
    }
    loadText(content);
  }

  async function savePuzzle() {
    try {
      const puzzle = parseLocalPuzzle(text);
      await saveLocalPuzzle({
        id: crypto.randomUUID(),
        title: `${puzzle.width} × ${puzzle.height} · ${new Date().toLocaleString()}`,
        text,
        colors: resolvedColors,
        createdAt: Date.now()
      });
      setSaved(await listLocalPuzzles());
      setNotice("Saved in this browser. Export a copy to keep a backup.");
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  function pickCell(value: string, keepSelection = false) {
    if (!board || selected === null) return;
    let letter = value;
    if (value === "new") {
      const next = nextLetter(board);
      if (!next) return;
      letter = next;
      setColors((current) => ({ ...current, [next]: newColor }));
    }
    editBoard(setCell(board, selected, letter));
    if (!keepSelection) setSelected(null);
  }

  // Keyboard shortcuts for a selected cell: a letter, "." or Backspace, "#", Escape.
  useEffect(() => {
    if (selected === null || phase !== "review" || dialog || menuAnchor) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      const key = event.key.toUpperCase();
      if (event.key === "Escape") setSelected(null);
      else if (/^[A-Z]$/.test(key) && !event.ctrlKey && !event.metaKey && !event.altKey) pickCell(key, true);
      else if (event.key === "." || event.key === "Backspace" || event.key === "Delete") pickCell(".", true);
      else if (event.key === "#") pickCell("#", true);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    const onResize = () => setViewportHeight(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(() => setStageWidth(stage.clientWidth));
    observer.observe(stage);
    setStageWidth(stage.clientWidth);
    return () => observer.disconnect();
  }, [phase]);

  // Keep the board and its actions on screen: phones reserve room for the
  // app bar, the bottom navigation and the action tray.
  const boardCap = Math.max(isShort ? 160 : 240, viewportHeight - (isPhone ? 268 : isShort ? 216 : 170));
  const fitWidth = Math.min(Math.max(0, stageWidth - (isPhone ? 16 : 32)), canvas.width, boardCap);
  const canvasCell = board ? (canvas.width - 36) / board.width : 0;
  const fitCell = canvas.width ? canvasCell * (fitWidth / canvas.width) : 0;
  // Boards with cells too small to tap can be zoomed to 44 px cells and panned.
  const zoomAvailable =
    Boolean(board) && (phase === "review" || phase === "result") && !showPhoto && fitWidth > 0 && fitCell < 30;
  const zoomed = zoomAvailable && (zoom ?? (touch && phase === "review"));
  const zoomWidth = canvasCell ? Math.round(canvas.width * (44 / canvasCell)) : 0;

  const labelOf = (letter: string) => `${capitalized(colorName(colorOf(letter)))} (${letter})`;
  const describeCell = (index: number) => {
    const value = board?.cells[index] ?? ".";
    return value === "." ? "empty" : value === "#" ? "hole" : `${colorName(colorOf(value))} dot, color ${value}`;
  };

  useEffect(() => {
    if (!board || selected === null || selected >= board.cells.length) return;
    cursorRef.current = selected;
    const row = Math.floor(selected / board.width) + 1;
    const col = (selected % board.width) + 1;
    setAnnouncement(`Row ${row}, column ${col}: ${describeCell(selected)}`);
  }, [selected, text]);

  // Keep the selected cell visible while zoomed.
  useEffect(() => {
    const container = zoomRef.current;
    if (!zoomed || selected === null || !board || !container) return;
    const factor = zoomWidth / canvas.width;
    const x = (18 + canvasCell * ((selected % board.width) + 0.5)) * factor;
    const y = (18 + canvasCell * (Math.floor(selected / board.width) + 0.5)) * factor;
    const margin = 66;
    if (x - margin < container.scrollLeft) container.scrollLeft = x - margin;
    else if (x + margin > container.scrollLeft + container.clientWidth) container.scrollLeft = x + margin - container.clientWidth;
    if (y - margin < container.scrollTop) container.scrollTop = y - margin;
    else if (y + margin > container.scrollTop + container.clientHeight) container.scrollTop = y + margin - container.clientHeight;
  }, [selected, zoomed]);

  const onBoardKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!board || phase !== "review" || busy) return;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1]
    };
    const move = moves[event.key];
    if (move) {
      event.preventDefault();
      if (selected === null) {
        setSelected(Math.min(cursorRef.current, board.cells.length - 1));
        return;
      }
      const col = Math.min(board.width - 1, Math.max(0, (selected % board.width) + move[0]));
      const row = Math.min(board.height - 1, Math.max(0, Math.floor(selected / board.width) + move[1]));
      setSelected(row * board.width + col);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (selected === null) setSelected(Math.min(cursorRef.current, board.cells.length - 1));
      else document.querySelector<HTMLElement>("[data-cell-palette] button")?.focus();
    }
  };

  // Paste a screenshot anywhere on the page.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (busy || dialog) return;
      const file = firstImage(event.clipboardData?.files);
      if (!file) return;
      event.preventDefault();
      void chooseImage(file);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  const onDragOver = (event: DragEvent) => {
    if (busy || !Array.from(event.dataTransfer.types).includes("Files")) return;
    event.preventDefault();
    setDragging(true);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const file = firstImage(event.dataTransfer.files);
    if (file && !busy) void chooseImage(file);
    else if (event.dataTransfer.files.length) setError("Drop a PNG or JPEG image.");
  };

  async function buildReport(description: string, includeImage: boolean) {
    const dataUrl = includeImage && pixels ? reportAttachment(pixels) : undefined;
    const preview = await makeFeedbackReport({
      description,
      stage,
      outcome: error ? "error" : result?.status ?? attemptRef.current,
      error: error || undefined,
      puzzleText: text,
      diagnostics: {
        ...diagnostics.current, crossOriginIsolated: window.crossOriginIsolated,
        processing: trace ? {
          status: trace.status, metrics: trace.metrics, engine: trace.engine,
          elapsedMs: (trace.endedAt ?? performance.now()) - trace.startedAt,
          stages: trace.stages.map(({ stage, elapsedMs }) => ({ stage, elapsedMs }))
        } : undefined
      },
      attachment: dataUrl ? { mime: "image/jpeg", dataUrl } : undefined
    });
    if (new TextEncoder().encode(JSON.stringify(preview)).length > 256 * 1024) {
      throw new Error("Report exceeds 256 KB. Turn off the image attachment.");
    }
    return preview;
  }

  const outlineCells = (predicate: (cell: string, index: number) => boolean): GameViewCell[] =>
    board ? board.cells.flatMap((cell, index) => (predicate(cell, index) ? [cellPoint(board, index)] : [])) : [];
  const flaggedLetters = new Set(incomplete.map((pair) => pair.letter));

  // ---- Per-phase panel content and actions ----
  let heading = "";
  let body = "";
  let primary: LocalAction | undefined;
  const secondary: LocalAction[] = [];
  const startOverAction: LocalAction = { label: "Start over", onClick: startOver, icon: <RestartAlt /> };

  if (phase === "outline") {
    heading = "Line up the board";
    body = "Drag the yellow corners onto the board's outer corners, then set the grid size.";
    primary = { label: "Find dots", onClick: () => void findDots(), disabled: busy || corners.length !== 4, icon: <PlayArrow /> };
    secondary.push(startOverAction);
  } else if (phase === "review") {
    heading = "Check the dots";
    const how = touch ? "Tap any cell" : "Click a cell, or use the arrow keys,";
    body = pixels
      ? `Compare with your game. ${how} to add, change or remove a dot.`
      : `${how} to place a dot. Every color needs exactly two.`;
    primary = { label: "Solve puzzle", onClick: () => void solve(), disabled: busy || Boolean(solveBlocker), icon: <PlayArrow /> };
    if (pixels) secondary.push({ label: "Adjust outline", onClick: () => setPhase("outline"), icon: <CropFree /> });
    secondary.push(startOverAction);
  } else if (phase === "result" && result) {
    if (result.status === "solved") {
      primary = { label: "New puzzle", onClick: startOver, icon: <RestartAlt /> };
      secondary.push(
        { label: "Save", onClick: () => void savePuzzle(), icon: <SaveOutlined /> },
        { label: "Edit dots", onClick: () => setPhase("review"), icon: <EditOutlined /> }
      );
    } else if (result.status === "timeout" && timeoutSeconds < 60) {
      primary = { label: "Try for 60 s", onClick: () => { setTimeoutSeconds(60); void solve(60); }, icon: <PlayArrow /> };
      secondary.push({ label: "Edit dots", onClick: () => setPhase("review"), icon: <EditOutlined /> }, startOverAction);
    } else {
      primary = { label: "Fix the dots", onClick: () => setPhase("review"), icon: <EditOutlined /> };
      secondary.push(startOverAction);
    }
  }

  if (busy) primary = { label: "Stop processing", onClick: cancel, icon: <Stop /> };

  const resultSummary = !result || phase !== "result" ? null : (() => {
    const seconds = `${(result.elapsedMs / 1000).toFixed(2)} s`;
    const details = `${result.checks} solver checks · ${result.cuts} connectivity cuts`;
    const content = {
      solved: { icon: <CheckCircle color="success" />, title: "Solved", text: `Found in ${seconds} and checked independently.` },
      unsat: { icon: <ErrorOutline color="warning" />, title: "No solution", text: "These dots can't all be connected under the fill rule. A missed or extra dot is the usual cause." },
      timeout: { icon: <HourglassBottom color="warning" />, title: "Out of time", text: `The solver stopped after ${timeoutSeconds} s. That doesn't mean the board is unsolvable.` },
      unknown: { icon: <ErrorOutline color="warning" />, title: "No answer", text: result.reason ?? "The solver couldn't decide this board." }
    }[result.status];
    return (
      <Stack spacing={0.5} role="status" sx={{ order: isShort ? -2 : 0 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          {content.icon}
          <Typography variant="h6" component="h2">{content.title}</Typography>
          <Tooltip title={details}>
            <InfoOutlined fontSize="small" sx={{ color: "text.secondary" }} aria-label={details} />
          </Tooltip>
        </Stack>
        <Typography color="text.secondary">{content.text}</Typography>
      </Stack>
    );
  })();

  const photoToggle = preparedUrl && (phase === "review" || phase === "result") && (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={showPhoto ? "photo" : "board"}
      onChange={(_, value) => value && setShowPhoto(value === "photo")}
      aria-label="Show board or original image"
    >
      <ToggleButton value="board" sx={{ px: 2 }}>Board</ToggleButton>
      <ToggleButton value="photo" sx={{ px: 2 }}>Original</ToggleButton>
    </ToggleButtonGroup>
  );

  const stageContent = (() => {
    if (phase === "outline" && pixels) {
      return (
        <OutlineEditor
          imageUrl={sourceUrl}
          imageWidth={pixels.width}
          imageHeight={pixels.height}
          corners={corners}
          rows={rows}
          cols={cols}
          disabled={busy}
          maxHeight={isPhone ? "56vh" : "min(620px, max(220px, calc(100svh - 170px)))"}
          onChange={setCorners}
        />
      );
    }
    if (showPhoto && preparedUrl) {
      return (
        <Box
          component="img"
          src={preparedUrl}
          alt="The board as cropped from your image"
          sx={{
            display: "block",
            mx: "auto",
            width: "100%",
            maxWidth: Math.min(canvas.width, boardCap),
            aspectRatio: `${cols} / ${rows}`,
            borderRadius: "10px",
            objectFit: "contain"
          }}
        />
      );
    }
    if (graph && board && extent) {
      const editing = phase === "review" && !busy;
      const view = (
        <GameView
          graph={graph}
          paths={paths}
          showSolution={Boolean(paths)}
          height={canvas.height}
          displayWidth={zoomed ? zoomWidth : undefined}
          editor={
            editing
              ? {
                  extent,
                  selected: selected === null ? null : cellPoint(board, selected),
                  holes: outlineCells((cell) => cell === "#"),
                  flagged: outlineCells((cell) => flaggedLetters.has(cell)),
                  onCellClick: (cell) => {
                    const index = cellIndex(board, cell);
                    boardRef.current?.focus({ preventScroll: true });
                    setSelected((current) => (current === index ? null : index));
                  }
                }
              : undefined
          }
        />
      );
      return (
        <>
          <Box
            ref={boardRef}
            key={paths ? "solved" : "board"}
            tabIndex={editing ? 0 : undefined}
            role={editing ? "application" : undefined}
            aria-roledescription={editing ? "puzzle board" : undefined}
            aria-label={editing ? `Puzzle board, ${board.height} rows by ${board.width} columns` : undefined}
            aria-describedby={editing ? "local-board-help" : undefined}
            onKeyDown={onBoardKeyDown}
            sx={{
              borderRadius: "10px",
              outline: "none",
              WebkitTapHighlightColor: "transparent",
              "&:focus-visible": { boxShadow: "0 0 0 3px #82b1ff" },
              "@media (prefers-reduced-motion: no-preference)": { animation: `${reveal} 220ms ease-out` }
            }}
          >
            {zoomed ? (
              <Box
                ref={zoomRef}
                data-board-zoom=""
                sx={{ overflow: "auto", maxHeight: boardCap, overscrollBehavior: "contain", borderRadius: "10px" }}
              >
                <Box sx={{ width: zoomWidth }}>{view}</Box>
              </Box>
            ) : (
              <Box sx={{ maxWidth: boardCap, mx: "auto" }}>{view}</Box>
            )}
          </Box>
          <Box id="local-board-help" sx={srOnly}>
            Use the arrow keys to move between cells. Type a letter to place that color, a period to clear a cell,
            or # for a hole. Press Enter to choose from the color list.
          </Box>
          <Box aria-live="polite" sx={srOnly}>
            {editing ? announcement : ""}
          </Box>
        </>
      );
    }
    return (
      <Box sx={{ minHeight: 320, display: "grid", placeItems: "center", p: 3, textAlign: "center" }}>
        {!busy && (
          <Typography color="text.secondary">This text can't be shown as a board. Use Edit as text to fix it.</Typography>
        )}
      </Box>
    );
  })();

  const panelSecondary = isPhone ? secondary.slice(1) : secondary;
  const cellPalette = phase === "review" && board && selected !== null && (
    <CellPalette
      pairs={pairs}
      colorOf={colorOf}
      labelOf={labelOf}
      current={board.cells[selected] ?? "."}
      newColor={nextLetter(board) ? newColor : null}
      onPick={pickCell}
      onClose={() => setSelected(null)}
    />
  );

  return (
    <Box
      onDragOver={onDragOver}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
      sx={{ maxWidth: 1180, mx: "auto", pb: isPhone && phase !== "empty" ? 10 : 0 }}
    >
      <SolveModeSwitch
        mode="device"
        onChange={onSwitchMode}
        trailing={
          <>
            {phase !== "empty" && !isPhone && (
              <Button color="inherit" startIcon={<RestartAlt />} onClick={startOver} sx={{ minHeight: 44 }}>
                New puzzle
              </Button>
            )}
            <IconButton aria-label="More options" onClick={(event) => setMenuAnchor(event.currentTarget)} sx={{ width: 44, height: 44 }}>
              <MoreVert />
            </IconButton>
          </>
        }
      />

      <Stack spacing={1.5} mb={error || !isolated ? 2 : 0}>
        {!isolated && (
          <Alert
            severity="warning"
            action={<Button color="inherit" onClick={() => window.location.reload()}>Reload</Button>}
          >
            Solving needs this page to reload with its security headers. You can still check and edit boards.
          </Alert>
        )}
        {error && (
          <Alert severity="error" onClose={() => setError("")}>
            {error}
          </Alert>
        )}
      </Stack>

      {trace && <ProcessingPanel trace={trace} onStop={cancel} />}

      {phase === "empty" ? (
        <Box sx={{ position: "relative", borderRadius: 3.5 }}>
          <DropZone
            touch={touch}
            dragging={dragging}
            disabled={busy}
            onFile={(file) => void chooseImage(file)}
            extras={[
              { label: "Try a demo", onClick: () => void demoImage() },
              { label: "Blank board", onClick: startBlank },
              { label: "Open .flow file", onClick: () => importRef.current?.click() },
              ...(saved.length ? [{ label: `Saved (${saved.length})`, onClick: () => setDialog("library") }] : [])
            ]}
          />
        </Box>
      ) : (
        <Box
          sx={{
            display: "grid",
            gap: { xs: 2, md: 3 },
            gridTemplateColumns: sideBySide ? "minmax(0, 1fr) minmax(280px, 340px)" : "minmax(0, 1fr)",
            alignItems: "start"
          }}
        >
          <Paper
            ref={stageRef}
            sx={{
              position: "relative",
              p: { xs: 1, sm: 2 },
              scrollMarginTop: 80,
              outline: dragging ? "2px dashed" : "none",
              outlineColor: "primary.main"
            }}
          >
            {(photoToggle || board) && phase !== "outline" && (
              <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1} px={0.5} minHeight={36}>
                <Typography variant="body2" color="text.secondary">
                  {board ? `${board.height} × ${board.width}` : ""}
                  {board && !board.fill ? " · fill not required" : ""}
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  {zoomAvailable && (
                    <Tooltip title={zoomed ? "Fit the board" : "Zoom in to edit"}>
                      <IconButton
                        aria-label={zoomed ? "Fit the board on screen" : "Zoom in on the board"}
                        aria-pressed={zoomed}
                        onClick={() => setZoom(!zoomed)}
                        sx={{ border: "1px solid rgba(255,255,255,0.12)" }}
                      >
                        {zoomed ? <ZoomOut /> : <ZoomIn />}
                      </IconButton>
                    </Tooltip>
                  )}
                  {photoToggle}
                </Stack>
              </Stack>
            )}
            {stageContent}
          </Paper>

          <Paper
            component="section"
            aria-label={heading || "Result"}
            sx={{ p: { xs: 2, md: 2.5 }, position: { md: "sticky" }, top: { md: 88 }, alignSelf: "start" }}
          >
            <Stack spacing={2} useFlexGap>
              {resultSummary ?? (
                <Box sx={{ order: isShort ? -2 : 0 }}>
                  <Typography variant="h6" component="h2">
                    {heading}
                  </Typography>
                  <Typography color="text.secondary" mt={0.5}>
                    {body}
                  </Typography>
                </Box>
              )}

              {phase === "outline" && (
                <Stack spacing={1}>
                  <Stepper label="Rows" value={rows} min={MIN_SIDE} max={MAX_SIDE} disabled={busy} onChange={setRows} />
                  <Stepper label="Columns" value={cols} min={MIN_SIDE} max={MAX_SIDE} disabled={busy} onChange={setCols} />
                </Stack>
              )}

              {!isPhone && cellPalette}

              {phase === "review" && (
                <>
                  <PairList pairs={pairs} colorOf={colorOf} labelOf={labelOf} />
                  {incomplete.length > 0 && (
                    <Alert severity="warning">
                      {incomplete.length === 1 ? "One color doesn't" : `${incomplete.length} colors don't`} have exactly two
                      dots. Tap a circled cell to fix it.
                    </Alert>
                  )}
                  {!incomplete.length && parsed.error && pairs.length > 0 && <Alert severity="warning">{parsed.error}</Alert>}
                  {!pixels && board && (
                    <Stack spacing={1}>
                      <Stepper label="Rows" value={board.height} min={MIN_SIDE} max={MAX_SIDE} disabled={busy}
                        onChange={(value) => { setSelected(null); editBoard(resizeBoard(board, value, board.width)); }} />
                      <Stepper label="Columns" value={board.width} min={MIN_SIDE} max={MAX_SIDE} disabled={busy}
                        onChange={(value) => { setSelected(null); editBoard(resizeBoard(board, board.height, value)); }} />
                    </Stack>
                  )}
                </>
              )}

              {!isPhone && primary && (
                <Box sx={{ order: isShort ? -1 : 0 }}>
                  <Button
                    variant="contained"
                    size="large"
                    fullWidth
                    startIcon={primary.icon}
                    disabled={primary.disabled}
                    onClick={primary.onClick}
                  >
                    {primary.label}
                  </Button>
                  {phase === "review" && solveBlocker && !busy && (
                    <Typography variant="body2" color="text.secondary" mt={1} textAlign="center">
                      {solveBlocker}
                    </Typography>
                  )}
                </Box>
              )}
              {isPhone && phase === "review" && solveBlocker && !busy && (
                <Typography variant="body2" color="text.secondary">
                  {solveBlocker}
                </Typography>
              )}
              {panelSecondary.length > 0 && (
                <Stack direction="row" flexWrap="wrap" useFlexGap gap={1}>
                  {panelSecondary.map((action) => (
                    <Button
                      key={action.label}
                      color="inherit"
                      variant="outlined"
                      startIcon={action.icon}
                      disabled={busy || action.disabled}
                      onClick={action.onClick}
                      sx={{ minHeight: 44, flex: { md: "1 1 auto" } }}
                    >
                      {action.label}
                    </Button>
                  ))}
                </Stack>
              )}
            </Stack>
          </Paper>
        </Box>
      )}

      {isPhone && phase !== "empty" && (
        <ActionBar primary={primary} secondary={secondary[0] && { ...secondary[0], disabled: busy || secondary[0].disabled }}>
          {cellPalette || undefined}
        </ActionBar>
      )}

      <input
        ref={importRef}
        aria-label="Open .flow file"
        type="file"
        accept=".flow,.txt"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void importFlow(file);
        }}
      />

      <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)} onClick={() => setMenuAnchor(null)}>
        <MenuItem onClick={() => setDialog("library")}>
          <ListItemIcon><FolderOpenOutlined fontSize="small" /></ListItemIcon>
          <ListItemText primary="Saved on this device" secondary={`${saved.length} puzzles · ${reports.length} reports`} />
        </MenuItem>
        <MenuItem disabled={busy} onClick={() => importRef.current?.click()}>
          <ListItemIcon><UploadFileOutlined fontSize="small" /></ListItemIcon>
          <ListItemText>Open .flow file</ListItemText>
        </MenuItem>
        <MenuItem disabled={busy || !parsed.puzzle} onClick={() => void savePuzzle()}>
          <ListItemIcon><SaveOutlined fontSize="small" /></ListItemIcon>
          <ListItemText>Save in this browser</ListItemText>
        </MenuItem>
        <MenuItem disabled={!board} onClick={() => download("local-puzzle.flow", text, "text/plain")}>
          <ListItemIcon><Download fontSize="small" /></ListItemIcon>
          <ListItemText>Export .flow file</ListItemText>
        </MenuItem>
        <MenuItem disabled={busy} onClick={() => setDialog("text")}>
          <ListItemIcon><NotesOutlined fontSize="small" /></ListItemIcon>
          <ListItemText>Edit as text</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => setDialog("settings")}>
          <ListItemIcon><TuneOutlined fontSize="small" /></ListItemIcon>
          <ListItemText>Solver settings</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem disabled={busy} onClick={() => setDialog("feedback")}>
          <ListItemIcon><BugReportOutlined fontSize="small" /></ListItemIcon>
          <ListItemText>Report a problem</ListItemText>
        </MenuItem>
      </Menu>

      <TextDialog
        open={dialog === "text"}
        text={text || writeBoard(blankBoard(5, 5))}
        onClose={() => setDialog(null)}
        onApply={(value) => {
          setDialog(null);
          setSelected(null);
          changeText(value);
          setPhase("review");
        }}
      />
      <SettingsDialog
        open={dialog === "settings"}
        timeoutSeconds={timeoutSeconds}
        fill={board ? board.fill : null}
        onClose={() => setDialog(null)}
        onTimeoutChange={setTimeoutSeconds}
        onFillChange={(fill) => board && editBoard({ ...board, fill })}
      />
      <LibraryDialog
        open={dialog === "library"}
        puzzles={saved}
        reports={reports}
        onClose={() => { setDialog(null); onLibraryClose?.(); }}
        onOpenPuzzle={(puzzle) => {
          setDialog(null);
          loadText(puzzle.text, puzzle.colors);
          onLibraryClose?.();
        }}
        onExportPuzzle={(puzzle) => download("local-puzzle.flow", puzzle.text, "text/plain")}
        onDeletePuzzle={async (puzzle) => {
          try {
            await deleteLocalPuzzle(puzzle.id);
            setSaved(await listLocalPuzzles());
          } catch (reason) {
            setError((reason as Error).message);
          }
        }}
        onDownloadReport={(report) => download(`flow-report-${report.id}.json`, JSON.stringify(report, null, 2))}
        onDeleteReport={async (report) => {
          try {
            await deleteLocalReport(report.id);
            setReports(await listLocalReports());
          } catch (reason) {
            setError((reason as Error).message);
          }
        }}
      />
      <FeedbackDialog
        open={dialog === "feedback"}
        canAttachImage={Boolean(pixels)}
        onClose={() => setDialog(null)}
        buildReport={buildReport}
        onDownload={(report) => download(`flow-report-${report.id}.json`, JSON.stringify(report, null, 2))}
        onSave={async (report) => {
          await saveLocalReport(report);
          setReports(await listLocalReports());
          setDialog(null);
          setNotice("Report saved in this browser. Nothing has been sent.");
        }}
      />

      <Snackbar
        open={Boolean(notice)}
        autoHideDuration={5000}
        onClose={() => setNotice("")}
        message={notice}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        sx={{ bottom: isPhone ? "calc(140px + env(safe-area-inset-bottom)) !important" : undefined }}
      />
    </Box>
  );
}
