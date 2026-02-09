import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  Divider,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  CropTemplate,
  imageClassify,
  imageDetectGrid,
  imageGenerate,
  imageOcr,
  LevelType,
  listCropTemplates,
  listPuzzles,
  savePuzzle
} from "../api";

type BatchStatus = "pending" | "processing" | "ready" | "error";
type SaveStatus = "idle" | "saving" | "saved" | "error";

type BatchItem = {
  id: string;
  file: File;
  status: BatchStatus;
  message?: string;
  ocrText?: string;
  suggestedName?: string | null;
  name?: string;
  text?: string;
  metadata?: Record<string, string>;
  detection?: Record<string, unknown>;
  levelType?: LevelType | null;
  kind?: string;
  sizeLabel?: string;
  selected: boolean;
  saveStatus: SaveStatus;
  saveMessage?: string;
};

type ExistingPuzzle = {
  name: string;
  kind: string;
  sizeLabel: string;
  source: string;
  meta: Record<string, string>;
};

type DuplicateInfo = {
  conflicts: string[];
  warnings: string[];
};

const DEFAULT_METADATA = {
  source: "image-import"
};

const buildItemId = (file: File, idx: number) => `${file.name}-${file.size}-${file.lastModified}-${idx}`;

const normalize = (value: string | null | undefined) => value?.trim().toLowerCase() ?? "";

function inferSignature(text: string | undefined): { kind?: string; sizeLabel?: string } {
  if (!text) {
    return {};
  }
  const raw = text.trim();
  if (!raw) {
    return {};
  }
  if (raw.startsWith("{")) {
    try {
      const obj = JSON.parse(raw) as { space?: { type?: string; topology?: string; nodes?: Record<string, unknown> } };
      const graphTopology = obj?.space?.topology?.toLowerCase();
      const kind =
        obj?.space?.type === "graph" && graphTopology && ["cube", "star", "figure8"].includes(graphTopology)
          ? graphTopology
          : (obj?.space?.type ?? "graph");
      const nodes = obj?.space?.nodes ? Object.keys(obj.space.nodes).length : 0;
      const sizeLabel = nodes ? `${nodes} nodes` : "graph";
      return { kind, sizeLabel };
    } catch {
      return {};
    }
  }

  const lines = text.split(/\r?\n/);
  let kind = "square";
  const gridLines: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    if (trimmed.startsWith("#")) {
      const header = trimmed.slice(1).trim();
      if (header.includes(":")) {
        const [key, value] = header.split(":", 2).map((part) => part.trim());
        if (key.toLowerCase() === "type" && value) {
          kind = value.toLowerCase();
        }
        continue;
      }
      if (trimmed.length >= 2 && /\s/.test(trimmed[1])) {
        continue;
      }
    }
    gridLines.push(line);
  }

  if (!gridLines.length) {
    return { kind };
  }

  const tokenRows = gridLines.map((row) =>
    row.trim().includes(" ") ? row.trim().split(/\s+/).filter(Boolean) : row.trim().split("")
  );
  const rows = tokenRows.length;
  const cols = Math.max(...tokenRows.map((row) => row.length));
  let sizeLabel = "";
  if (kind === "circle") {
    sizeLabel = `${rows}x${cols}`;
  } else if (kind === "square" || kind === "hex") {
    sizeLabel = `${cols}x${rows}`;
  } else {
    sizeLabel = `${cols}x${rows}`;
  }
  return { kind, sizeLabel };
}

async function getImageDimensions(file: File): Promise<{ width: number; height: number }> {
  if ("createImageBitmap" in window) {
    const bitmap = await createImageBitmap(file);
    const dims = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return dims;
  }
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Failed to load image."));
    };
    img.src = url;
  });
}

function cropFromTemplate(tmpl: CropTemplate, dims: { width: number; height: number }) {
  return {
    x: Math.round(tmpl.crop_pct.x * dims.width),
    y: Math.round(tmpl.crop_pct.y * dims.height),
    width: Math.round(tmpl.crop_pct.width * dims.width),
    height: Math.round(tmpl.crop_pct.height * dims.height)
  };
}

