import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { DeleteOutline, Download } from "@mui/icons-material";
import { GameView } from "../GameView";
import { editorGraph, readBoard } from "../../core/localBoardEdit";
import { parseLocalPuzzle } from "../../core/localPuzzle";
import type { FeedbackReport, SavedLocalPuzzle } from "../../storage/localStore";

function useFullScreenDialog() {
  const theme = useTheme();
  return useMediaQuery(theme.breakpoints.down("sm"));
}

type TextDialogProps = { open: boolean; text: string; onClose: () => void; onApply: (text: string) => void };

export function TextDialog({ open, text, onClose, onApply }: TextDialogProps) {
  const fullScreen = useFullScreenDialog();
  const [draft, setDraft] = useState(text);
  useEffect(() => {
    if (open) setDraft(text);
  }, [open, text]);
  const problem = useMemo(() => {
    if (readBoard(draft)) return "";
    try {
      parseLocalPuzzle(draft);
      return "This text can't be shown as a square board.";
    } catch (reason) {
      return (reason as Error).message;
    }
  }, [draft]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" fullScreen={fullScreen}>
      <DialogTitle>Edit as text</DialogTitle>
      <DialogContent>
        <Stack spacing={2} pt={1}>
          <Typography color="text.secondary">
            One line per row. A–Z are dots (two per color), <code>.</code> is an empty cell and <code>#</code> is a hole.
          </Typography>
          <TextField
            label="Puzzle"
            value={draft}
            multiline
            minRows={8}
            maxRows={24}
            onChange={(event) => setDraft(event.target.value)}
            inputProps={{ style: { fontFamily: "ui-monospace, Consolas, monospace" }, maxLength: 10000, spellCheck: false }}
          />
          {problem && <Alert severity="warning">{problem}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="contained" disabled={Boolean(problem)} onClick={() => onApply(draft)}>
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
}

type SettingsDialogProps = {
  open: boolean;
  timeoutSeconds: number;
  fill: boolean | null;
  onClose: () => void;
  onTimeoutChange: (seconds: number) => void;
  onFillChange: (fill: boolean) => void;
};

export function SettingsDialog({ open, timeoutSeconds, fill, onClose, onTimeoutChange, onFillChange }: SettingsDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Solver settings</DialogTitle>
      <DialogContent>
        <Stack spacing={3} pt={1}>
          <Box>
            <Typography gutterBottom>Time limit</Typography>
            <ToggleButtonGroup
              exclusive
              fullWidth
              value={timeoutSeconds}
              onChange={(_, value: number | null) => value && onTimeoutChange(value)}
              aria-label="Time limit"
            >
              {[10, 30, 60].map((seconds) => (
                <ToggleButton key={seconds} value={seconds} sx={{ minHeight: 44 }}>
                  {seconds} s
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <Typography variant="body2" color="text.secondary" mt={1}>
              Most boards solve in about a second. Large boards may need longer.
            </Typography>
          </Box>
          <FormControlLabel
            control={<Switch checked={fill ?? true} disabled={fill === null} onChange={(event) => onFillChange(event.target.checked)} />}
            label="Every cell must be filled"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  );
}

type LibraryDialogProps = {
  open: boolean;
  puzzles: SavedLocalPuzzle[];
  reports: FeedbackReport[];
  onClose: () => void;
  onOpenPuzzle: (puzzle: SavedLocalPuzzle) => void;
  onExportPuzzle: (puzzle: SavedLocalPuzzle) => void;
  onDeletePuzzle: (puzzle: SavedLocalPuzzle) => void;
  onDownloadReport: (report: FeedbackReport) => void;
  onDeleteReport: (report: FeedbackReport) => void;
};

function PuzzleThumbnail({ puzzle }: { puzzle: SavedLocalPuzzle }) {
  const graph = useMemo(() => {
    const board = readBoard(puzzle.text);
    return board ? editorGraph(board, puzzle.colors) : null;
  }, [puzzle]);
  return (
    <Box sx={{ width: 72, flexShrink: 0 }}>
      {graph ? <GameView graph={graph} compact height={72} /> : null}
    </Box>
  );
}

export function LibraryDialog(props: LibraryDialogProps) {
  const { open, puzzles, reports, onClose } = props;
  const fullScreen = useFullScreenDialog();
  const [tab, setTab] = useState<"puzzles" | "reports">("puzzles");

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" fullScreen={fullScreen}>
      <DialogTitle sx={{ pb: 0 }}>Saved on this device</DialogTitle>
      <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ px: 2 }}>
        <Tab value="puzzles" label={`Puzzles (${puzzles.length})`} />
        <Tab value="reports" label={`Problem reports (${reports.length})`} />
      </Tabs>
      <DialogContent dividers>
        {tab === "puzzles" && (
          <Stack spacing={1}>
            <Typography variant="body2" color="text.secondary">
              Kept in this browser until you delete them or clear site data (up to 100). Export anything you want to keep.
            </Typography>
            {puzzles.length === 0 && <Typography py={2}>No saved puzzles yet.</Typography>}
            {puzzles.map((puzzle) => (
              <Stack
                key={puzzle.id}
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ p: 1, borderRadius: 2, "&:hover": { bgcolor: "rgba(255,255,255,0.04)" } }}
              >
                <PuzzleThumbnail puzzle={puzzle} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography noWrap fontWeight={650}>
                    {puzzle.title}
                  </Typography>
                  <Button size="small" onClick={() => props.onOpenPuzzle(puzzle)} sx={{ px: 0, minHeight: 36 }}>
                    Open
                  </Button>
                </Box>
                <IconButton aria-label={`Export ${puzzle.title}`} onClick={() => props.onExportPuzzle(puzzle)}>
                  <Download />
                </IconButton>
                <IconButton aria-label={`Delete ${puzzle.title}`} onClick={() => props.onDeletePuzzle(puzzle)}>
                  <DeleteOutline />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}
        {tab === "reports" && (
          <Stack spacing={1}>
            <Typography variant="body2" color="text.secondary">
              Up to 50 reports and 5 MB. Reports older than 30 days are removed; the oldest go first when a limit is
              reached. Nothing is sent anywhere.
            </Typography>
            {reports.length === 0 && <Typography py={2}>No reports saved.</Typography>}
            {reports.map((report) => (
              <Stack key={report.id} direction="row" alignItems="center" spacing={1}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography noWrap>{report.description}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {new Date(report.createdAt).toLocaleString()} · {report.outcome}
                  </Typography>
                </Box>
                <IconButton aria-label="Download report" onClick={() => props.onDownloadReport(report)}>
                  <Download />
                </IconButton>
                <IconButton aria-label="Delete report" onClick={() => props.onDeleteReport(report)}>
                  <DeleteOutline />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

type FeedbackDialogProps = {
  open: boolean;
  canAttachImage: boolean;
  onClose: () => void;
  buildReport: (description: string, includeImage: boolean) => Promise<FeedbackReport>;
  onSave: (report: FeedbackReport) => Promise<void>;
  onDownload: (report: FeedbackReport) => void;
};

export function FeedbackDialog({ open, canAttachImage, onClose, buildReport, onSave, onDownload }: FeedbackDialogProps) {
  const fullScreen = useFullScreenDialog();
  const [description, setDescription] = useState("");
  const [includeImage, setIncludeImage] = useState(false);
  const [report, setReport] = useState<FeedbackReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDescription("");
    setIncludeImage(false);
    setReport(null);
    setError("");
  }, [open]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={() => !busy && onClose()} fullWidth maxWidth="sm" fullScreen={fullScreen}>
      <DialogTitle>Report a problem</DialogTitle>
      <DialogContent>
        <Stack spacing={2} pt={1}>
          <Typography color="text.secondary">
            Nothing is sent. Preview the report, then download it to share or save it in this browser.
          </Typography>
          <TextField
            label="What went wrong?"
            multiline
            minRows={3}
            value={description}
            disabled={busy}
            inputProps={{ maxLength: 2000 }}
            onChange={(event) => {
              setDescription(event.target.value);
              setReport(null);
            }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={includeImage}
                disabled={!canAttachImage || busy}
                onChange={(event) => {
                  setIncludeImage(event.target.checked);
                  setReport(null);
                }}
              />
            }
            label="Include a resized copy of the image (may show the whole photo)"
          />
          {error && <Alert severity="error">{error}</Alert>}
          {report && (
            <>
              <Typography variant="subtitle2">Exactly what the report contains</Typography>
              <Box
                component="pre"
                sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 250, overflow: "auto", fontSize: 12, m: 0 }}
              >
                {JSON.stringify(
                  {
                    ...report,
                    attachment: report.attachment
                      ? { mime: report.attachment.mime, dataUrl: "Image shown below; encoded in the downloaded report." }
                      : undefined
                  },
                  null,
                  2
                )}
              </Box>
              {report.attachment && (
                <Box
                  component="img"
                  src={report.attachment.dataUrl}
                  alt="Image included in the report"
                  sx={{ maxHeight: 320, maxWidth: "100%", objectFit: "contain" }}
                />
              )}
              <Typography variant="body2" color="text.secondary">
                {Math.ceil(new TextEncoder().encode(JSON.stringify(report)).length / 1024)} KB total. Device and location
                metadata are removed from the image.
              </Typography>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ flexWrap: "wrap", gap: 1 }}>
        <Button color="inherit" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        {!report ? (
          <Button
            variant="contained"
            disabled={busy || !description.trim()}
            onClick={() => void run(async () => setReport(await buildReport(description, includeImage)))}
          >
            Preview report
          </Button>
        ) : (
          <>
            <Button disabled={busy} startIcon={<Download />} onClick={() => onDownload(report)}>
              Download
            </Button>
            <Button variant="contained" disabled={busy} onClick={() => void run(() => onSave(report))}>
              Save in this browser
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}
