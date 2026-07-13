import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  LinearProgress,
  MenuItem,
  Modal,
  Stack,
  Switch,
  TextField,
  Typography
} from "@mui/material";
import {
  AutoAwesome,
  Cancel,
  CheckCircle,
  Close,
  CropFree,
  DeleteOutline,
  ExpandMore,
  HourglassEmpty,
  PhotoCamera,
  RemoveCircleOutline,
  TuneOutlined,
  UploadFile
} from "@mui/icons-material";
import ReactCrop, { Crop, PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import {
  archiveImageImportFailure,
  cropTemplatePreviewUrl,
  deleteCropTemplate,
  fetchImageImportFile,
  imageAutoCrop,
  imageClassify,
  imageDetectGrid,
  imageDetectTerminals,
  imageGenerate,
  imageOcr,
  ImageImportEntry,
  LevelType,
  listCropTemplates,
  listPuzzles,
  recordImageImportReprocessFailure,
  saveCropTemplate,
  savePuzzle,
  solvePuzzle,
  SolveResponse
} from "../api";
import { GameView } from "../components/GameView";
import {
  EdgeOverrides,
  formatEdgePairsText,
  isEdgeOverridesEmpty,
  parseEdgeOverrideTexts
} from "../edgeOverrides";

type TerminalPayload = { row: number; col: number; letter: string; color?: number[] };
type NodeTerminalPayload = { nodeId: string; letter: string; color?: number[] };
type BuilderType = "square" | "hex" | "circle" | "graph" | "cube" | "star" | "figure8";
type TargetType = "auto" | BuilderType;

type ImageViewProps = {
  onGenerated: (name: string, text: string) => void;
  onApplied?: () => void;
  onSuggestedName?: (name: string) => void;
  onApplyGrid?: (payload: {
    type: BuilderType;
    rows: number;
    cols: number;
    terminals: TerminalPayload[];
    nodeTerminals?: NodeTerminalPayload[];
    suggestedName?: string | null;
    levelType?: LevelType | null;
    edgeOverrides?: EdgeOverrides;
  }) => void;
  embedded?: boolean;
  preferredTargetType?: TargetType;
  // Archived screenshots queued for reprocessing from the library; a new token
  // (with the entries to fetch) enqueues them into the batch pipeline.
  reprocessRequest?: { token: number; entries: ImageImportEntry[] } | null;
  onReprocessHandled?: () => void;
};

type CropPixels = { x: number; y: number; width: number; height: number };

type BatchStatus = "queued" | "processing" | "solved" | "detected" | "error";

type BatchItem = {
  id: string;
  file: File;
  previewUrl: string;
  status: BatchStatus;
  name: string;
  text?: string;
  geometry?: string;
  sizeLabel?: string;
  endpoints?: number;
  solve?: SolveResponse;
  solveMs?: number | null;
  error?: string;
  saveState: "idle" | "saving" | "saved" | "error";
  saveMessage?: string;
  archiveImportId?: string;
};

type BatchSource = {
  file: File;
  archiveImportId?: string;
};

const DEFAULT_CROP: Crop = { unit: "%", x: 0, y: 0, width: 100, height: 100 };

type PipelineState = "idle" | "pending" | "ok" | "fail" | "skipped";

function getBuilderType(
  targetType: TargetType,
  levelType: LevelType | null
): BuilderType {
  const recommended = levelType?.recommended_target_type;
  const geometry = levelType?.geometry;
  const mappedGeometry =
    geometry === "square" ||
    geometry === "hex" ||
    geometry === "circle" ||
    geometry === "graph" ||
    geometry === "cube" ||
    geometry === "star" ||
    geometry === "figure8"
      ? geometry
      : "square";
  if (targetType === "auto") {
    if (recommended) {
      return recommended as BuilderType;
    }
    return mappedGeometry;
  }
  return targetType;
}

function choosePipelineBuilderType(
  targetType: TargetType,
  levelType: LevelType | null
): BuilderType {
  const base = getBuilderType(targetType, levelType);
  if (targetType !== "auto" || !levelType) {
    return base;
  }
  if (levelType.signals?.recommended_graph_layout === "regions") {
    return "graph";
  }
  if (base === "cube" || base === "star" || base === "figure8") {
    return base;
  }
  const rawCandidates = Array.isArray(levelType.candidates) ? levelType.candidates : [];
  const top = rawCandidates[0];
  const topConf = top && Number.isFinite(top.confidence) ? Number(top.confidence) : 0;
  const topologyCandidate = rawCandidates
    .filter((candidate) => candidate && (candidate.geometry === "cube" || candidate.geometry === "star" || candidate.geometry === "figure8"))
    .sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0))[0];
  if (!topologyCandidate) {
    return base;
  }
  const topoConf = Number(topologyCandidate.confidence) || 0;
  if (topoConf >= Math.max(0.22, topConf * 0.62)) {
    return topologyCandidate.geometry as BuilderType;
  }
  return base;
}

function graphLayoutForDetection(
  levelType: LevelType | null,
  fallback: "grid" | "line" | "regions"
): "grid" | "line" | "regions" {
  return levelType?.signals?.recommended_graph_layout === "regions" ? "regions" : fallback;
}

// The backend picks its parser from the file extension, so an OCR-suggested
// name like "classic_level_1.flow" must not be paired with generated JSON text.
function nameForPuzzleText(
  preferred: string | null | undefined,
  fallback: string,
  text: string
): string {
  const base = (preferred ?? fallback).trim() || fallback;
  const ext = text.trimStart().startsWith("{") ? ".json" : ".flow";
  return `${base.replace(/\.(flow|json)$/i, "")}${ext}`;
}

function parseSizeHint(text: string | null | undefined): { cols: number; rows: number } | null {
  if (!text) {
    return null;
  }
  const raw = String(text);
  const patterns = [
    /([1-9][0-9]{0,1})\s*[x×]\s*([1-9][0-9]{0,1})/i,
    /([1-9][0-9]{0,1})\s*by\s*([1-9][0-9]{0,1})/i
  ];
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    if (!match) {
      continue;
    }
    const cols = Number(match[1]);
    const rows = Number(match[2]);
    if (Number.isFinite(cols) && Number.isFinite(rows) && cols > 0 && rows > 0) {
      return {
        cols: Math.max(1, Math.min(40, Math.round(cols))),
        rows: Math.max(1, Math.min(40, Math.round(rows)))
      };
    }
  }
  return null;
}

function graphEdgeOverridesFromText(text: string): EdgeOverrides | null {
  try {
    const obj = JSON.parse(text) as {
      format?: string;
      schema_version?: number;
      topology?: {
        adjacencies?: Array<{
          a?: { channel?: string };
          b?: { channel?: string };
          kind?: string;
          state?: string;
        }>;
      };
      space?: {
        edge_overrides?: { add?: Array<[string, string]>; remove?: Array<[string, string]> };
        warps?: Array<[string, string]>;
        walls?: Array<[string, string]>;
      };
    };
    if (obj.format === "flow-solver-puzzle" && obj.schema_version === 2) {
      const out: EdgeOverrides = { add: [], remove: [], warps: [], walls: [] };
      for (const adjacency of obj.topology?.adjacencies ?? []) {
        const u = adjacency.a?.channel;
        const v = adjacency.b?.channel;
        if (!u || !v || u === v) {
          continue;
        }
        if (adjacency.state === "blocked") {
          out.walls.push([u, v]);
        } else if (adjacency.kind === "warp") {
          out.warps.push([u, v]);
        } else if (adjacency.kind === "custom") {
          out.add.push([u, v]);
        }
      }
      return out;
    }
    if (!obj?.space) {
      return null;
    }
    return {
      add: obj.space.edge_overrides?.add ?? [],
      remove: obj.space.edge_overrides?.remove ?? [],
      warps: obj.space.warps ?? [],
      walls: obj.space.walls ?? []
    };
  } catch {
    return null;
  }
}

