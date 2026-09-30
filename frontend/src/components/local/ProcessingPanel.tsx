import { useEffect, useState } from "react";
import { Box, Button, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { Stop } from "@mui/icons-material";
import { STAGE_LABELS, type ProcessingTrace } from "../../processing/localProgress";

const seconds = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
const bytes = (value: number) => `${(value / 1024 / 1024).toFixed(2)} MiB`;
const metricLabels: Record<string, string> = {
  inputBytes: "Image size", downloadBytes: "Solver bytes received", runtimeBytes: "Unpacked solver size",
  endpoints: "Dots found", pairs: "Color pairs", cellsExamined: "Cells examined", activeCells: "Playable cells",
  constraints: "Constraints", checks: "Search checks started", completedChecks: "Search checks completed", cuts: "Disconnected loops excluded"
};

export function ProcessingPanel({ trace, onStop }: { trace: ProcessingTrace; onStop: () => void }) {
  const [clock, setClock] = useState(() => performance.now());
  const running = trace.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setClock(performance.now()), 200);
    return () => clearInterval(timer);
  }, [running]);
  const now = trace.endedAt ?? Math.max(clock, trace.startedAt, trace.stages.at(-1)!.startedAt);
  const { current, metrics } = trace;
  const measured = current.completed !== undefined && current.total !== undefined && current.total > 0;
  const percent = measured ? Math.min(100, Math.max(0, current.completed! / current.total! * 100)) : undefined;
  const budgetStart = trace.stages.find(entry => entry.stage === "building")?.startedAt;
  const title = running ? STAGE_LABELS[current.stage] : trace.status === "stopped" ? "Processing stopped" : trace.status === "error" ? "Processing failed" : "Processing finished";
  const timings = new Map<string, number>();
  trace.stages.forEach((entry, index) => {
    const elapsed = index === trace.stages.length - 1 && running ? now - entry.startedAt : entry.elapsedMs;
    timings.set(entry.stage, (timings.get(entry.stage) ?? 0) + elapsed);
  });
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }} aria-label="Processing progress">
      <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
        <Box sx={{ minWidth: 0 }}>
          <Typography fontWeight={600} role="status">{title}</Typography>
          <Typography variant="body2" color="text.secondary">{seconds(now - trace.startedAt)} elapsed{percent !== undefined && running ? ` · ${Math.floor(percent)}% of this stage` : ""}</Typography>
        </Box>
        {running && <Button variant="outlined" color="error" startIcon={<Stop />} onClick={onStop} sx={{ minHeight: 44, flexShrink: 0 }}>Stop</Button>}
      </Stack>
      {running && <LinearProgress aria-label={STAGE_LABELS[current.stage]} variant={measured ? "determinate" : "indeterminate"} value={percent} sx={{ my: 1.5, borderRadius: 1 }} />}
      {running && current.unit === "bytes" && measured && <Typography variant="body2" color="text.secondary">{bytes(current.completed!)} / {bytes(current.total!)} received (may come from your browser cache).</Typography>}
      {running && current.stage === "solving" && <Typography variant="body2" color="text.secondary">Searching · {metrics.completedChecks ?? 0} checks completed · {metrics.cuts ?? 0} loops excluded. Search progress has no reliable percentage.</Typography>}
      <Box component="details" sx={{ mt: 1, fontSize: "0.875rem", "& summary": { cursor: "pointer", minHeight: 32, alignContent: "center" } }}>
        <summary>Processing details</summary>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, columnGap: 5, rowGap: 2, mt: 1 }}>
        <Box>
        <Typography variant="body2" fontWeight={600}>Operation statistics</Typography>
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 0.75, m: 0, mt: 1, "& dd": { m: 0, textAlign: "right", fontVariantNumeric: "tabular-nums" } }}>
          {metrics.originalWidth !== undefined && <><dt>Original image</dt><dd>{metrics.originalWidth} × {metrics.originalHeight}</dd></>}
          {metrics.imageWidth !== undefined && <><dt>Working image</dt><dd>{metrics.imageWidth} × {metrics.imageHeight}</dd></>}
          {metrics.rows !== undefined && <><dt>Board</dt><dd>{metrics.rows} × {metrics.cols}</dd></>}
          {Object.entries(metricLabels).filter(([key]) => metrics[key] !== undefined).map(([key, label]) => <Box key={key} sx={{ display: "contents" }}><dt>{label}</dt><dd>{/Bytes$/.test(key) ? bytes(metrics[key]) : metrics[key].toLocaleString()}</dd></Box>)}
          {metrics.timeLimitMs !== undefined && <><dt>Search time limit</dt><dd>{seconds(metrics.timeLimitMs)}</dd></>}
          {running && budgetStart !== undefined && metrics.timeLimitMs !== undefined && <><dt>Search budget remaining (not an ETA)</dt><dd>{seconds(metrics.timeLimitMs - (now - budgetStart))}</dd></>}
        </Box>
        </Box>
        <Box>
        <Typography variant="body2" fontWeight={600} sx={{ mb: 1 }}>Time by stage</Typography>
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 0.5, m: 0, "& dd": { m: 0 } }}>
          {[...timings].map(([stage, elapsed]) => <Box key={stage} sx={{ display: "contents" }}><dt>{STAGE_LABELS[stage as keyof typeof STAGE_LABELS]}</dt><dd>{seconds(elapsed)}</dd></Box>)}
        </Box>
        </Box>
        </Box>
        {!!Object.keys(trace.engine).length && <Box component="details" sx={{ mt: 1 }}>
          <summary>Solver statistics from the last completed check</summary>
          <Typography variant="caption" color="text.secondary">Snapshots update between checks. These are solver counters, not device CPU or memory measurements.</Typography>
          <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 0.5, m: 0, "& dt": { overflowWrap: "anywhere" }, "& dd": { m: 0, pl: 1 } }}>
            {Object.entries(trace.engine).map(([key, value]) => <Box key={key} sx={{ display: "contents" }}><dt>{key}</dt><dd>{value.toLocaleString()}</dd></Box>)}
          </Box>
        </Box>}
      </Box>
    </Paper>
  );
}