function computeDuplicates(items: BatchItem[], existing: ExistingPuzzle[]): Map<string, DuplicateInfo> {
  const byConflictKey = new Map<string, ExistingPuzzle[]>();
  const byName = new Map<string, ExistingPuzzle[]>();
  const bySource = new Map<string, ExistingPuzzle[]>();
  const byTitle = new Map<string, ExistingPuzzle[]>();

  const conflictKey = (nameKey: string, kind: string, size: string) =>
    `${nameKey}|${normalize(kind)}|${normalize(size)}`;

  existing.forEach((entry) => {
    const nameKey = normalize(entry.name);
    if (nameKey) {
      const list = byName.get(nameKey) ?? [];
      list.push(entry);
      byName.set(nameKey, list);
    }
    if (entry.source === "user" && nameKey && entry.kind && entry.sizeLabel) {
      const key = conflictKey(nameKey, entry.kind, entry.sizeLabel);
      const list = byConflictKey.get(key) ?? [];
      list.push(entry);
      byConflictKey.set(key, list);
    }
    const sourceKey = normalize(entry.meta?.source_image);
    if (sourceKey) {
      const list = bySource.get(sourceKey) ?? [];
      list.push(entry);
      bySource.set(sourceKey, list);
    }
    const titleKey = normalize(entry.meta?.title);
    if (titleKey) {
      const list = byTitle.get(titleKey) ?? [];
      list.push(entry);
      byTitle.set(titleKey, list);
    }
  });

  const batchNameCounts = new Map<string, number>();
  const batchConflictCounts = new Map<string, number>();
  const batchSourceCounts = new Map<string, number>();
  const batchTitleCounts = new Map<string, number>();

  items.forEach((item) => {
    const nameKey = normalize(item.name);
    if (nameKey) {
      batchNameCounts.set(nameKey, (batchNameCounts.get(nameKey) ?? 0) + 1);
    }
    if (nameKey && item.kind && item.sizeLabel) {
      const key = conflictKey(nameKey, item.kind, item.sizeLabel);
      batchConflictCounts.set(key, (batchConflictCounts.get(key) ?? 0) + 1);
    }
    const sourceKey = normalize(item.metadata?.source_image);
    if (sourceKey) {
      batchSourceCounts.set(sourceKey, (batchSourceCounts.get(sourceKey) ?? 0) + 1);
    }
    const titleKey = normalize(item.metadata?.title);
    if (titleKey) {
      batchTitleCounts.set(titleKey, (batchTitleCounts.get(titleKey) ?? 0) + 1);
    }
  });

  const out = new Map<string, DuplicateInfo>();
  items.forEach((item) => {
    const conflicts: string[] = [];
    const warnings: string[] = [];
    const nameKey = normalize(item.name);
    if (nameKey && item.kind && item.sizeLabel) {
      const key = conflictKey(nameKey, item.kind, item.sizeLabel);
      if (byConflictKey.has(key)) {
        conflicts.push("name already exists for this size in user library");
      }
      if ((batchConflictCounts.get(key) ?? 0) > 1) {
        conflicts.push("name repeats for this size in batch");
      }
    }
    if (nameKey && byName.has(nameKey) && conflicts.length === 0) {
      warnings.push("name exists in library");
    } else if (nameKey && (batchNameCounts.get(nameKey) ?? 0) > 1 && conflicts.length === 0) {
      warnings.push("name repeats in batch (different size)");
    }
    const sourceKey = normalize(item.metadata?.source_image);
    if (sourceKey && bySource.has(sourceKey)) {
      warnings.push("source_image exists in library");
    }
    if (sourceKey && (batchSourceCounts.get(sourceKey) ?? 0) > 1) {
      warnings.push("source_image repeats in batch");
    }
    const titleKey = normalize(item.metadata?.title);
    if (titleKey && byTitle.has(titleKey)) {
      warnings.push("title exists in library");
    }
    if (titleKey && (batchTitleCounts.get(titleKey) ?? 0) > 1) {
      warnings.push("title repeats in batch");
    }
    if (conflicts.length || warnings.length) {
      out.set(item.id, { conflicts, warnings });
    }
  });
  return out;
}