export function ImageView({
  onGenerated,
  onApplied,
  onSuggestedName,
  onApplyGrid,
  embedded = false,
  preferredTargetType,
  reprocessRequest = null,
  onReprocessHandled
}: ImageViewProps) {
  const [file, setFile] = useState<File | null>(null);
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [imageName, setImageName] = useState("");
  const [imageDims, setImageDims] = useState<{ width: number; height: number } | null>(null);
  const [crop, setCrop] = useState<Crop>(DEFAULT_CROP);
  const [completedCrop, setCompletedCrop] = useState<PixelCrop | null>(null);
  const [threshold, setThreshold] = useState(230);
  const [lineThreshold, setLineThreshold] = useState(0.6);
  const [invert, setInvert] = useState(false);
  const [perspective, setPerspective] = useState(false);
  const [padding, setPadding] = useState(6);
  const [gridWidth, setGridWidth] = useState(10);
  const [gridHeight, setGridHeight] = useState(10);
  const [targetType, setTargetType] = useState<TargetType>(preferredTargetType ?? "auto");
  const [graphLayout, setGraphLayout] = useState<"grid" | "line" | "regions">("grid");
  const [graphNodes, setGraphNodes] = useState(12);
  const [autoTerminals, setAutoTerminals] = useState(true);
  const [autoClassify, setAutoClassify] = useState(true);
  const [autoProcess, setAutoProcess] = useState(true);
  const [satThreshold, setSatThreshold] = useState(30);
  const [brightnessMin, setBrightnessMin] = useState(30);
  const [brightnessMax, setBrightnessMax] = useState(230);
  const [marginRatio, setMarginRatio] = useState(0.15);
  const [clusterThreshold, setClusterThreshold] = useState(60);
  const [bgThreshold, setBgThreshold] = useState(40);
  const [status, setStatus] = useState<string | null>(null);
  const [terminalStatus, setTerminalStatus] = useState<string | null>(null);
  const [generatedName, setGeneratedName] = useState("");
  const [generatedText, setGeneratedText] = useState("");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [gridDetection, setGridDetection] = useState<{ rows: number; cols: number } | null>(null);
  const [terminalDetections, setTerminalDetections] = useState<
    Array<{ row: number; col: number; letter: string; color: number[] }>
  >([]);
  const [gridStatus, setGridStatus] = useState<string | null>(null);
  const [templates, setTemplates] = useState<Array<import("../api").CropTemplate>>([]);
  const [templateId, setTemplateId] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [templateNote, setTemplateNote] = useState("");
  const [templateStatus, setTemplateStatus] = useState<string | null>(null);
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const [pipelineStatus, setPipelineStatus] = useState<string | null>(null);
  const [pipelineUseClassifier, setPipelineUseClassifier] = useState(true);
  const [pipelineUseOcr, setPipelineUseOcr] = useState(true);
  const [pipelineUseGrid, setPipelineUseGrid] = useState(true);
  const [pipelineUseTerminals, setPipelineUseTerminals] = useState(true);
  const [pipelineChecks, setPipelineChecks] = useState<{
    classify: PipelineState;
    ocr: PipelineState;
    grid: PipelineState;
    terminals: PipelineState;
  }>({ classify: "idle", ocr: "idle", grid: "idle", terminals: "idle" });
  const [ocrText, setOcrText] = useState("");
  const [ocrSuggested, setOcrSuggested] = useState<string | null>(null);
  const [ocrWholeImage, setOcrWholeImage] = useState(true);
  const [levelType, setLevelType] = useState<LevelType | null>(null);
  const [levelTypeStatus, setLevelTypeStatus] = useState<string | null>(null);
  const [edgeAddText, setEdgeAddText] = useState("");
  const [edgeRemoveText, setEdgeRemoveText] = useState("");
  const [edgeWarpsText, setEdgeWarpsText] = useState("");
  const [edgeWallsText, setEdgeWallsText] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [lightbox, setLightbox] = useState<{ url: string; alt: string } | null>(null);

  const [batchItems, setBatchItems] = useState<BatchItem[]>([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [reprocessProgress, setReprocessProgress] = useState<{ current: number; total: number } | null>(null);
  const [reprocessError, setReprocessError] = useState<string | null>(null);
  const [libraryIndex, setLibraryIndex] = useState<{
    names: Set<string>;
    sourceImages: Set<string>;
  } | null>(null);

  const imgRef = useRef<HTMLImageElement | null>(null);
  const previewCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const autoRanForSrcRef = useRef<string | null>(null);
  const batchUrlsRef = useRef<string[]>([]);
  const handledReprocessTokenRef = useRef(0);

  // Batch mode is only offered in the standalone importer; the builder's
  // embedded importer applies one screenshot to the board at a time.
  const allowBatch = !onApplyGrid;
  const batchMode = batchItems.length > 0;

  // Warn about duplicates before saving batch results into the library.
  useEffect(() => {
    if (!batchMode || libraryIndex) {
      return;
    }
    void listPuzzles()
      .then((entries) => {
        const names = new Set<string>();
        const sourceImages = new Set<string>();
        entries.forEach((entry) => {
          const name = entry.name.trim().toLowerCase().replace(/\.(flow|json)$/i, "");
          if (name) {
            names.add(name);
          }
          const sourceImage = String(entry.meta?.source_image ?? "").trim().toLowerCase();
          if (sourceImage) {
            sourceImages.add(sourceImage);
          }
        });
        setLibraryIndex({ names, sourceImages });
      })
      .catch(() => undefined);
  }, [batchMode, libraryIndex]);

  const batchDuplicates = useMemo(() => {
    const out = new Map<string, string[]>();
    if (!libraryIndex) {
      return out;
    }
    const normalizeName = (value: string) => value.trim().toLowerCase().replace(/\.(flow|json)$/i, "");
    const nameCounts = new Map<string, number>();
    batchItems.forEach((item) => {
      const key = normalizeName(item.name);
      if (key) {
        nameCounts.set(key, (nameCounts.get(key) ?? 0) + 1);
      }
    });
    batchItems.forEach((item) => {
      if (item.saveState === "saved") {
        return;
      }
      const warnings: string[] = [];
      const key = normalizeName(item.name);
      if (key && libraryIndex.names.has(key)) {
        warnings.push("a puzzle with this name is already in the library");
      }
      if (key && (nameCounts.get(key) ?? 0) > 1) {
        warnings.push("this name repeats in the batch");
      }
      if (libraryIndex.sourceImages.has(item.file.name.trim().toLowerCase())) {
        warnings.push("this screenshot file was already imported into the library");
      }
      if (warnings.length) {
        out.set(item.id, warnings);
      }
    });
    return out;
  }, [batchItems, libraryIndex]);

  useEffect(() => {
    return () => {
      batchUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  const selectImageFile = useCallback((next: File | null) => {
    if (!next) {
      setFile(null);
      setImageSrc(null);
      setImageName("");
      setGeneratedName("");
      setGeneratedText("");
      setPipelineStatus(null);
      return;
    }
    setFile(next);
    setImageName(next.name);
    setImageSrc(URL.createObjectURL(next));
    setCrop(DEFAULT_CROP);
    setCompletedCrop(null);
    setGeneratedName("");
    setGeneratedText("");
    setStatus(null);
    setSaveStatus(null);
    setSaveError(null);
    setTerminalStatus(null);
    setGridStatus(null);
    setGridDetection(null);
    setTerminalDetections([]);
    setLevelType(null);
    setLevelTypeStatus(null);
    setPipelineStatus(null);
    setPipelineChecks({ classify: "idle", ocr: "idle", grid: "idle", terminals: "idle" });
  }, []);
  const refreshTemplates = useCallback(async () => {
    try {
      const data = await listCropTemplates();
      setTemplates(data);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    refreshTemplates();
  }, [refreshTemplates]);

  useEffect(() => {
    const tmpl = templates.find((t) => t.id === templateId);
    if (tmpl?.pipeline) {
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

  const parsedEdgeOverrides = useMemo(() => {
    try {
      const parsed = parseEdgeOverrideTexts({
        addText: edgeAddText,
        removeText: edgeRemoveText,
        warpsText: edgeWarpsText,
        wallsText: edgeWallsText
      });
      return { value: parsed, error: null as string | null };
    } catch (err) {
      return {
        value: null,
        error: err instanceof Error ? err.message : "Invalid edge override input."
      };
    }
  }, [edgeAddText, edgeRemoveText, edgeWarpsText, edgeWallsText]);

  const manualEdgeOverrides = useMemo(() => {
    if (!parsedEdgeOverrides.value || isEdgeOverridesEmpty(parsedEdgeOverrides.value)) {
      return undefined;
    }
    return parsedEdgeOverrides.value;
  }, [parsedEdgeOverrides.value]);
  const graphTarget = targetType === "graph";
  const topologyTarget = targetType === "cube" || targetType === "star" || targetType === "figure8";

  useEffect(() => {
    if (preferredTargetType) {
      setTargetType(preferredTargetType);
    }
  }, [preferredTargetType]);

  const imageSize = useMemo(() => {
    if (!imageDims) {
      return "";
    }
    return `${imageDims.width}x${imageDims.height}`;
  }, [imageDims]);

  useEffect(() => {
    if (!imageSrc) {
      return;
    }
    return () => URL.revokeObjectURL(imageSrc);
  }, [imageSrc]);

  useEffect(() => {
    if (imgRef.current && completedCrop && previewCanvasRef.current) {
      const canvas = previewCanvasRef.current;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return;
      }
      const image = imgRef.current;
      const scaleX = image.naturalWidth / image.width;
      const scaleY = image.naturalHeight / image.height;
      const pixelRatio = window.devicePixelRatio || 1;
      const canvasWidth = completedCrop.width * scaleX * pixelRatio;
      const canvasHeight = completedCrop.height * scaleY * pixelRatio;
      canvas.width = canvasWidth;
      canvas.height = canvasHeight;
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(
        image,
        completedCrop.x * scaleX,
        completedCrop.y * scaleY,
        completedCrop.width * scaleX,
        completedCrop.height * scaleY,
        0,
        0,
        completedCrop.width * scaleX,
        completedCrop.height * scaleY
      );

      if (gridDetection && gridDetection.cols > 0 && gridDetection.rows > 0) {
        const cellW = (completedCrop.width * scaleX) / gridDetection.cols;
        const cellH = (completedCrop.height * scaleY) / gridDetection.rows;
        ctx.strokeStyle = "rgba(0, 200, 255, 0.6)";
        ctx.lineWidth = 1;
        for (let c = 0; c <= gridDetection.cols; c += 1) {
          const x = c * cellW;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, completedCrop.height * scaleY);
          ctx.stroke();
        }
        for (let r = 0; r <= gridDetection.rows; r += 1) {
          const y = r * cellH;
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(completedCrop.width * scaleX, y);
          ctx.stroke();
        }

        if (terminalDetections.length) {
          terminalDetections.forEach((t) => {
            const r = t.color[0] ?? 255;
            const g = t.color[1] ?? 82;
            const b = t.color[2] ?? 82;
            ctx.fillStyle = `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, 0.85)`;
            const cx = (t.col + 0.5) * cellW;
            const cy = (t.row + 0.5) * cellH;
            ctx.beginPath();
            ctx.arc(cx, cy, Math.max(3, cellW * 0.15), 0, Math.PI * 2);
            ctx.fill();
          });
        }
      }
    }
  }, [completedCrop, gridDetection, terminalDetections]);

  function naturalDims(): { width: number; height: number } | null {
    if (imageDims) {
      return imageDims;
    }
    if (imgRef.current?.naturalWidth) {
      return { width: imgRef.current.naturalWidth, height: imgRef.current.naturalHeight };
    }
    return null;
  }

  function getCropPixels(): CropPixels | null {
    if (!imgRef.current) {
      return null;
    }
    if (!completedCrop || completedCrop.width <= 0 || completedCrop.height <= 0) {
      return {
        x: 0,
        y: 0,
        width: imgRef.current.naturalWidth,
        height: imgRef.current.naturalHeight
      };
    }
    const scaleX = imgRef.current.naturalWidth / imgRef.current.width;
    const scaleY = imgRef.current.naturalHeight / imgRef.current.height;
    return {
      x: Math.round(completedCrop.x * scaleX),
      y: Math.round(completedCrop.y * scaleY),
      width: Math.round(completedCrop.width * scaleX),
      height: Math.round(completedCrop.height * scaleY)
    };
  }

  function isFullFrameCrop(): boolean {
    const dims = naturalDims();
    const current = getCropPixels();
    if (!dims || !current) {
      return true;
    }
    const ratio = (current.width * current.height) / Math.max(1, dims.width * dims.height);
    return ratio >= 0.985;
  }

  const templateCropPixels = (
    tmpl: import("../api").CropTemplate,
    dims?: { width: number; height: number } | null
  ) => {
    const size = dims ?? naturalDims();
    if (!size) {
      return null;
    }
    const pct = tmpl.crop_pct;
    return {
      x: Math.round(pct.x * size.width),
      y: Math.round(pct.y * size.height),
      width: Math.round(pct.width * size.width),
      height: Math.round(pct.height * size.height)
    };
  };

  function cropPixelsToPercent(cropBox: CropPixels, dims?: { width: number; height: number } | null): Crop {
    const size = dims ?? naturalDims();
    if (!size) {
      return DEFAULT_CROP;
    }
    const x = (cropBox.x / size.width) * 100;
    const y = (cropBox.y / size.height) * 100;
    const width = (cropBox.width / size.width) * 100;
    const height = (cropBox.height / size.height) * 100;
    return { unit: "%", x, y, width, height };
  }

  function applyCropPixels(cropBox: CropPixels, dims?: { width: number; height: number } | null) {
    const size = dims ?? naturalDims();
    if (!size) {
      return;
    }
    setCrop(cropPixelsToPercent(cropBox, size));
    if (imgRef.current) {
      const scaleX = imgRef.current.width / size.width;
      const scaleY = imgRef.current.height / size.height;
      setCompletedCrop({
        unit: "px",
        x: cropBox.x * scaleX,
        y: cropBox.y * scaleY,
        width: cropBox.width * scaleX,
        height: cropBox.height * scaleY
      });
    }
  }

  const applyTemplateCrop = (tmpl: import("../api").CropTemplate) => {
    const cropBox = templateCropPixels(tmpl);
    if (!cropBox) {
      return;
    }
    applyCropPixels(cropBox);
  };

  async function handleAutoCrop(): Promise<CropPixels | null> {
    if (!file || !imgRef.current) {
      return null;
    }
    const dims = {
      width: imgRef.current.naturalWidth,
      height: imgRef.current.naturalHeight
    };
    try {
      const tmpl = templates.find((t) => t.id === templateId);
      let seedCrop: CropPixels | null = null;
      if (tmpl) {
        seedCrop = templateCropPixels(tmpl, dims);
      }
      if (!seedCrop) {
        const current = getCropPixels();
        if (current) {
          const ratio = (current.width * current.height) / Math.max(1, dims.width * dims.height);
          if (ratio < 0.985) {
            seedCrop = current;
          }
        }
      }
      const res = await imageAutoCrop({ file, threshold, invert, padding, crop: seedCrop });
      if (!res.crop) {
        setStatus(res.message ?? "Auto-crop failed.");
        return null;
      }
      applyCropPixels(res.crop, dims);
      setGridDetection(null);
      setTerminalDetections([]);
      setStatus(
        res.message ??
          (seedCrop ? "Auto-crop applied (refined from seed crop)." : "Auto-crop applied.")
      );
      return res.crop;
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Auto-crop failed.");
      return null;
    }
  }

  async function handleClassifyLevel(cropOverride?: CropPixels | null) {
    if (!file) {
      return null;
    }
    try {
      const res = await imageClassify({
        file,
        threshold,
        lineThreshold,
        invert,
        perspective,
        levelHint: ocrSuggested ?? imageName,
        crop: cropOverride === undefined ? getCropPixels() : cropOverride
      });
      setLevelType(res.level_type);
      setLevelTypeStatus(
        `Detected ${res.level_type.geometry} (${Math.round(res.level_type.confidence * 100)}% confidence).`
      );
      return res.level_type;
    } catch (err) {
      setLevelTypeStatus(err instanceof Error ? err.message : "Level type classification failed.");
      return null;
    }
  }

  async function handleDetectGrid() {
    if (!file) {
      return;
    }
    try {
      const builderType = getBuilderType(targetType, levelType);
      const res = await imageDetectGrid({
        file,
        targetType: builderType,
        threshold,
        lineThreshold,
        invert,
        crop: getCropPixels(),
        perspective
      });
      if (!res.grid) {
        setGridStatus(res.message ?? "Grid detection failed.");
        return;
      }
      setGridWidth(res.grid.cols);
      setGridHeight(res.grid.rows);
      setGridDetection({ rows: res.grid.rows, cols: res.grid.cols });
      if (onApplyGrid) {
        onApplyGrid({
          type: builderType,
          rows: res.grid.rows,
          cols: res.grid.cols,
          terminals: terminalDetections.map((t) => ({ row: t.row, col: t.col, letter: t.letter, color: t.color })),
          suggestedName: ocrSuggested,
          levelType,
          edgeOverrides: manualEdgeOverrides
        });
      }
      const mode = String(res.grid.mode ?? "rect");
      if (mode === "circle") {
        setGridStatus(`Detected circle grid ${res.grid.cols} sectors x ${res.grid.rows} rings.`);
      } else {
        setGridStatus(
          `Detected ${res.grid.cols}x${res.grid.rows} grid (lines: ${res.grid.vertical_lines}x${res.grid.horizontal_lines}).`
        );
      }
    } catch (err) {
      setGridStatus(err instanceof Error ? err.message : "Grid detection failed.");
    }
  }

  async function handleDetectTerminals() {
    if (!file) {
      return;
    }
    try {
      const builderType = getBuilderType(targetType, levelType);
      const res = await imageDetectTerminals({
        file,
        targetType: builderType,
        rows: gridHeight,
        cols: gridWidth,
        satThreshold,
        brightnessMin,
        brightnessMax,
        marginRatio,
        clusterThreshold,
        bgThreshold,
        crop: getCropPixels(),
        perspective
      });
      const warnings = res.info?.warnings?.length ? res.info.warnings.join(" ") : "No warnings.";
      setTerminalStatus(`Detected ${res.terminals.length} terminals. ${warnings}`);
      setTerminalDetections(res.terminals);
      const rows = gridDetection?.rows ?? gridHeight;
      const cols = gridDetection?.cols ?? gridWidth;
      if (!gridDetection) {
        setGridDetection({ rows, cols });
      }
      if (onApplyGrid) {
        const builderType = getBuilderType(targetType, levelType);
        onApplyGrid({
          type: builderType,
          rows,
          cols,
          terminals: res.terminals.map((t) => ({ row: t.row, col: t.col, letter: t.letter, color: t.color })),
          suggestedName: ocrSuggested,
          levelType,
          edgeOverrides: manualEdgeOverrides
        });
      }
    } catch (err) {
      setTerminalStatus(err instanceof Error ? err.message : "Terminal detection failed.");
    }
  }

  async function handleGenerate() {
    if (!file) {
      setStatus("Upload an image first.");
      return;
    }
    if (parsedEdgeOverrides.error) {
      setStatus(parsedEdgeOverrides.error);
      return;
    }
    try {
      const effectiveGraphLayout = graphLayoutForDetection(levelType, graphLayout);
      const res = await imageGenerate({
        file,
        targetType,
        gridWidth,
        gridHeight,
        graphLayout: effectiveGraphLayout,
        graphNodes,
        autoTerminals,
        autoClassify,
        levelType,
        edgeOverrides: manualEdgeOverrides,
        metadata: {
          title: "",
          source: "image-import"
        },
        crop: getCropPixels(),
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
      setGeneratedName(res.name);
      setGeneratedText(res.text);
      setGridDetection(
        res.detection?.grid
          ? {
              rows: (res.detection.grid as { rows: number }).rows,
              cols: (res.detection.grid as { cols: number }).cols
            }
          : null
      );
      const rawDetectedTerminals = Array.isArray(res.detection?.terminals)
        ? (res.detection.terminals as Array<{
            row?: number;
            col?: number;
            node_id?: string;
            letter?: string;
            color?: number[];
          }>)
        : [];
      const detectedGridTerminals: Array<{ row: number; col: number; letter: string; color: number[] }> =
        rawDetectedTerminals
          .filter(
            (terminal) =>
              Number.isFinite(terminal.row) &&
              Number.isFinite(terminal.col) &&
              typeof terminal.letter === "string"
          )
          .map((terminal) => ({
            row: Number(terminal.row),
            col: Number(terminal.col),
            letter: String(terminal.letter),
            color: terminal.color ?? []
          }));
      const detectedNodeTerminals: NodeTerminalPayload[] = rawDetectedTerminals
        .filter((terminal) => typeof terminal.node_id === "string" && terminal.node_id && typeof terminal.letter === "string")
        .map((terminal) => ({
          nodeId: String(terminal.node_id),
          letter: String(terminal.letter),
          color: terminal.color
        }));
      if (detectedGridTerminals.length) {
        setTerminalDetections(detectedGridTerminals);
      }
      const detectedLevelType = (res.detection?.level_type as LevelType | undefined) ?? null;
      if (detectedLevelType) {
        setLevelType(detectedLevelType);
        setLevelTypeStatus(
          `Detected ${detectedLevelType.geometry} (${Math.round(detectedLevelType.confidence * 100)}% confidence).`
        );
      }
      setStatus("Generated puzzle text.");
      const targetUsed = String(res.detection?.target_type_used ?? targetType);
      if (onApplyGrid && !(targetUsed === "graph" && effectiveGraphLayout === "regions")) {
        const rows =
          (res.detection?.grid as { rows?: number } | undefined)?.rows ??
          gridDetection?.rows ??
          gridHeight;
        const cols =
          (res.detection?.grid as { cols?: number } | undefined)?.cols ??
          gridDetection?.cols ??
          gridWidth;
        const terminals = detectedGridTerminals.map((terminal) => ({
          row: terminal.row,
          col: terminal.col,
          letter: terminal.letter,
          color: terminal.color
        }));
        const targetForBuilder =
          targetUsed === "auto" ||
          targetUsed === "square" ||
          targetUsed === "hex" ||
          targetUsed === "circle" ||
          targetUsed === "graph" ||
          targetUsed === "cube" ||
          targetUsed === "star" ||
          targetUsed === "figure8"
            ? targetUsed
            : targetType;
        const builderType = getBuilderType(
          targetForBuilder,
          detectedLevelType ?? levelType
        );
        const graphOverrides =
          targetUsed === "graph" || targetUsed === "cube" || targetUsed === "star" || targetUsed === "figure8"
            ? graphEdgeOverridesFromText(res.text)
            : null;
        if (graphOverrides) {
          setEdgeAddText(formatEdgePairsText(graphOverrides.add));
          setEdgeRemoveText(formatEdgePairsText(graphOverrides.remove));
          setEdgeWarpsText(formatEdgePairsText(graphOverrides.warps));
          setEdgeWallsText(formatEdgePairsText(graphOverrides.walls));
        }
        const suggestedForBuilder =
          builderType === "square" || builderType === "hex" || builderType === "circle"
            ? res.name.replace(/\.json$/i, ".flow")
            : res.name;
        onApplyGrid({
          type: builderType,
          rows,
          cols,
          terminals,
          nodeTerminals: detectedNodeTerminals,
          suggestedName: ocrSuggested ?? suggestedForBuilder,
          levelType: detectedLevelType ?? levelType,
          edgeOverrides: graphOverrides ?? manualEdgeOverrides
        });
      } else if (targetUsed === "graph" && effectiveGraphLayout === "regions") {
        setStatus("Generated an image-derived region graph. Use Open in solver to keep its exact topology.");
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Generation failed.");
    }
  }

  async function handleSave() {
    if (!generatedText || !generatedName) {
      return;
    }
    try {
      setSaveError(null);
      const res = await savePuzzle({
        name: generatedName,
        text: generatedText,
        overwrite: false,
        metadata: {
          source_image: imageName,
          image_size: imageSize,
          generated: "image_import"
        }
      });
      setSaveStatus(`Saved to ${res.path}`);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleSaveTemplate() {
    if (!file || !imageDims) {
      setTemplateStatus("Upload an image first.");
      return;
    }
    const crop = getCropPixels();
    if (!crop) {
      setTemplateStatus("Select a crop before saving a template.");
      return;
    }
    try {
      const previewCanvas = document.createElement("canvas");
      const ctx = previewCanvas.getContext("2d");
      if (ctx && imgRef.current) {
        const maxW = 360;
        const scale = Math.min(1, maxW / imgRef.current.naturalWidth);
        previewCanvas.width = imgRef.current.naturalWidth * scale;
        previewCanvas.height = imgRef.current.naturalHeight * scale;
        ctx.drawImage(imgRef.current, 0, 0, previewCanvas.width, previewCanvas.height);
      }
      const preview = previewCanvas.toDataURL("image/png").split(",")[1];
      await saveCropTemplate({
        name: templateName || `template-${Date.now()}`,
        image_width: imageDims.width,
        image_height: imageDims.height,
        crop,
        note: templateNote,
        preview_png_base64: preview,
        pipeline: {
          classifier: pipelineUseClassifier,
          ocr: pipelineUseOcr,
          grid: pipelineUseGrid,
          terminals: pipelineUseTerminals,
          ocr_full: ocrWholeImage
        }
      });
      setTemplateStatus("Template saved.");
      await refreshTemplates();
    } catch (err) {
      setTemplateStatus(err instanceof Error ? err.message : "Failed to save template.");
    }
  }

  async function handleApplyTemplate() {
    const tmpl = templates.find((t) => t.id === templateId);
    if (!tmpl) {
      return;
    }
    applyTemplateCrop(tmpl);
  }

  async function handleRunPipeline(cropOverride?: CropPixels) {
    if (!file) {
      setPipelineStatus("Upload an image first.");
      return;
    }
    if (parsedEdgeOverrides.error) {
      setPipelineStatus(parsedEdgeOverrides.error);
      return;
    }
    setPipelineBusy(true);
    setPipelineStatus(null);
    setPipelineChecks({
      classify: pipelineUseClassifier ? "pending" : "skipped",
      ocr: pipelineUseOcr ? "pending" : "skipped",
      grid: pipelineUseGrid ? "pending" : "skipped",
      terminals: pipelineUseTerminals ? "pending" : "skipped"
    });
    try {
      let crop = cropOverride ?? getCropPixels();
      if (!crop) {
        const tmpl = templates.find((t) => t.id === templateId);
        const templateCrop = tmpl ? templateCropPixels(tmpl) : null;
        if (templateCrop) {
          crop = templateCrop;
        }
      }
      if (!crop) {
        setPipelineStatus("Select a crop before running the pipeline.");
        return;
      }
      let rows = gridDetection?.rows ?? gridHeight;
      let cols = gridDetection?.cols ?? gridWidth;
      let terminals: Array<TerminalPayload> = [];
      let nodeTerminals: Array<NodeTerminalPayload> = [];
      let suggestedName: string | null = ocrSuggested;
      let detectedLevelType: LevelType | null = levelType;
      let ocrRawText = ocrText;
      let rawTopology: { name: string; text: string } | null = null;

      if (pipelineUseClassifier) {
        const classified = await handleClassifyLevel(crop);
        if (classified) {
          detectedLevelType = classified;
          setPipelineChecks((prev) => ({ ...prev, classify: "ok" }));
        } else {
          setPipelineChecks((prev) => ({ ...prev, classify: "fail" }));
        }
      } else {
        setPipelineChecks((prev) => ({ ...prev, classify: "skipped" }));
      }

      if (pipelineUseOcr) {
        const ocrRes = await imageOcr({
          file,
          crop: ocrWholeImage ? null : crop,
          perspective
        });
        const suggested = ocrRes.suggested_name ?? null;
        setOcrText(ocrRes.text || ocrRes.message || "");
        ocrRawText = ocrRes.text || ocrRes.message || "";
        setOcrSuggested(suggested);
        suggestedName = suggested;
        setPipelineChecks((prev) => ({
          ...prev,
          ocr: ocrRes.message ? "fail" : "ok"
        }));
        if (suggested) {
          onSuggestedName?.(suggested);
        }
      } else {
        setPipelineChecks((prev) => ({ ...prev, ocr: "skipped" }));
      }

      let builderType = choosePipelineBuilderType(targetType, detectedLevelType);
      let effectiveGraphLayout = graphLayoutForDetection(detectedLevelType, graphLayout);
      let gridDrivenTarget =
        builderType === "square" ||
        builderType === "hex" ||
        builderType === "circle" ||
        (builderType === "graph" && effectiveGraphLayout === "grid");
      let topologyDrivenTarget =
        builderType === "cube" ||
        builderType === "star" ||
        builderType === "figure8" ||
        (builderType === "graph" && effectiveGraphLayout !== "grid");

      if (topologyDrivenTarget) {
        const sizeHint = parseSizeHint(ocrRawText) ?? parseSizeHint(suggestedName) ?? parseSizeHint(imageName);
        if (sizeHint) {
          cols = sizeHint.cols;
          rows = sizeHint.rows;
          setGridWidth(cols);
          setGridHeight(rows);
        }
        setGridDetection(null);
        setGridStatus(`Topology size hint: ${cols}x${rows}.`);
      }

      if (pipelineUseGrid && gridDrivenTarget) {
        const gridRes = await imageDetectGrid({
          file,
          targetType: builderType,
          threshold,
          lineThreshold,
          invert,
          crop,
          perspective
        });
        if (!gridRes.grid) {
          if (targetType !== "auto") {
            setPipelineStatus(gridRes.message ?? "Grid detection failed.");
            setPipelineChecks((prev) => ({ ...prev, grid: "fail" }));
            return;
          }
          // Unknown silhouettes frequently classify as their dominant line
          // orientation (square/hex). Preserve their exact cells by deriving
          // the region adjacency graph when that detector cannot find a grid.
          builderType = "graph";
          effectiveGraphLayout = "regions";
          gridDrivenTarget = false;
          topologyDrivenTarget = true;
          setGridDetection(null);
          setGridStatus("No regular grid found; using free-form region detection.");
          setPipelineChecks((prev) => ({ ...prev, grid: "skipped" }));
        } else {
          rows = gridRes.grid.rows;
          cols = gridRes.grid.cols;
          setGridWidth(cols);
          setGridHeight(rows);
          setGridDetection({ rows, cols });
          const mode = String(gridRes.grid.mode ?? "rect");
          if (mode === "circle") {
            setGridStatus(`Detected circle grid ${cols} sectors x ${rows} rings.`);
          } else {
            setGridStatus(
              `Detected ${cols}x${rows} grid (lines: ${gridRes.grid.vertical_lines}x${gridRes.grid.horizontal_lines}).`
            );
          }
          setPipelineChecks((prev) => ({ ...prev, grid: "ok" }));
        }
      } else {
        setPipelineChecks((prev) => ({ ...prev, grid: "skipped" }));
      }

      if (pipelineUseTerminals && gridDrivenTarget) {
        const termRes = await imageDetectTerminals({
          file,
          targetType: builderType,
          rows,
          cols,
          satThreshold,
          brightnessMin,
          brightnessMax,
          marginRatio,
          clusterThreshold,
          bgThreshold,
          crop,
          perspective
        });
        terminals = termRes.terminals.map((t) => ({ row: t.row, col: t.col, letter: t.letter, color: t.color }));
        setTerminalDetections(termRes.terminals);
        const warnings = termRes.info?.warnings?.length ? termRes.info.warnings.join(" ") : "No warnings.";
        setTerminalStatus(`Detected ${termRes.terminals.length} terminals. ${warnings}`);
        setPipelineChecks((prev) => ({ ...prev, terminals: "ok" }));
      } else if ((pipelineUseTerminals || topologyDrivenTarget) && !gridDrivenTarget) {
        const graphRes = await imageGenerate({
          file,
          targetType: builderType,
          gridWidth: cols,
          gridHeight: rows,
          graphLayout: effectiveGraphLayout,
          graphNodes,
          autoTerminals: pipelineUseTerminals,
          autoClassify: false,
          levelType: detectedLevelType,
          edgeOverrides: manualEdgeOverrides,
          metadata: {
            source: "image-import"
          },
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
        if (!onApplyGrid || (builderType === "graph" && effectiveGraphLayout === "regions")) {
          rawTopology = { name: graphRes.name, text: graphRes.text };
          setGeneratedName(graphRes.name);
          setGeneratedText(graphRes.text);
        }
        const detected = graphRes.detection ?? {};
        const modifierInfo = (detected.modifier_info as { topology?: { width_hint?: number; height_hint?: number } } | undefined)?.topology;
        if (modifierInfo?.width_hint && modifierInfo?.height_hint) {
          cols = Number(modifierInfo.width_hint);
          rows = Number(modifierInfo.height_hint);
          if (Number.isFinite(cols) && Number.isFinite(rows) && cols > 0 && rows > 0) {
            setGridWidth(cols);
            setGridHeight(rows);
          }
        }
        const detectedRaw = Array.isArray(detected.terminals)
          ? (detected.terminals as Array<{
              row?: number;
              col?: number;
              node_id?: string;
              letter?: string;
              color?: number[];
            }>)
          : [];
        terminals = detectedRaw
          .filter(
            (terminal) =>
              Number.isFinite(terminal.row) &&
              Number.isFinite(terminal.col) &&
              typeof terminal.letter === "string"
          )
          .map((terminal) => ({
            row: Number(terminal.row),
            col: Number(terminal.col),
            letter: String(terminal.letter),
            color: terminal.color
          }));
        nodeTerminals = detectedRaw
          .filter((terminal) => typeof terminal.node_id === "string" && terminal.node_id && typeof terminal.letter === "string")
          .map((terminal) => ({
            nodeId: String(terminal.node_id),
            letter: String(terminal.letter),
            color: terminal.color
          }));
        if (terminals.length) {
          setTerminalDetections(terminals.map((terminal) => ({ ...terminal, color: terminal.color ?? [] })));
        } else {
          setTerminalDetections([]);
        }
        const warningList = Array.isArray(detected.warnings) ? detected.warnings.map(String).filter(Boolean) : [];
        const terminalInfoWarnings = Array.isArray((detected.terminal_info as { warnings?: unknown[] } | undefined)?.warnings)
          ? ((detected.terminal_info as { warnings?: unknown[] }).warnings ?? []).map(String).filter(Boolean)
          : [];
        const allWarnings = [...warningList, ...terminalInfoWarnings].filter(Boolean);
        if (pipelineUseTerminals) {
          if (nodeTerminals.length > 0 || terminals.length > 0) {
            setPipelineChecks((prev) => ({ ...prev, terminals: "ok" }));
            const count = nodeTerminals.length || terminals.length;
            const suffix = allWarnings.length ? ` ${allWarnings.join(" ")}` : "";
            setTerminalStatus(`Detected ${count} terminals.${suffix}`);
          } else {
            setPipelineChecks((prev) => ({ ...prev, terminals: "fail" }));
            setTerminalStatus(allWarnings.join(" ") || "No topology terminals were confidently detected.");
          }
        } else {
          setPipelineChecks((prev) => ({ ...prev, terminals: "skipped" }));
          setTerminalStatus("Terminal detection skipped.");
        }
      } else {
        setPipelineChecks((prev) => ({ ...prev, terminals: "skipped" }));
      }

      if (!onApplyGrid && !rawTopology) {
        const generated = await imageGenerate({
          file,
          targetType: builderType,
          gridWidth: cols,
          gridHeight: rows,
          graphLayout: effectiveGraphLayout,
          graphNodes,
          autoTerminals: pipelineUseTerminals,
          autoClassify: false,
          levelType: detectedLevelType,
          edgeOverrides: manualEdgeOverrides,
          metadata: { source: "image-import" },
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
        rawTopology = { name: generated.name, text: generated.text };
        setGeneratedName(nameForPuzzleText(suggestedName, generated.name, generated.text));
        setGeneratedText(generated.text);
      }

      if (rawTopology && onApplyGrid) {
        onGenerated(rawTopology.name, rawTopology.text);
      } else if (onApplyGrid) {
        onApplyGrid({
          type: builderType,
          rows,
          cols,
          terminals,
          nodeTerminals,
          suggestedName,
          levelType: detectedLevelType,
          edgeOverrides: manualEdgeOverrides
        });
        onApplied?.();
      } else if (rawTopology) {
        // Standalone import: hand the detected puzzle straight to the solver.
        onGenerated(nameForPuzzleText(suggestedName, rawTopology.name, rawTopology.text), rawTopology.text);
      }
      const classificationNote = detectedLevelType ? ` (${detectedLevelType.geometry})` : "";
      setPipelineStatus(
        rawTopology
          ? "Screenshot processed. Opening the solver…"
          : `Pipeline applied to builder${classificationNote}.`
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Pipeline failed.";
      setPipelineChecks((prev) => ({
        classify: prev.classify === "pending" ? "fail" : prev.classify,
        ocr: prev.ocr === "pending" ? "fail" : prev.ocr,
        grid: prev.grid === "pending" ? "fail" : prev.grid,
        terminals: prev.terminals === "pending" ? "fail" : prev.terminals
      }));
      setPipelineStatus(message);
      void archiveImageImportFailure({ file, error: message, stage: "single-image-pipeline" }).catch(
        () => undefined
      );
    } finally {
      setPipelineBusy(false);
    }
  }

  async function handleProcessClick() {
    if (!file || pipelineBusy) {
      return;
    }
    let override: CropPixels | undefined;
    if (isFullFrameCrop()) {
      const autoCropped = await handleAutoCrop();
      if (autoCropped) {
        override = autoCropped;
      }
    }
    await handleRunPipeline(override);
  }

  const handleImageLoaded = () => {
    if (!imgRef.current) {
      return;
    }
    setImageDims({
      width: imgRef.current.naturalWidth,
      height: imgRef.current.naturalHeight
    });
    setCrop(DEFAULT_CROP);
    if (autoProcess && imageSrc && autoRanForSrcRef.current !== imageSrc && !pipelineBusy) {
      autoRanForSrcRef.current = imageSrc;
      void (async () => {
        const autoCropped = await handleAutoCrop();
        await handleRunPipeline(autoCropped ?? undefined);
      })();
    }
  };

  const updateBatchItem = useCallback((id: string, patch: Partial<BatchItem>) => {
    setBatchItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  // Full per-image pipeline: auto-crop -> classify -> OCR -> generate -> solve.
  async function processBatchItem(item: BatchItem): Promise<Partial<BatchItem>> {
    const batchFile = item.file;
    let crop: CropPixels | null = null;
    try {
      const res = await imageAutoCrop({ file: batchFile, threshold, invert, padding, crop: null });
      crop = res.crop ?? null;
    } catch {
      // fall back to the full frame
    }

    let detected: LevelType | null = null;
    if (pipelineUseClassifier) {
      try {
        const res = await imageClassify({
          file: batchFile,
          threshold,
          lineThreshold,
          invert,
          perspective,
          levelHint: batchFile.name,
          crop
        });
        detected = res.level_type;
      } catch {
        // classification is best-effort in batch mode
      }
    }

    let suggested: string | null = null;
    if (pipelineUseOcr) {
      try {
        const ocr = await imageOcr({ file: batchFile, crop: ocrWholeImage ? null : crop, perspective });
        suggested = ocr.suggested_name ?? null;
      } catch {
        // OCR is best-effort in batch mode
      }
    }

    // Detect the real cell grid before generating, exactly like the single-image
    // pipeline — generating with the default 10x10 produces malformed boards.
    let builderType = choosePipelineBuilderType(targetType, detected);
    let effectiveGraphLayout = graphLayoutForDetection(detected, graphLayout);
    const gridDrivenTarget =
      builderType === "square" ||
      builderType === "hex" ||
      builderType === "circle" ||
      (builderType === "graph" && effectiveGraphLayout === "grid");
    let detectedCols = gridWidth;
    let detectedRows = gridHeight;
    if (pipelineUseGrid && gridDrivenTarget) {
      const gridRes = await imageDetectGrid({
        file: batchFile,
        targetType: builderType,
        threshold,
        lineThreshold,
        invert,
        crop,
        perspective
      });
      if (!gridRes.grid) {
        if (targetType !== "auto") {
          throw new Error(gridRes.message ?? "Grid detection failed.");
        }
        builderType = "graph";
        effectiveGraphLayout = "regions";
      } else {
        detectedCols = gridRes.grid.cols;
        detectedRows = gridRes.grid.rows;
      }
    }

    const gen = await imageGenerate({
      file: batchFile,
      replaceImportId: item.archiveImportId,
      targetType: builderType,
      gridWidth: detectedCols,
      gridHeight: detectedRows,
      graphLayout: effectiveGraphLayout,
      graphNodes,
      autoTerminals: pipelineUseTerminals,
      autoClassify: !detected,
      levelType: detected,
      metadata: { source: "image-import", source_image: batchFile.name },
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
    const name = nameForPuzzleText(suggested, gen.name, gen.text);
    const grid = gen.detection?.grid as { rows?: number; cols?: number } | undefined;
    const detectedTerminals = Array.isArray(gen.detection?.terminals)
      ? (gen.detection?.terminals as unknown[]).length
      : undefined;
    const geometry =
      (gen.detection?.level_type as LevelType | undefined)?.geometry ?? detected?.geometry;
    const sizeLabel = grid?.cols && grid?.rows ? `${grid.cols}×${grid.rows}` : undefined;
    const base: Partial<BatchItem> = {
      name,
      text: gen.text,
      geometry,
      sizeLabel,
      endpoints: detectedTerminals
    };

    try {
      const solve = await solvePuzzle({
        name,
        text: gen.text,
        fill: true,
        solver: "z3",
        timeout_ms: 30000,
        import_id: gen.import_id
      });
      const totalMsRaw = solve.stats?.total_ms;
      return {
        ...base,
        status: "solved",
        solve,
        solveMs: typeof totalMsRaw === "number" ? totalMsRaw : null
      };
    } catch (err) {
      const initialMessage = err instanceof Error ? err.message : "unknown error";
      const canRetryAsRegions =
        targetType === "auto" &&
        !(builderType === "graph" && effectiveGraphLayout === "regions") &&
        /(validation failed|\bunsat\b|no solution found|cannot reach each other)/i.test(initialMessage);
      if (canRetryAsRegions) {
        try {
          const recovery = await imageGenerate({
            file: batchFile,
            replaceImportId: item.archiveImportId,
            targetType: "graph",
            gridWidth: detectedCols,
            gridHeight: detectedRows,
            graphLayout: "regions",
            graphNodes,
            autoTerminals: pipelineUseTerminals,
            autoClassify: !detected,
            levelType: detected,
            metadata: { source: "image-import", source_image: batchFile.name },
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
          const recoveryName = nameForPuzzleText(suggested, recovery.name, recovery.text);
          const recoveryTerminals = Array.isArray(recovery.detection?.terminals)
            ? (recovery.detection.terminals as unknown[]).length
            : undefined;
          const recoveredSolve = await solvePuzzle({
            name: recoveryName,
            text: recovery.text,
            fill: true,
            solver: "z3",
            timeout_ms: 30000,
            import_id: recovery.import_id
          });
          const recoveryMs = recoveredSolve.stats?.total_ms;
          return {
            name: recoveryName,
            text: recovery.text,
            geometry: "graph",
            endpoints: recoveryTerminals,
            status: "solved",
            solve: recoveredSolve,
            solveMs: typeof recoveryMs === "number" ? recoveryMs : null
          };
        } catch (recoveryError) {
          const recoveryMessage =
            recoveryError instanceof Error ? recoveryError.message : "unknown recovery error";
          return {
            ...base,
            status: "detected",
            error: `Detected, but solving failed: ${initialMessage}. Region recovery also failed: ${recoveryMessage}`
          };
        }
      }
      return {
        ...base,
        status: "detected",
        error: `Detected, but solving failed: ${initialMessage}`
      };
    }
  }

  // Each item runs several backend calls (classify/OCR/grid/terminals/solve),
  // all threadpool-friendly on the server, so a small worker pool cuts batch
  // wall-clock roughly by the pool size without saturating a single node.
  const BATCH_CONCURRENCY = 3;

  async function runBatch(targets: BatchItem[]) {
    if (!targets.length || batchBusy) {
      return;
    }
    setBatchBusy(true);
    try {
      const queue = [...targets];
      const processOne = async (item: BatchItem) => {
        updateBatchItem(item.id, { status: "processing", error: undefined });
        try {
          const patch = await processBatchItem(item);
          updateBatchItem(item.id, patch);
        } catch (err) {
          const message = err instanceof Error ? err.message : "Pipeline failed.";
          updateBatchItem(item.id, {
            status: "error",
            error: message
          });
          const archiveFailure = item.archiveImportId
            ? recordImageImportReprocessFailure({
                importId: item.archiveImportId,
                error: message,
                stage: "screenshot-library"
              })
            : archiveImageImportFailure({ file: item.file, error: message, stage: "screenshot-batch" });
          await archiveFailure.catch(() => undefined);
        }
      };
      const workers = Array.from({ length: Math.min(BATCH_CONCURRENCY, queue.length) }, async () => {
        for (let item = queue.shift(); item; item = queue.shift()) {
          await processOne(item);
        }
      });
      await Promise.all(workers);
    } finally {
      setBatchBusy(false);
    }
  }

  const addBatchSources = (sources: BatchSource[]) => {
    // Batch replaces the single-image workflow; clear that state.
    selectImageFile(null);
    const newItems: BatchItem[] = sources.map(({ file: batchFile, archiveImportId }, idx) => {
      const url = URL.createObjectURL(batchFile);
      batchUrlsRef.current.push(url);
      return {
        id: `${batchFile.name}-${batchFile.size}-${batchFile.lastModified}-${Date.now()}-${idx}`,
        file: batchFile,
        previewUrl: url,
        status: "queued",
        name: batchFile.name.replace(/\.(png|jpe?g|webp)$/i, ""),
        saveState: "idle",
        archiveImportId
      };
    });
    setBatchItems((prev) => [...prev, ...newItems]);
    if (autoProcess) {
      void runBatch(newItems);
    }
  };

  const addBatchFiles = (files: File[]) => {
    addBatchSources(files.map((batchFile) => ({ file: batchFile })));
  };

  const clearBatch = () => {
    batchUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    batchUrlsRef.current = [];
    setBatchItems([]);
  };

  // Route incoming files: several at once (or adding while a batch exists)
  // goes to the batch list; a lone file uses the guided single-image flow.
  const handleFilesSelected = (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []).filter(
      (candidate) => candidate.type.startsWith("image/") || candidate.type === ""
    );
    if (!files.length) {
      return;
    }
    if (allowBatch && (files.length > 1 || batchMode)) {
      addBatchFiles(files);
    } else {
      selectImageFile(files[0]);
    }
  };

  async function handleBatchSave(item: BatchItem) {
    if (!item.text) {
      return;
    }
    updateBatchItem(item.id, { saveState: "saving", saveMessage: undefined });
    try {
      const res = await savePuzzle({
        name: item.name,
        text: item.text,
        overwrite: false,
        metadata: {
          source_image: item.file.name,
          generated: "image_import"
        }
      });
      updateBatchItem(item.id, { saveState: "saved", saveMessage: `Saved to ${res.path}` });
    } catch (err) {
      updateBatchItem(item.id, {
        saveState: "error",
        saveMessage: err instanceof Error ? err.message : "Save failed."
      });
    }
  }

  async function handleBatchSaveAll() {
    const targets = batchItems.filter(
      (item) => item.text && item.saveState !== "saved" && item.saveState !== "saving"
    );
    for (const item of targets) {
      await handleBatchSave(item);
    }
  }

  // Queue archived screenshots handed off from the library's uploaded
  // screenshots page into the batch pipeline.
  useEffect(() => {
    if (!reprocessRequest || embedded || reprocessRequest.token === handledReprocessTokenRef.current) {
      return;
    }
    handledReprocessTokenRef.current = reprocessRequest.token;
    const { entries } = reprocessRequest;
    // Consume the request so remounting the importer doesn't re-queue it. The
    // download continues below regardless; the token ref guards re-runs.
    onReprocessHandled?.();
    void (async () => {
      setReprocessProgress({ current: 0, total: entries.length });
      setReprocessError(null);
      const sources: BatchSource[] = [];
      const failures: string[] = [];
      for (let index = 0; index < entries.length; index += 1) {
        const entry = entries[index];
        try {
          sources.push({ file: await fetchImageImportFile(entry), archiveImportId: entry.id });
        } catch (err) {
          failures.push(`${entry.original_name}: ${err instanceof Error ? err.message : "download failed"}`);
        }
        setReprocessProgress({ current: index + 1, total: entries.length });
      }
      setReprocessProgress(null);
      setReprocessError(
        failures.length
          ? `${failures.length} screenshot${failures.length === 1 ? "" : "s"} could not be queued. ${failures[0]}`
          : null
      );
      if (sources.length) {
        addBatchSources(sources);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reprocessRequest, embedded]);

  async function handleDeleteTemplate() {
    if (!templateId) {
      return;
    }
    try {
      await deleteCropTemplate(templateId);
      setTemplateId("");
      await refreshTemplates();
    } catch (err) {
      setTemplateStatus(err instanceof Error ? err.message : "Failed to delete template.");
    }
  }

  async function handleOcr() {
    if (!file) {
      return;
    }
    try {
      const res = await imageOcr({
        file,
        crop: ocrWholeImage ? null : getCropPixels(),
        perspective
      });
      const suggested = res.suggested_name ?? null;
      setOcrText(res.text || res.message || "");
      setOcrSuggested(suggested);
      if (suggested) {
        onSuggestedName?.(suggested);
      }
    } catch (err) {
      setOcrText(err instanceof Error ? err.message : "OCR failed.");
      setOcrSuggested(null);
    }
  }

  const applyDetectedToBuilder = () => {
    if (!onApplyGrid) {
      return;
    }
    if (!gridDetection && (gridWidth <= 0 || gridHeight <= 0)) {
      return;
    }
    const rows = gridDetection?.rows ?? gridHeight;
    const cols = gridDetection?.cols ?? gridWidth;
    const terminals = terminalDetections.map((t) => ({ row: t.row, col: t.col, letter: t.letter, color: t.color }));
    const builderType = getBuilderType(targetType, levelType);
    onApplyGrid({
      type: builderType,
      rows,
      cols,
      terminals,
      suggestedName: ocrSuggested,
      levelType,
      edgeOverrides: manualEdgeOverrides
    });
  };

  const pipelineStatusChips = (
    <Box display="flex" flexWrap="wrap" gap={1}>
      {[
        ["Board type", pipelineChecks.classify],
        ["Level name", pipelineChecks.ocr],
        ["Board cells", pipelineChecks.grid],
        ["Color pairs", pipelineChecks.terminals]
      ].map(([label, state]) => {
        const icon =
          state === "ok" ? (
            <CheckCircle fontSize="small" />
          ) : state === "fail" ? (
            <Cancel fontSize="small" />
          ) : state === "pending" ? (
            <HourglassEmpty fontSize="small" />
          ) : (
            <RemoveCircleOutline fontSize="small" />
          );
        const color =
          state === "ok" ? "success" : state === "fail" ? "error" : state === "pending" ? "warning" : "default";
        return (
          <Chip
            key={label as string}
            label={`${label}`}
            icon={icon}
            color={color as "success" | "error" | "warning" | "default"}
            size="small"
          />
        );
      })}
    </Box>
  );

  const advancedSections = (
    <Stack spacing={2}>
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Processing steps
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={1}>
            Turn off a step only when you plan to enter that information manually.
          </Typography>
          <Box display="flex" flexWrap="wrap" gap={1}>
            <FormControlLabel
              control={<Switch checked={pipelineUseClassifier} onChange={(event) => setPipelineUseClassifier(event.target.checked)} />}
              label="Detect type"
            />
            <FormControlLabel
              control={<Switch checked={pipelineUseOcr} onChange={(event) => setPipelineUseOcr(event.target.checked)} />}
              label="Read level name"
            />
            <FormControlLabel
              control={<Switch checked={pipelineUseGrid} onChange={(event) => setPipelineUseGrid(event.target.checked)} />}
              label="Detect cells"
            />
            <FormControlLabel
              control={<Switch checked={pipelineUseTerminals} onChange={(event) => setPipelineUseTerminals(event.target.checked)} />}
              label="Detect colors"
            />
          </Box>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Crop templates
          </Typography>
          <Stack spacing={2}>
            <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
              <TextField
                label="Template"
                select
                value={templateId}
                onChange={(event) => setTemplateId(event.target.value)}
                size="small"
                sx={{ minWidth: 220 }}
              >
                <MenuItem value="">Select template</MenuItem>
                {templates.map((t) => (
                  <MenuItem key={t.id} value={t.id}>
                    {t.name}
                  </MenuItem>
                ))}
              </TextField>
              <Button variant="outlined" onClick={handleApplyTemplate} disabled={!templateId}>
                Apply
              </Button>
              <Button variant="outlined" color="error" onClick={handleDeleteTemplate} disabled={!templateId}>
                Delete
              </Button>
              <Button variant="outlined" onClick={refreshTemplates}>
                Refresh
              </Button>
            </Box>
            {templateId && (
              <Box display="flex" gap={2} flexWrap="wrap" alignItems="center">
                <Box
                  component="img"
                  src={cropTemplatePreviewUrl(templateId)}
                  alt="Template preview"
                  sx={{ width: 160, borderRadius: 1, border: "1px solid rgba(255,255,255,0.1)" }}
                />
                <Typography variant="caption" color="text.secondary">
                  Template preview
                </Typography>
              </Box>
            )}
            <Divider />
            <Typography variant="subtitle2">Save current crop as template</Typography>
            <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
              <TextField
                label="Template name"
                value={templateName}
                onChange={(event) => setTemplateName(event.target.value)}
                size="small"
                sx={{ minWidth: 220 }}
              />
              <TextField
                label="Note"
                value={templateNote}
                onChange={(event) => setTemplateNote(event.target.value)}
                size="small"
                sx={{ minWidth: 220 }}
              />
              <Button variant="outlined" onClick={handleSaveTemplate} disabled={!file}>
                Save template
              </Button>
            </Box>
            {templateStatus && (
              <Alert severity={templateStatus.includes("fail") ? "error" : "info"}>{templateStatus}</Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Crop tools
          </Typography>
          <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
            <TextField
              label="Threshold"
              type="number"
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
              size="small"
            />
            <TextField
              label="Padding"
              type="number"
              value={padding}
              onChange={(event) => setPadding(Number(event.target.value))}
              size="small"
            />
            <FormControlLabel
              control={<Switch checked={invert} onChange={(event) => setInvert(event.target.checked)} />}
              label="Invert"
            />
            <FormControlLabel
              control={<Switch checked={perspective} onChange={(event) => setPerspective(event.target.checked)} />}
              label="Auto perspective"
            />
            <FormControlLabel
              control={<Switch checked={ocrWholeImage} onChange={(event) => setOcrWholeImage(event.target.checked)} />}
              label="OCR full screen"
            />
            <Button variant="outlined" onClick={handleOcr} disabled={!file}>
              OCR level name
            </Button>
          </Stack>
          {(ocrText || ocrSuggested) && (
            <Box mt={2}>
              <Typography variant="body2" color="text.secondary">
                {ocrText || "Run OCR to detect a level name or number."}
              </Typography>
              {ocrSuggested && (
                <Box mt={1} display="flex" gap={2} alignItems="center">
                  <Typography variant="caption">Suggested name: {ocrSuggested}</Typography>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => {
                      setGeneratedName(ocrSuggested);
                      onSuggestedName?.(ocrSuggested);
                    }}
                  >
                    Use name
                  </Button>
                </Box>
              )}
            </Box>
          )}
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Feature extraction
          </Typography>
          <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
            <TextField
              label="Line threshold"
              type="number"
              inputProps={{ min: 0, max: 1, step: 0.05 }}
              value={lineThreshold}
              onChange={(event) => setLineThreshold(Number(event.target.value))}
              size="small"
            />
            <Button variant="outlined" onClick={() => void handleClassifyLevel()} disabled={!file}>
              Classify level
            </Button>
            <Button variant="outlined" onClick={handleDetectGrid} disabled={!file}>
              Auto-detect grid
            </Button>
            {onApplyGrid && (
              <Button variant="outlined" onClick={applyDetectedToBuilder} disabled={!gridDetection}>
                Apply to builder
              </Button>
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block">
            Line detection works best when grid lines are visible.
          </Typography>
          {levelTypeStatus && (
            <Typography variant="caption" color="text.secondary" display="block">
              {levelTypeStatus}
            </Typography>
          )}
          {gridStatus && (
            <Typography variant="caption" color="text.secondary" display="block">
              {gridStatus}
            </Typography>
          )}
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Terminal detection
          </Typography>
          <Box display="flex" flexWrap="wrap" gap={2}>
            <TextField
              label="Sat threshold"
              type="number"
              value={satThreshold}
              onChange={(event) => setSatThreshold(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <TextField
              label="Brightness min"
              type="number"
              value={brightnessMin}
              onChange={(event) => setBrightnessMin(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <TextField
              label="Brightness max"
              type="number"
              value={brightnessMax}
              onChange={(event) => setBrightnessMax(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <TextField
              label="Margin ratio"
              type="number"
              value={marginRatio}
              onChange={(event) => setMarginRatio(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <TextField
              label="Cluster threshold"
              type="number"
              value={clusterThreshold}
              onChange={(event) => setClusterThreshold(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <TextField
              label="BG threshold"
              type="number"
              value={bgThreshold}
              onChange={(event) => setBgThreshold(Number(event.target.value))}
              size="small"
              sx={{ width: 140 }}
            />
            <Button variant="outlined" onClick={handleDetectTerminals} disabled={!file}>
              Detect terminals
            </Button>
          </Box>
          {terminalStatus && <Typography variant="caption">{terminalStatus}</Typography>}
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Generate graph space
          </Typography>
          <Stack spacing={2}>
            {graphTarget ? (
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
                  <MenuItem value="regions">image regions</MenuItem>
                </TextField>
                {graphLayout === "line" ? (
                  <TextField
                    label="Nodes"
                    type="number"
                    value={graphNodes}
                    onChange={(event) => setGraphNodes(Number(event.target.value))}
                    size="small"
                  />
                ) : graphLayout === "grid" ? (
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
                ) : (
                  <Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>
                    Detect cells and adjacencies directly from enclosed image regions.
                  </Typography>
                )}
              </Stack>
            ) : topologyTarget ? (
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
                  helperText={gridStatus ? `Auto: ${gridWidth}x${gridHeight}` : undefined}
                />
                <TextField
                  label={targetType === "circle" ? "Rings" : "Grid height"}
                  type="number"
                  value={gridHeight}
                  onChange={(event) => setGridHeight(Number(event.target.value))}
                  size="small"
                  helperText={gridStatus ? `Auto: ${gridWidth}x${gridHeight}` : undefined}
                />
              </Stack>
            )}

            <FormControlLabel
              control={<Switch checked={autoTerminals} onChange={(event) => setAutoTerminals(event.target.checked)} />}
              label="Auto-detect terminals"
            />
            <FormControlLabel
              control={<Switch checked={autoClassify} onChange={(event) => setAutoClassify(event.target.checked)} />}
              label="Auto-classify level type"
            />

            <Typography variant="subtitle2">Manual graph edge overrides (optional)</Typography>
            <Typography variant="caption" color="text.secondary">
              One pair per line: <code>u v</code> or <code>u|v</code>. For grid graphs, node ids are <code>x,y</code>.
            </Typography>
            {parsedEdgeOverrides.error && <Alert severity="warning">{parsedEdgeOverrides.error}</Alert>}
            <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
              <TextField
                label="Add edges"
                value={edgeAddText}
                onChange={(event) => setEdgeAddText(event.target.value)}
                multiline
                minRows={3}
                size="small"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Remove edges"
                value={edgeRemoveText}
                onChange={(event) => setEdgeRemoveText(event.target.value)}
                multiline
                minRows={3}
                size="small"
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
              <TextField
                label="Warp edges"
                value={edgeWarpsText}
                onChange={(event) => setEdgeWarpsText(event.target.value)}
                multiline
                minRows={2}
                size="small"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Wall edges"
                value={edgeWallsText}
                onChange={(event) => setEdgeWallsText(event.target.value)}
                multiline
                minRows={2}
                size="small"
                sx={{ flex: 1 }}
              />
            </Stack>
            <Box display="flex" gap={2} flexWrap="wrap">
              <Button
                variant="text"
                onClick={() => {
                  setEdgeAddText("");
                  setEdgeRemoveText("");
                  setEdgeWarpsText("");
                  setEdgeWallsText("");
                }}
              >
                Clear edge overrides
              </Button>
              <Button variant="outlined" onClick={handleGenerate} disabled={!file}>
                Generate puzzle only
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      {(status || saveError) && <Alert severity={saveError ? "error" : "info"}>{saveError ?? status}</Alert>}
    </Stack>
  );

  return (
    <Stack spacing={2}>
      {!embedded && (
        <Card
          sx={{
            overflow: "hidden",
            background:
              "linear-gradient(135deg, rgba(255,82,82,0.18), rgba(130,177,255,0.1) 55%, rgba(22,26,34,0.95))"
          }}
        >
          <CardContent sx={{ py: { xs: 2.5, sm: 3 } }}>
            <Chip icon={<AutoAwesome />} label="Screenshot to solution" color="primary" size="small" sx={{ mb: 1.5 }} />
            <Typography variant="h5" gutterBottom>
              Solve a screenshot
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 620 }}>
              Drop in one or many screenshots. Each one is cropped, detected, and solved — a single
              screenshot opens straight in the solver, a batch shows every solution in a list.
            </Typography>
          </CardContent>
        </Card>
      )}

      {!embedded && reprocessProgress && (
        <Card>
          <CardContent sx={{ py: 2 }}>
            <LinearProgress
              variant="determinate"
              value={(reprocessProgress.current / Math.max(1, reprocessProgress.total)) * 100}
            />
            <Typography variant="caption" color="text.secondary">
              Loading {reprocessProgress.current} of {reprocessProgress.total} archived screenshots…
            </Typography>
          </CardContent>
        </Card>
      )}
      {!embedded && reprocessError && <Alert severity="warning">{reprocessError}</Alert>}

      <Card>
        <CardContent>
          <Stack spacing={2}>
            <Box>
              <Typography variant="overline" color="primary.main" fontWeight={700}>
                Step 1
              </Typography>
              <Typography variant="h6">Choose the screenshot</Typography>
            </Box>
            <Box
              onDragEnter={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setIsDragging(false);
                handleFilesSelected(event.dataTransfer.files);
              }}
              sx={{
                border: "1.5px dashed",
                borderColor: isDragging ? "primary.main" : "rgba(255,255,255,0.2)",
                backgroundColor: isDragging ? "rgba(255,82,82,0.08)" : "rgba(255,255,255,0.025)",
                borderRadius: 2,
                p: { xs: 2, sm: 3 },
                textAlign: "center",
                transition: "160ms ease"
              }}
            >
              <UploadFile sx={{ fontSize: 34, color: "text.secondary", mb: 0.5 }} />
              <Typography variant="subtitle1" fontWeight={650}>
                {batchMode
                  ? `${batchItems.length} screenshot${batchItems.length === 1 ? "" : "s"} in the batch`
                  : file
                    ? imageName
                    : allowBatch
                      ? "Drop one or more screenshots here"
                      : "Drop a screenshot here"}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1.5 }}>
                {allowBatch ? "PNG, JPEG, or WebP — select several to solve them in bulk" : "PNG, JPEG, or WebP"}
              </Typography>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} justifyContent="center">
                <Button
                  component="label"
                  variant={file || batchMode ? "outlined" : "contained"}
                  startIcon={<UploadFile />}
                >
                  {batchMode ? "Add screenshots" : file ? "Replace screenshot" : "Choose screenshots"}
                  <input
                    hidden
                    type="file"
                    multiple={allowBatch}
                    accept="image/png,image/jpeg,image/webp,image/*"
                    onChange={(event) => {
                      handleFilesSelected(event.target.files);
                      event.target.value = "";
                    }}
                  />
                </Button>
                <Button component="label" variant="outlined" startIcon={<PhotoCamera />}>
                  Use camera
                  <input
                    hidden
                    type="file"
                    accept="image/*"
                    capture="environment"
                    onChange={(event) => {
                      handleFilesSelected(event.target.files);
                      event.target.value = "";
                    }}
                  />
                </Button>
              </Stack>
            </Box>
            <FormControlLabel
              control={<Switch checked={autoProcess} onChange={(event) => setAutoProcess(event.target.checked)} />}
              label="Process automatically after upload"
            />
            {imageSrc && (
              <Box>
                <Box display="flex" justifyContent="space-between" gap={1} alignItems="center" mb={1}>
                  <Box>
                    <Typography variant="overline" color="primary.main" fontWeight={700}>
                      Step 2
                    </Typography>
                    <Typography variant="subtitle1" fontWeight={650}>
                      Frame the board
                    </Typography>
                  </Box>
                  <Button size="small" variant="outlined" startIcon={<CropFree />} onClick={() => void handleAutoCrop()}>
                    Auto-crop
                  </Button>
                </Box>
                <ReactCrop
                  crop={crop}
                  onChange={(nextCrop) => setCrop(nextCrop)}
                  onComplete={(pixelCrop) => setCompletedCrop(pixelCrop)}
                  keepSelection
                  ruleOfThirds
                  style={{ maxWidth: "100%", width: "fit-content" }}
                >
                  <img
                    ref={imgRef}
                    alt="Crop preview"
                    src={imageSrc}
                    style={{
                      maxWidth: "100%",
                      maxHeight: 520,
                      width: "auto",
                      height: "auto",
                      display: "block"
                    }}
                    onLoad={handleImageLoaded}
                  />
                </ReactCrop>
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                  Auto-crop framed the board for you — adjust the handles if it missed, then process again.
                </Typography>
              </Box>
            )}
          </Stack>
        </CardContent>
      </Card>

      {batchMode && (
        <Card>
          <CardContent>
            <Stack spacing={2}>
              <Box display="flex" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={1}>
                <Box>
                  <Typography variant="overline" color="primary.main" fontWeight={700}>
                    Step 2
                  </Typography>
                  <Typography variant="h6">Solved puzzles</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Each screenshot is cropped, detected, and solved. Open any result in the solver or save it
                    to the library.
                  </Typography>
                </Box>
                <Box display="flex" gap={0.75} flexWrap="wrap">
                  {batchItems.filter((item) => item.status === "solved").length > 0 && (
                    <Chip
                      label={`${batchItems.filter((item) => item.status === "solved").length} solved`}
                      color="success"
                      size="small"
                    />
                  )}
                  {batchItems.filter((item) => item.status === "detected").length > 0 && (
                    <Chip
                      label={`${batchItems.filter((item) => item.status === "detected").length} unsolved`}
                      color="warning"
                      size="small"
                    />
                  )}
                  {batchItems.filter((item) => item.status === "error").length > 0 && (
                    <Chip
                      label={`${batchItems.filter((item) => item.status === "error").length} failed`}
                      color="error"
                      size="small"
                    />
                  )}
                  {batchItems.filter((item) => item.status === "queued" || item.status === "processing").length >
                    0 && (
                    <Chip
                      label={`${
                        batchItems.filter((item) => item.status === "queued" || item.status === "processing")
                          .length
                      } pending`}
                      size="small"
                    />
                  )}
                </Box>
              </Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <TextField
                  label="Board type"
                  select
                  value={targetType}
                  onChange={(event) => setTargetType(event.target.value as TargetType)}
                  size="small"
                  fullWidth
                >
                  <MenuItem value="auto">Detect automatically</MenuItem>
                  <MenuItem value="square">Square grid</MenuItem>
                  <MenuItem value="hex">Hex grid</MenuItem>
                  <MenuItem value="circle">Circular rings</MenuItem>
                  <MenuItem value="cube">Cube</MenuItem>
                  <MenuItem value="star">Radial star</MenuItem>
                  <MenuItem value="figure8">Figure eight</MenuItem>
                  <MenuItem value="graph">Custom / irregular</MenuItem>
                </TextField>
                <Button
                  variant="contained"
                  onClick={() =>
                    void runBatch(
                      batchItems.filter((item) => item.status === "queued" || item.status === "error")
                    )
                  }
                  disabled={
                    batchBusy ||
                    !batchItems.some((item) => item.status === "queued" || item.status === "error")
                  }
                  sx={{ whiteSpace: "nowrap", minWidth: 160 }}
                >
                  {batchBusy ? "Processing…" : "Process pending"}
                </Button>
              </Stack>
              {batchBusy && <LinearProgress />}
              <Stack spacing={1.5}>
                {batchItems.map((item) => {
                  const statusChip =
                    item.status === "solved" ? (
                      <Chip icon={<CheckCircle fontSize="small" />} label="Solved" color="success" size="small" />
                    ) : item.status === "detected" ? (
                      <Chip icon={<Cancel fontSize="small" />} label="Not solved" color="warning" size="small" />
                    ) : item.status === "error" ? (
                      <Chip icon={<Cancel fontSize="small" />} label="Failed" color="error" size="small" />
                    ) : item.status === "processing" ? (
                      <Chip icon={<HourglassEmpty fontSize="small" />} label="Processing" color="warning" size="small" />
                    ) : (
                      <Chip icon={<HourglassEmpty fontSize="small" />} label="Queued" size="small" />
                    );
                  return (
                    <Card key={item.id} variant="outlined" sx={{ borderColor: "rgba(255,255,255,0.12)" }}>
                      <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                          <Box
                            component="img"
                            src={item.previewUrl}
                            alt={item.file.name}
                            onClick={() => setLightbox({ url: item.previewUrl, alt: item.file.name })}
                            sx={{
                              width: { xs: 132, sm: 96 },
                              height: { xs: 132, sm: 96 },
                              objectFit: "contain",
                              bgcolor: "rgba(0,0,0,0.25)",
                              borderRadius: 1.5,
                              border: "1px solid rgba(255,255,255,0.1)",
                              flexShrink: 0,
                              cursor: "zoom-in",
                              alignSelf: { xs: "center", sm: "flex-start" }
                            }}
                          />
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Box display="flex" gap={0.75} flexWrap="wrap" alignItems="center">
                              {statusChip}
                              {item.geometry && <Chip label={item.geometry} size="small" variant="outlined" />}
                              {item.sizeLabel && <Chip label={item.sizeLabel} size="small" variant="outlined" />}
                              {typeof item.endpoints === "number" && item.endpoints > 0 && (
                                <Chip label={`${item.endpoints} endpoints`} size="small" variant="outlined" />
                              )}
                              {item.status === "solved" && item.solveMs !== null && item.solveMs !== undefined && (
                                <Chip label={`Solved in ${Math.max(1, Math.round(item.solveMs))} ms`} size="small" />
                              )}
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                noWrap
                                sx={{ ml: "auto", maxWidth: 180 }}
                                title={item.file.name}
                              >
                                {item.file.name}
                              </Typography>
                            </Box>
                            {item.status === "processing" && (
                              <Box display="flex" alignItems="center" gap={1} my={1.5}>
                                <CircularProgress size={16} />
                                <Typography variant="body2" color="text.secondary">
                                  Detecting board and solving…
                                </Typography>
                              </Box>
                            )}
                            {item.status === "solved" && item.solve && (
                              <Box my={1}>
                                <GameView
                                  graph={item.solve.graph}
                                  nodeColor={item.solve.node_color}
                                  pathEdges={item.solve.path_edges}
                                  paths={item.solve.paths}
                                  showSolution
                                  height={220}
                                />
                              </Box>
                            )}
                            {item.error && (
                              <Alert
                                severity={item.status === "error" ? "error" : "warning"}
                                sx={{ my: 1 }}
                              >
                                {item.error}
                              </Alert>
                            )}
                            {item.text && (
                              <>
                                <TextField
                                  label="Puzzle name"
                                  value={item.name}
                                  onChange={(event) => updateBatchItem(item.id, { name: event.target.value })}
                                  size="small"
                                  fullWidth
                                  sx={{ mt: 1 }}
                                />
                                {batchDuplicates.has(item.id) && (
                                  <Typography variant="caption" color="warning.main" display="block" mt={0.5}>
                                    Possible duplicate: {batchDuplicates.get(item.id)!.join("; ")}.
                                  </Typography>
                                )}
                                <Stack direction={{ xs: "column", sm: "row" }} spacing={1} mt={1.5}>
                                  <Button
                                    variant="contained"
                                    size="small"
                                    onClick={() => onGenerated(item.name, item.text!)}
                                  >
                                    Open in solver
                                  </Button>
                                  <Button
                                    variant="outlined"
                                    size="small"
                                    disabled={item.saveState === "saving" || item.saveState === "saved"}
                                    onClick={() => void handleBatchSave(item)}
                                  >
                                    {item.saveState === "saved"
                                      ? "Saved"
                                      : item.saveState === "saving"
                                        ? "Saving…"
                                        : "Save to library"}
                                  </Button>
                                </Stack>
                                {item.saveMessage && (
                                  <Typography
                                    variant="caption"
                                    color={item.saveState === "error" ? "error" : "text.secondary"}
                                    display="block"
                                    mt={0.5}
                                  >
                                    {item.saveMessage}
                                  </Typography>
                                )}
                              </>
                            )}
                          </Box>
                        </Stack>
                      </CardContent>
                    </Card>
                  );
                })}
              </Stack>
              <Box display="flex" gap={1.5} flexWrap="wrap">
                <Button
                  variant="outlined"
                  onClick={() => void handleBatchSaveAll()}
                  disabled={batchBusy || !batchItems.some((item) => item.text && item.saveState !== "saved")}
                >
                  Save all to library
                </Button>
                <Button variant="text" color="error" onClick={clearBatch} disabled={batchBusy}>
                  Clear batch
                </Button>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      )}

      <Modal
        open={Boolean(lightbox)}
        onClose={() => setLightbox(null)}
        sx={{ display: "flex", alignItems: "center", justifyContent: "center", p: 2 }}
      >
        <Box
          sx={{
            position: "relative",
            outline: "none",
            display: "flex",
            maxWidth: "100%",
            maxHeight: "100%"
          }}
        >
          <IconButton
            aria-label="Close preview"
            onClick={() => setLightbox(null)}
            sx={{
              position: "absolute",
              top: 8,
              right: 8,
              zIndex: 1,
              bgcolor: "error.main",
              color: "#fff",
              "&:hover": { bgcolor: "error.dark" }
            }}
          >
            <Close />
          </IconButton>
          <Box
            component="img"
            src={lightbox?.url}
            alt={lightbox?.alt ?? ""}
            sx={{
              maxWidth: "92vw",
              maxHeight: "92vh",
              objectFit: "contain",
              borderRadius: 1,
              boxShadow: 24
            }}
          />
        </Box>
      </Modal>

      {!batchMode && (
      <Card>
        <CardContent>
          <Stack spacing={2}>
            <Box>
              <Typography variant="overline" color="primary.main" fontWeight={700}>
                Step {imageSrc ? 3 : 2}
              </Typography>
              <Typography variant="h6">Detect and solve</Typography>
              <Typography variant="body2" color="text.secondary">
                Automatic works for most screenshots. Choose a board type only when detection needs a hint.
              </Typography>
            </Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField
                label="Board type"
                select
                value={targetType}
                onChange={(event) => setTargetType(event.target.value as TargetType)}
                size="small"
                fullWidth
              >
                <MenuItem value="auto">Detect automatically</MenuItem>
                <MenuItem value="square">Square grid</MenuItem>
                <MenuItem value="hex">Hex grid</MenuItem>
                <MenuItem value="circle">Circular rings</MenuItem>
                <MenuItem value="cube">Cube</MenuItem>
                <MenuItem value="star">Radial star</MenuItem>
                <MenuItem value="figure8">Figure eight</MenuItem>
                <MenuItem value="graph">Custom / irregular</MenuItem>
              </TextField>
              <TextField
                label="Crop preset (optional)"
                select
                value={templateId}
                onChange={(event) => setTemplateId(event.target.value)}
                size="small"
                fullWidth
              >
                <MenuItem value="">No preset</MenuItem>
                {templates.map((t) => (
                  <MenuItem key={t.id} value={t.id}>
                    {t.name}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
            {templateId && (
              <Button variant="text" size="small" onClick={handleApplyTemplate} sx={{ alignSelf: "flex-start" }}>
                Apply selected crop preset
              </Button>
            )}
            <Button
              variant="contained"
              size="large"
              fullWidth
              startIcon={<AutoAwesome />}
              onClick={() => void handleProcessClick()}
              disabled={!file || pipelineBusy}
              sx={{ minHeight: 48 }}
            >
              {pipelineBusy
                ? "Processing screenshot…"
                : onApplyGrid
                  ? "Process screenshot"
                  : "Process & solve"}
            </Button>
            {pipelineBusy && <LinearProgress />}
            {pipelineStatusChips}
            {pipelineStatus && (
              <Alert
                severity={
                  pipelineStatus.toLowerCase().includes("fail")
                    ? "error"
                    : pipelineChecks.terminals === "fail"
                      ? "warning"
                      : "success"
                }
              >
                {pipelineStatus}
              </Alert>
            )}
            {completedCrop && gridDetection && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Detection preview — detected cells and endpoints overlaid on your crop
                </Typography>
                <canvas
                  ref={previewCanvasRef}
                  style={{
                    width: "100%",
                    maxWidth: 420,
                    maxHeight: 260,
                    marginTop: 8,
                    borderRadius: 6,
                    border: "1px solid rgba(255,255,255,0.08)",
                    display: "block",
                    objectFit: "contain"
                  }}
                />
              </Box>
            )}
          </Stack>
        </CardContent>
      </Card>
      )}

      {generatedText && (
        <Card sx={{ borderColor: "success.main" }}>
          <CardContent>
            <Stack spacing={2}>
              <Box display="flex" gap={1} alignItems="center">
                <CheckCircle color="success" />
                <Box>
                  <Typography variant="h6">Puzzle ready</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Review the detected name, then open it in the solver or save it for later.
                  </Typography>
                </Box>
              </Box>
              <TextField
                label="Puzzle name"
                value={generatedName}
                onChange={(event) => setGeneratedName(event.target.value)}
                size="small"
                fullWidth
              />
              <Box display="flex" gap={1} flexWrap="wrap">
                {levelType?.geometry && <Chip label={levelType.geometry} size="small" />}
                {gridDetection && <Chip label={`${gridDetection.cols} × ${gridDetection.rows}`} size="small" />}
                {terminalDetections.length > 0 && (
                  <Chip label={`${terminalDetections.length} endpoints`} size="small" />
                )}
              </Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <Button
                  variant="contained"
                  fullWidth
                  onClick={() => onGenerated(generatedName, generatedText)}
                >
                  Open in solver
                </Button>
                <Button variant="outlined" fullWidth onClick={handleSave}>
                  Save to library
                </Button>
              </Stack>
              {saveStatus && <Alert severity="success">{saveStatus}</Alert>}
              {saveError && <Alert severity="error">{saveError}</Alert>}
            </Stack>
          </CardContent>
        </Card>
      )}

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMore />}>
          <Box display="flex" gap={1} alignItems="center">
            <TuneOutlined fontSize="small" color="secondary" />
            <Typography variant="subtitle1">Advanced import settings</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>{advancedSections}</AccordionDetails>
      </Accordion>

      {imageSrc && (
        <Typography variant="caption" color="text.secondary" textAlign="center">
          {imageName} · {imageSize}
        </Typography>
      )}
    </Stack>
  );
}
