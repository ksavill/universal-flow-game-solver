import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField
} from "@mui/material";
import { Flag, FlagOutlined } from "@mui/icons-material";
import {
  flagImageImport,
  getImageImport,
  ImageImportEntry,
  unflagImageImport
} from "../api";

type FlagReviewControlProps = {
  importId: string;
  initialFlagged?: boolean;
  initialReason?: string | null;
  onChanged?: (entry: ImageImportEntry) => void;
  fullWidth?: boolean;
  size?: "small" | "medium" | "large";
};

export function FlagReviewControl({
  importId,
  initialFlagged,
  initialReason,
  onChanged,
  fullWidth = false,
  size = "small"
}: FlagReviewControlProps) {
  const [flagged, setFlagged] = useState(initialFlagged ?? false);
  const [reason, setReason] = useState(initialReason ?? "");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFlagged(initialFlagged ?? false);
    setReason(initialReason ?? "");
    setError(null);
    if (initialFlagged !== undefined) {
      return;
    }
    let active = true;
    void getImageImport(importId)
      .then((record) => {
        if (!active) return;
        setFlagged(record.flagged);
        setReason(record.flag_reason ?? "");
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Could not load review status.");
      });
    return () => {
      active = false;
    };
  }, [importId, initialFlagged, initialReason]);

  async function handleUnflag() {
    setBusy(true);
    setError(null);
    try {
      const entry = await unflagImageImport(importId);
      setFlagged(false);
      setReason("");
      onChanged?.(entry);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the review flag.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFlag() {
    setBusy(true);
    setError(null);
    try {
      const entry = await flagImageImport(importId, reason);
      setFlagged(true);
      setReason(entry.flag_reason ?? "");
      setDialogOpen(false);
      onChanged?.(entry);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not flag this result.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        size={size}
        fullWidth={fullWidth}
        color={flagged ? "warning" : "inherit"}
        variant={flagged ? "contained" : "outlined"}
        startIcon={
          busy ? (
            <CircularProgress size={16} color="inherit" />
          ) : flagged ? (
            <Flag fontSize="small" />
          ) : (
            <FlagOutlined fontSize="small" />
          )
        }
        onClick={() => {
          if (flagged) {
            void handleUnflag();
          } else {
            setDialogOpen(true);
          }
        }}
        disabled={busy}
      >
        {flagged ? "Unflag" : "Flag for review"}
      </Button>

      <Dialog
        open={dialogOpen}
        onClose={() => !busy && setDialogOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Flag this result for review</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Add a concise note so the next person or agent knows what needs attention.
          </DialogContentText>
          <TextField
            autoFocus
            fullWidth
            multiline
            minRows={3}
            label="Review note (optional)"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            inputProps={{ maxLength: 1000 }}
          />
          {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="warning"
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <Flag />}
            onClick={() => void handleFlag()}
            disabled={busy}
          >
            Flag for review
          </Button>
        </DialogActions>
      </Dialog>

      {error && !dialogOpen && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
    </>
  );
}