export function BulkImportView() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [items, setItems] = useState<BatchItem[]>([]);
  const [templates, setTemplates] = useState<CropTemplate[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [templateNote, setTemplateNote] = useState("");
  const [pipelineUseClassifier, setPipelineUseClassifier] = useState(true);
  const [pipelineUseOcr, setPipelineUseOcr] = useState(true);
  const [pipelineUseGrid, setPipelineUseGrid] = useState(true);
  const [pipelineUseTerminals, setPipelineUseTerminals] = useState(true);
  const [ocrWholeImage, setOcrWholeImage] = useState(true);
  const [targetType, setTargetType] = useState<
    "auto" | "square" | "hex" | "circle" | "graph" | "cube" | "star" | "figure8"
  >("auto");
  const [gridWidth, setGridWidth] = useState(10);
  const [gridHeight, setGridHeight] = useState(10);
  const [graphLayout, setGraphLayout] = useState<"grid" | "line">("grid");
  const [graphNodes, setGraphNodes] = useState(10);
  const [threshold, setThreshold] = useState(230);
  const [lineThreshold, setLineThreshold] = useState(0.6);
  const [invert, setInvert] = useState(false);
  const [perspective, setPerspective] = useState(false);
  const [satThreshold, setSatThreshold] = useState(30);
  const [brightnessMin, setBrightnessMin] = useState(30);
  const [brightnessMax, setBrightnessMax] = useState(230);
  const [marginRatio, setMarginRatio] = useState(0.15);
  const [clusterThreshold, setClusterThreshold] = useState(60);
  const [bgThreshold, setBgThreshold] = useState(40);
  const [runBusy, setRunBusy] = useState(false);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [existingPuzzles, setExistingPuzzles] = useState<ExistingPuzzle[]>([]);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));

  const duplicates = useMemo(() => computeDuplicates(items, existingPuzzles), [items, existingPuzzles]);

  const updateItem = useCallback((id: string, updater: (item: BatchItem) => BatchItem) => {
    setItems((prev) => prev.map((item) => (item.id === id ? updater(item) : item)));
  }, []);

  useEffect(() => {
    async function loadTemplates() {
      try {
        const data = await listCropTemplates();
        setTemplates(data);
        if (data.length && !templateId) {
          setTemplateId(data[0].id);
        }
      } catch {
        // ignore
      }
    }
    loadTemplates();
  }, [templateId]);

  useEffect(() => {
    async function loadExisting() {
      try {
        const entries = await listPuzzles();
        setExistingPuzzles(
          entries.map((entry) => ({
            name: entry.name,
            meta: entry.meta ?? {},
            kind: entry.kind ?? "unknown",
            sizeLabel: entry.size_label ?? "-",
            source: entry.source ?? "unknown"
          }))
        );
      } catch {
        // ignore
      }
    }
    loadExisting();
  }, []);

  useEffect(() => {
    const tmpl = templates.find((t) => t.id === templateId);
    if (!tmpl) {
      setTemplateNote("");
      return;
    }
    setTemplateNote(tmpl.note ?? "");
    if (tmpl.pipeline) {
      if (typeof tmpl.pipeline.classifier === "boolean") {
        setPipelineUseClassifier(tmpl.pipeline.classifier);
      }
      if (typeof tmpl.pipeline.ocr === "boolean") {
        setPipelineUseOcr(tmpl.pipeline.ocr);
      }
      if (typeof tmpl.pipeline.grid === "boolean") {
        setPipelineUseGrid(tmpl.pipeline.grid);
      }
      if (typeof tmpl.pipeline.terminals === "boolean") {
        setPipelineUseTerminals(tmpl.pipeline.terminals);
      }
      if (typeof tmpl.pipeline.ocr_full === "boolean") {
        setOcrWholeImage(tmpl.pipeline.ocr_full);
      }
    }
  }, [templateId, templates]);

  const handleFilesChange = (event: ChangeEvent<HTMLInputElement>) => {
    const fileList = Array.from(event.target.files ?? []);
    if (!fileList.length) {
      return;
    }
    setItems((prev) => {
      const base = [...prev];
      fileList.forEach((file, idx) => {
        base.push({
          id: buildItemId(file, prev.length + idx),
          file,
          status: "pending",
          selected: true,
          saveStatus: "idle"
        });
      });
      return base;
    });
    setRunStatus(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const clearItems = () => {
    setItems([]);
    setRunStatus(null);
  };

  const resetResults = () => {
    setItems((prev) =>
      prev.map((item) => ({
        ...item,
        status: "pending",
        message: undefined,
        ocrText: undefined,
        suggestedName: undefined,
        name: undefined,
        text: undefined,
        metadata: undefined,
        detection: undefined,
        levelType: undefined,
        selected: true,
        saveStatus: "idle",
        saveMessage: undefined
      }))
    );
    setRunStatus(null);
  };

  const runPipeline = async () => {
    if (!items.length) {
      setRunStatus("Add images before running the pipeline.");
      return;
    }
    const tmpl = templates.find((t) => t.id === templateId);
    setRunBusy(true);
    setRunStatus(`Processing ${items.length} image(s)...`);

    for (const item of items) {
      updateItem(item.id, (prev) => ({
        ...prev,
        status: "processing",
        message: undefined,
        saveStatus: "idle",
        saveMessage: undefined
      }));
      try {
        const dims = tmpl ? await getImageDimensions(item.file) : null;
        const crop = tmpl && dims ? cropFromTemplate(tmpl, dims) : null;

        let ocrText = "";
        let suggestedName: string | null = null;
        if (pipelineUseOcr) {
          const ocrRes = await imageOcr({
            file: item.file,
            crop: ocrWholeImage ? null : crop,
            perspective
          });
          ocrText = ocrRes.text || ocrRes.message || "";
          suggestedName = ocrRes.suggested_name ?? null;
        }

        let detectedLevelType: LevelType | null = null;
        if (pipelineUseClassifier || targetType === "auto") {
          const classifyRes = await imageClassify({
            file: item.file,
            threshold,
            lineThreshold,
            invert,
            perspective,
            levelHint: suggestedName ?? item.file.name,
            crop
          });
          detectedLevelType = classifyRes.level_type;
        }

        let rows: number | undefined;
        let cols: number | undefined;
        const targetForSizing =
          targetType === "auto" ? detectedLevelType?.recommended_target_type ?? "square" : targetType;
        const topologyTarget = targetForSizing === "cube" || targetForSizing === "star" || targetForSizing === "figure8";
        const needsGridDimensions = (targetForSizing === "graph" && graphLayout === "grid") || (!topologyTarget && targetForSizing !== "graph");
        if (needsGridDimensions) {
          if (pipelineUseGrid) {
            const gridRes = await imageDetectGrid({
              file: item.file,
              targetType: targetForSizing,
              threshold,
              lineThreshold,
              invert,
              perspective,
              crop
            });
            if (!gridRes.grid) {
              throw new Error(gridRes.message ?? "Grid detection failed.");
            }
            rows = gridRes.grid.rows;
            cols = gridRes.grid.cols;
          } else {
            if (gridWidth <= 0 || gridHeight <= 0) {
              throw new Error("Manual grid size required when grid detection is disabled.");
            }
            cols = gridWidth;
            rows = gridHeight;
          }
        }

        const shouldServerClassify = !detectedLevelType && targetType === "auto";
        const gen = await imageGenerate({
          file: item.file,
          targetType,
          gridWidth: cols ?? gridWidth,
          gridHeight: rows ?? gridHeight,
          graphLayout,
          graphNodes,
          autoTerminals: pipelineUseTerminals,
          autoClassify: shouldServerClassify,
          levelType: detectedLevelType ?? undefined,
          metadata: DEFAULT_METADATA,
          crop,
          threshold,
          lineThreshold,
          invert,
          perspective,
          satThreshold,
          brightnessMin,
          brightnessMax,
          marginRatio,
          clusterThreshold,
          bgThreshold
        });

        const name = (suggestedName || gen.name || "").trim();
        const warnings = Array.isArray(gen.detection?.warnings)
          ? (gen.detection?.warnings as string[]).join(" ")
          : "";
        const levelTypeForItem = (gen.detection?.level_type as LevelType | undefined) ?? detectedLevelType;
        const levelLabel = levelTypeForItem
          ? `${levelTypeForItem.geometry}${levelTypeForItem.modifiers.length ? `+${levelTypeForItem.modifiers.join("+")}` : ""}`
          : "";
        const targetUsed = String(gen.detection?.target_type_used ?? targetType);
        const gridLabel = rows && cols ? `grid ${cols}x${rows}` : "";
        const messageParts = [
          levelLabel ? `type ${levelLabel}` : "",
          targetUsed ? `target ${targetUsed}` : "",
          gridLabel,
          warnings
        ].filter((part) => Boolean(part));
        const message = messageParts.join(" | ");

        const signature = inferSignature(gen.text);

        updateItem(item.id, (prev) => ({
          ...prev,
          status: "ready",
          message: message || undefined,
          ocrText,
          suggestedName,
          name: name || prev.name,
          text: gen.text,
          metadata: gen.metadata,
          detection: gen.detection,
          levelType: levelTypeForItem,
          kind: signature.kind ?? targetUsed,
          sizeLabel: signature.sizeLabel,
          selected: true
        }));
      } catch (err) {
        updateItem(item.id, (prev) => ({
          ...prev,
          status: "error",
          message: err instanceof Error ? err.message : "Pipeline failed.",
          selected: false
        }));
      }
    }

    setRunBusy(false);
    setRunStatus("Pipeline complete.");
  };

  const selectAll = (value: boolean) => {
    setItems((prev) =>
      prev.map((item) =>
        item.status === "ready"
          ? {
              ...item,
              selected: value
            }
          : item
      )
    );
  };

  const handleBulkSave = async () => {
    const candidates = items.filter((item) => item.status === "ready" && item.selected);
    if (!candidates.length) {
      setRunStatus("Select at least one ready puzzle to save.");
      return;
    }
    setRunBusy(true);
    setRunStatus(`Saving ${candidates.length} puzzle(s)...`);

    for (const item of candidates) {
      updateItem(item.id, (prev) => ({
        ...prev,
        saveStatus: "saving",
        saveMessage: undefined
      }));
      try {
        if (!item.name || !item.text) {
          throw new Error("Missing generated puzzle.");
        }
        await savePuzzle({
          name: item.name,
          text: item.text,
          overwrite: false,
          metadata: item.metadata ?? {}
        });
        updateItem(item.id, (prev) => ({
          ...prev,
          saveStatus: "saved",
          saveMessage: "Saved."
        }));
        setExistingPuzzles((prev) => [
          ...prev,
          {
            name: item.name!,
            meta: item.metadata ?? {},
            kind: item.kind ?? "unknown",
            sizeLabel: item.sizeLabel ?? "-",
            source: "user"
          }
        ]);
      } catch (err) {
        updateItem(item.id, (prev) => ({
          ...prev,
          saveStatus: "error",
          saveMessage: err instanceof Error ? err.message : "Save failed."
        }));
      }
    }

    setRunBusy(false);
    setRunStatus("Bulk save complete.");
  };

  const selectedCount = items.filter((item) => item.selected && item.status === "ready").length;
  const readyCount = items.filter((item) => item.status === "ready").length;
  const errorCount = items.filter((item) => item.status === "error").length;
  const processingCount = items.filter((item) => item.status === "processing").length;

  return (
    <Stack spacing={3}>
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Bulk Import (Images)
          </Typography>
          <Stack spacing={2}>
            <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
              <Button variant="contained" component="label" disabled={runBusy}>
                Select images
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  capture="environment"
                  multiple
                  hidden
                  onChange={handleFilesChange}
                />
              </Button>
              <Button variant="outlined" onClick={clearItems} disabled={runBusy || items.length === 0}>
                Clear list
              </Button>
              <Typography variant="body2" color="text.secondary">
                {items.length} file(s) selected
              </Typography>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Pipeline settings
          </Typography>
          <Stack spacing={2}>
            <TextField
              label="Crop template"
              select
              value={templateId}
              onChange={(event) => setTemplateId(event.target.value)}
              size="small"
              sx={{ maxWidth: 320, width: isMobile ? "100%" : undefined }}
            >
              <MenuItem value="">No template</MenuItem>
              {templates.map((tmpl) => (
                <MenuItem key={tmpl.id} value={tmpl.id}>
                  {tmpl.name}
                </MenuItem>
              ))}
            </TextField>
            {templateNote && <Typography variant="body2">{templateNote}</Typography>}
            <Box display="flex" flexWrap="wrap" gap={2}>
              <FormControlLabel
                control={
                  <Switch checked={pipelineUseClassifier} onChange={(event) => setPipelineUseClassifier(event.target.checked)} />
                }
                label="Run classifier"
              />
              <FormControlLabel
                control={<Switch checked={pipelineUseOcr} onChange={(event) => setPipelineUseOcr(event.target.checked)} />}
                label="Run OCR"
              />
              <FormControlLabel
                control={
                  <Switch checked={pipelineUseGrid} onChange={(event) => setPipelineUseGrid(event.target.checked)} />
                }
                label="Auto-detect grid"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={pipelineUseTerminals}
                    onChange={(event) => setPipelineUseTerminals(event.target.checked)}
                  />
                }
                label="Auto-detect terminals"
              />
              {pipelineUseOcr && (
                <FormControlLabel
                  control={
                    <Switch checked={ocrWholeImage} onChange={(event) => setOcrWholeImage(event.target.checked)} />
                  }
                  label="OCR whole image"
                />
              )}
            </Box>
            <Divider />
            <Stack spacing={2}>
              <TextField
                label="Target type"
                select
                value={targetType}
                onChange={(event) => setTargetType(event.target.value as typeof targetType)}
                size="small"
                sx={{ maxWidth: 240, width: isMobile ? "100%" : undefined }}
              >
                <MenuItem value="auto">auto (classifier)</MenuItem>
                <MenuItem value="square">square</MenuItem>
                <MenuItem value="hex">hex</MenuItem>
                <MenuItem value="circle">circle</MenuItem>
                <MenuItem value="graph">graph</MenuItem>
                <MenuItem value="cube">cube</MenuItem>
                <MenuItem value="star">star</MenuItem>
                <MenuItem value="figure8">figure8</MenuItem>
              </TextField>

              {targetType === "graph" || targetType === "auto" ? (
                <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
                  <TextField
                    label="Graph layout"
                    select
                    value={graphLayout}
                    onChange={(event) => setGraphLayout(event.target.value as typeof graphLayout)}
                    size="small"
                    sx={{ minWidth: 200 }}
                  >
                    <MenuItem value="grid">grid</MenuItem>
                    <MenuItem value="line">line</MenuItem>
                  </TextField>
                  {graphLayout === "line" ? (
                    <TextField
                      label="Nodes"
                      type="number"
                      value={graphNodes}
                      onChange={(event) => setGraphNodes(Number(event.target.value))}
                      size="small"
                    />
                  ) : (
                    <>
                      <TextField
                        label="Grid width"
                        type="number"
                        value={gridWidth}
                        onChange={(event) => setGridWidth(Number(event.target.value))}
                        size="small"
                      />
                      <TextField
                        label="Grid height"
                        type="number"
                        value={gridHeight}
                        onChange={(event) => setGridHeight(Number(event.target.value))}
                        size="small"
                      />
                    </>
                  )}
                </Stack>
              ) : targetType === "cube" || targetType === "star" || targetType === "figure8" ? (
                <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
                  <TextField
                    label="Topology detail"
                    type="number"
                    value={gridWidth}
                    onChange={(event) => setGridWidth(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Height hint"
                    type="number"
                    value={gridHeight}
                    onChange={(event) => setGridHeight(Number(event.target.value))}
                    size="small"
                  />
                </Stack>
              ) : (
                <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
                  <TextField
                    label={targetType === "circle" ? "Sectors" : "Grid width"}
                    type="number"
                    value={gridWidth}
                    onChange={(event) => setGridWidth(Number(event.target.value))}
                    size="small"
                    disabled={pipelineUseGrid}
                  />
                  <TextField
                    label={targetType === "circle" ? "Rings" : "Grid height"}
                    type="number"
                    value={gridHeight}
                    onChange={(event) => setGridHeight(Number(event.target.value))}
                    size="small"
                    disabled={pipelineUseGrid}
                  />
                </Stack>
              )}
              {!pipelineUseTerminals && (
                <Alert severity="info">Disabling terminals means generated puzzles will have no terminals.</Alert>
              )}
            </Stack>
            <details>
              <summary>Advanced detection settings</summary>
              <Box mt={2}>
                <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
                  <TextField
                    label="Threshold"
                    type="number"
                    value={threshold}
                    onChange={(event) => setThreshold(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Line threshold"
                    type="number"
                    value={lineThreshold}
                    onChange={(event) => setLineThreshold(Number(event.target.value))}
                    size="small"
                  />
                  <FormControlLabel
                    control={<Switch checked={invert} onChange={(event) => setInvert(event.target.checked)} />}
                    label="Invert"
                  />
                  <FormControlLabel
                    control={<Switch checked={perspective} onChange={(event) => setPerspective(event.target.checked)} />}
                    label="Perspective"
                  />
                </Stack>
                <Stack spacing={2} direction={{ xs: "column", md: "row" }} mt={2}>
                  <TextField
                    label="Saturation threshold"
                    type="number"
                    value={satThreshold}
                    onChange={(event) => setSatThreshold(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Brightness min"
                    type="number"
                    value={brightnessMin}
                    onChange={(event) => setBrightnessMin(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Brightness max"
                    type="number"
                    value={brightnessMax}
                    onChange={(event) => setBrightnessMax(Number(event.target.value))}
                    size="small"
                  />
                </Stack>
                <Stack spacing={2} direction={{ xs: "column", md: "row" }} mt={2}>
                  <TextField
                    label="Margin ratio"
                    type="number"
                    value={marginRatio}
                    onChange={(event) => setMarginRatio(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Cluster threshold"
                    type="number"
                    value={clusterThreshold}
                    onChange={(event) => setClusterThreshold(Number(event.target.value))}
                    size="small"
                  />
                  <TextField
                    label="Background threshold"
                    type="number"
                    value={bgThreshold}
                    onChange={(event) => setBgThreshold(Number(event.target.value))}
                    size="small"
                  />
                </Stack>
              </Box>
            </details>
            <Box display="flex" flexWrap="wrap" gap={2}>
              <Button variant="contained" onClick={runPipeline} disabled={runBusy || items.length === 0}>
                Run pipeline
              </Button>
              <Button variant="outlined" onClick={resetResults} disabled={runBusy || items.length === 0}>
                Reset results
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      {runStatus && <Alert severity="info">{runStatus}</Alert>}

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Results
          </Typography>
          <Box display="flex" flexWrap="wrap" gap={1} mb={2}>
            <Chip label={`Ready ${readyCount}`} size="small" color="success" variant="outlined" />
            <Chip label={`Processing ${processingCount}`} size="small" color="warning" variant="outlined" />
            <Chip label={`Errors ${errorCount}`} size="small" color="error" variant="outlined" />
            <Chip label={`Selected ${selectedCount}`} size="small" color="info" variant="outlined" />
          </Box>
          <Box display="flex" flexWrap="wrap" gap={2} alignItems="center" mb={2}>
            <Button variant="outlined" onClick={() => selectAll(true)} disabled={runBusy}>
              Select all
            </Button>
            <Button variant="outlined" onClick={() => selectAll(false)} disabled={runBusy}>
              Select none
            </Button>
            <Button variant="contained" onClick={handleBulkSave} disabled={runBusy || selectedCount === 0}>
              Save selected ({selectedCount})
            </Button>
          </Box>
          {isMobile ? (
            <Stack spacing={1.5}>
              {items.map((item) => {
                const dup = duplicates.get(item.id);
                const statusColor =
                  item.status === "ready" ? "success" : item.status === "error" ? "error" : "default";
                const saveColor = item.saveStatus === "saved" ? "success" : item.saveStatus === "error" ? "error" : "default";
                return (
                  <Card key={item.id} variant="outlined" sx={{ borderColor: "rgba(255,255,255,0.12)" }}>
                    <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                      <Stack spacing={1.25}>
                        <Box display="flex" justifyContent="space-between" alignItems="flex-start" gap={1}>
                          <Typography variant="subtitle2" sx={{ wordBreak: "break-word" }}>
                            {item.file.name}
                          </Typography>
                          <Checkbox
                            size="small"
                            checked={item.selected}
                            onChange={(event) =>
                              updateItem(item.id, (prev) => ({ ...prev, selected: event.target.checked }))
                            }
                            disabled={item.status !== "ready" || runBusy}
                          />
                        </Box>
                        <Box display="flex" flexWrap="wrap" gap={1}>
                          <Chip label={item.status} size="small" color={statusColor} variant="outlined" />
                          {dup && (
                            <Chip
                              label={dup.conflicts.length ? "Conflict" : "Potential duplicate"}
                              size="small"
                              color={dup.conflicts.length ? "error" : "warning"}
                              variant="outlined"
                              title={[...dup.conflicts, ...dup.warnings].join(", ")}
                            />
                          )}
                          {item.saveStatus !== "idle" && (
                            <Chip label={item.saveStatus} size="small" color={saveColor} variant="outlined" />
                          )}
                        </Box>
                        {item.message && (
                          <Typography variant="caption" color="text.secondary">
                            {item.message}
                          </Typography>
                        )}
                        <TextField
                          label="Generated name"
                          value={item.name ?? ""}
                          onChange={(event) =>
                            updateItem(item.id, (prev) => ({ ...prev, name: event.target.value }))
                          }
                          size="small"
                          fullWidth
                          disabled={item.status !== "ready" || runBusy}
                          helperText={
                            item.suggestedName && item.suggestedName !== item.name ? `OCR: ${item.suggestedName}` : " "
                          }
                        />
                        {item.saveMessage && (
                          <Typography variant="caption" color="text.secondary">
                            {item.saveMessage}
                          </Typography>
                        )}
                      </Stack>
                    </CardContent>
                  </Card>
                );
              })}
              {!items.length && <Alert severity="info">Add images to start a batch import.</Alert>}
            </Stack>
          ) : (
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small" sx={{ minWidth: 760 }}>
                <TableHead>
                  <TableRow>
                    <TableCell padding="checkbox" />
                    <TableCell>File</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Generated name</TableCell>
                    <TableCell>Duplicates</TableCell>
                    <TableCell>Save</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {items.map((item) => {
                    const dup = duplicates.get(item.id);
                    const statusColor =
                      item.status === "ready" ? "success" : item.status === "error" ? "error" : "default";
                    const saveColor =
                      item.saveStatus === "saved" ? "success" : item.saveStatus === "error" ? "error" : "default";
                    return (
                      <TableRow key={item.id}>
                        <TableCell padding="checkbox">
                          <Checkbox
                            checked={item.selected}
                            onChange={(event) =>
                              updateItem(item.id, (prev) => ({ ...prev, selected: event.target.checked }))
                            }
                            disabled={item.status !== "ready" || runBusy}
                          />
                        </TableCell>
                        <TableCell>{item.file.name}</TableCell>
                        <TableCell>
                          <Stack spacing={0.5}>
                            <Chip label={item.status} size="small" color={statusColor} variant="outlined" />
                            {item.message && (
                              <Typography variant="caption" color="text.secondary">
                                {item.message}
                              </Typography>
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <TextField
                            value={item.name ?? ""}
                            onChange={(event) =>
                              updateItem(item.id, (prev) => ({ ...prev, name: event.target.value }))
                            }
                            size="small"
                            disabled={item.status !== "ready" || runBusy}
                            helperText={
                              item.suggestedName && item.suggestedName !== item.name ? `OCR: ${item.suggestedName}` : " "
                            }
                          />
                        </TableCell>
                        <TableCell>
                          {dup ? (
                            <Chip
                              label={dup.conflicts.length ? "Conflict" : "Potential"}
                              size="small"
                              color={dup.conflicts.length ? "error" : "warning"}
                              variant="outlined"
                              title={[...dup.conflicts, ...dup.warnings].join(", ")}
                            />
                          ) : (
                            <Typography variant="caption" color="text.secondary">
                              -
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          {item.saveStatus !== "idle" ? (
                            <Stack spacing={0.5}>
                              <Chip label={item.saveStatus} size="small" color={saveColor} variant="outlined" />
                              {item.saveMessage && (
                                <Typography variant="caption" color="text.secondary">
                                  {item.saveMessage}
                                </Typography>
                              )}
                            </Stack>
                          ) : (
                            <Typography variant="caption" color="text.secondary">
                              -
                            </Typography>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {!items.length && (
                    <TableRow>
                      <TableCell colSpan={6}>
                        <Typography variant="body2" color="text.secondary">
                          Add images to start a batch import.
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </Box>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
}

