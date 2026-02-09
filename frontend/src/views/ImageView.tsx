import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography
} from "@mui/material";
import {
  Cancel,
  CheckCircle,
  HourglassEmpty,
  RemoveCircleOutline
} from "@mui/icons-material";
import ReactCrop, { Crop, PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import {
  cropTemplatePreviewUrl,
  deleteCropTemplate,
  imageAutoCrop,
  imageClassify,
  imageDetectGrid,
  imageDetectTerminals,
  imageGenerate,
  imageOcr,
  LevelType,
  listCropTemplates,
  saveCropTemplate,
  savePuzzle
} from "../api";
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
  compact?: boolean;
};

type CropPixels = { x: number; y: number; width: number; height: number };

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

function graphEdgeOverridesFromText(text: string): EdgeOverrides | null {
  try {
    const obj = JSON.parse(text) as {
      space?: {
        edge_overrides?: { add?: Array<[string, string]>; remove?: Array<[string, string]> };
        warps?: Array<[string, string]>;
        walls?: Array<[string, string]>;
      };
    };
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

export function ImageView({ onGenerated, onSuggestedName, onApplyGrid, compact = false }: ImageViewProps) {
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
  const [targetType, setTargetType] = useState<TargetType>("auto");
  const [graphLayout, setGraphLayout] = useState<"grid" | "line">("grid");
  const [graphNodes, setGraphNodes] = useState(12);
  const [autoTerminals, setAutoTerminals] = useState(true);
  const [autoClassify, setAutoClassify] = useState(true);
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

  const imgRef = useRef<HTMLImageElement | null>(null);
  const previewCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const refreshTemplates = useCallback(async () => {
    try {
      const data = await listCropTemplates();
      setTemplates(data);
      if (data.length && !templateId) {
        setTemplateId(data[0].id);
      }
    } catch {
      // ignore
    }
  }, [templateId]);

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

  function getCropPixels(): CropPixels | null {
    if (!completedCrop || !imgRef.current) {
      return null;
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

  const getPercentCrop = () => {
    if (!imageDims || !completedCrop || !imgRef.current) {
      return null;
    }
    const scaleX = imgRef.current.naturalWidth / imgRef.current.width;
    const scaleY = imgRef.current.naturalHeight / imgRef.current.height;
    const x = (completedCrop.x * scaleX) / imageDims.width;
    const y = (completedCrop.y * scaleY) / imageDims.height;
    const w = (completedCrop.width * scaleX) / imageDims.width;
    const h = (completedCrop.height * scaleY) / imageDims.height;
    return { x, y, width: w, height: h };
  };

  const templateCropPixels = (tmpl: import("../api").CropTemplate) => {
    if (!imageDims) {
      return null;
    }
    const pct = tmpl.crop_pct;
    return {
      x: Math.round(pct.x * imageDims.width),
      y: Math.round(pct.y * imageDims.height),
      width: Math.round(pct.width * imageDims.width),
      height: Math.round(pct.height * imageDims.height)
    };
  };

  const applyTemplateCrop = (tmpl: import("../api").CropTemplate) => {
    if (!imageDims) {
      return;
    }
    const cropBox = templateCropPixels(tmpl);
    if (!cropBox) {
      return;
    }
    setCrop(cropPixelsToPercent(cropBox));
    if (imgRef.current) {
      const scaleX = imgRef.current.width / imageDims.width;
      const scaleY = imgRef.current.height / imageDims.height;
      setCompletedCrop({
        unit: "px",
        x: cropBox.x * scaleX,
        y: cropBox.y * scaleY,
        width: cropBox.width * scaleX,
        height: cropBox.height * scaleY
      });
    }
  };

  function cropPixelsToPercent(cropBox: CropPixels): Crop {
    if (!imageDims) {
      return DEFAULT_CROP;
    }
    const x = (cropBox.x / imageDims.width) * 100;
    const y = (cropBox.y / imageDims.height) * 100;
    const width = (cropBox.width / imageDims.width) * 100;
    const height = (cropBox.height / imageDims.height) * 100;
    return { unit: "%", x, y, width, height };
  }

  async function handleAutoCrop() {
    if (!file) {
      return;
    }
    try {
      const tmpl = templates.find((t) => t.id === templateId);
      let seedCrop: CropPixels | null = null;
      if (tmpl) {
        seedCrop = templateCropPixels(tmpl);
      }
      if (!seedCrop) {
        const current = getCropPixels();
        if (current && imageDims) {
          const ratio = (current.width * current.height) / Math.max(1, imageDims.width * imageDims.height);
          if (ratio < 0.985) {
            seedCrop = current;
          }
        }
      }
      const res = await imageAutoCrop({ file, threshold, invert, padding, crop: seedCrop });
      if (!res.crop) {
        setStatus(res.message ?? "Auto-crop failed.");
        return;
      }
      setCrop(cropPixelsToPercent(res.crop));
      setGridDetection(null);
      setTerminalDetections([]);
      if (imgRef.current && imageDims) {
        const scaleX = imgRef.current.width / imageDims.width;
        const scaleY = imgRef.current.height / imageDims.height;
        setCompletedCrop({
          unit: "px",
          x: res.crop.x * scaleX,
          y: res.crop.y * scaleY,
          width: res.crop.width * scaleX,
          height: res.crop.height * scaleY
        });
      }
      setStatus(seedCrop ? "Auto-crop applied (refined from seed crop)." : "Auto-crop applied.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Auto-crop failed.");
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
      const res = await imageGenerate({
        file,
        targetType,
        gridWidth,
        gridHeight,
        graphLayout,
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
      if (onApplyGrid) {
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
        onApplyGrid({
          type: builderType,
          rows,
          cols,
          terminals,
          nodeTerminals: detectedNodeTerminals,
          suggestedName: ocrSuggested ?? res.name,
          levelType: detectedLevelType ?? levelType,
          edgeOverrides: graphOverrides ?? manualEdgeOverrides
        });
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

  async function handleRunPipeline() {
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
      const tmpl = templates.find((t) => t.id === templateId);
      let crop = getCropPixels();
      if (tmpl) {
        applyTemplateCrop(tmpl);
        const templateCrop = templateCropPixels(tmpl);
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
      let suggestedName: string | null = ocrSuggested;
      let detectedLevelType: LevelType | null = levelType;

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

      const builderType = getBuilderType(targetType, detectedLevelType);
      const gridDrivenTarget =
        builderType === "square" ||
        builderType === "hex" ||
        builderType === "circle" ||
        (builderType === "graph" && graphLayout === "grid");

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
          setPipelineStatus(gridRes.message ?? "Grid detection failed.");
          setPipelineChecks((prev) => ({ ...prev, grid: "fail" }));
          return;
        }
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
      } else if (pipelineUseGrid && !gridDrivenTarget) {
        setPipelineChecks((prev) => ({ ...prev, grid: "skipped" }));
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
      } else if (pipelineUseTerminals && !gridDrivenTarget) {
        setPipelineChecks((prev) => ({ ...prev, terminals: "skipped" }));
        setTerminalStatus("Terminal detection skipped for non-grid topology.");
      } else {
        setPipelineChecks((prev) => ({ ...prev, terminals: "skipped" }));
      }

      if (onApplyGrid) {
        onApplyGrid({
          type: builderType,
          rows,
          cols,
          terminals,
          suggestedName,
          levelType: detectedLevelType,
          edgeOverrides: manualEdgeOverrides
        });
      }
      const classificationNote = detectedLevelType ? ` (${detectedLevelType.geometry})` : "";
      setPipelineStatus(`Pipeline applied to builder${classificationNote}.`);
    } catch (err) {
      setPipelineChecks((prev) => ({
        classify: prev.classify === "pending" ? "fail" : prev.classify,
        ocr: prev.ocr === "pending" ? "fail" : prev.ocr,
        grid: prev.grid === "pending" ? "fail" : prev.grid,
        terminals: prev.terminals === "pending" ? "fail" : prev.terminals
      }));
      setPipelineStatus(err instanceof Error ? err.message : "Pipeline failed.");
    } finally {
      setPipelineBusy(false);
    }
  }

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

  const advancedSections = (
    <>
      <Card>
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
            <Divider />
            <Typography variant="subtitle2">Import pipeline</Typography>
            <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
              <FormControlLabel
                control={
                  <Switch checked={pipelineUseClassifier} onChange={(event) => setPipelineUseClassifier(event.target.checked)} />
                }
                label="Classifier"
              />
              <FormControlLabel
                control={<Switch checked={pipelineUseOcr} onChange={(event) => setPipelineUseOcr(event.target.checked)} />}
                label="OCR"
              />
              <FormControlLabel
                control={<Switch checked={pipelineUseGrid} onChange={(event) => setPipelineUseGrid(event.target.checked)} />}
                label="Grid"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={pipelineUseTerminals}
                    onChange={(event) => setPipelineUseTerminals(event.target.checked)}
                  />
                }
                label="Terminals"
              />
              <Button variant="contained" onClick={handleRunPipeline} disabled={!file || pipelineBusy}>
                Run pipeline
              </Button>
            </Box>
            {pipelineStatus && (
              <Alert severity={pipelineStatus.includes("failed") ? "error" : "info"}>{pipelineStatus}</Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      <Card>
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
            <Button variant="outlined" onClick={handleAutoCrop} disabled={!file}>
              Auto-crop
            </Button>
            <FormControlLabel
              control={<Switch checked={ocrWholeImage} onChange={(event) => setOcrWholeImage(event.target.checked)} />}
              label="OCR full screen"
            />
            <Button variant="outlined" onClick={handleOcr} disabled={!file}>
              OCR level name
            </Button>
          </Stack>
          {completedCrop && (
            <Box mt={2}>
              <Typography variant="caption" color="text.secondary">
                Cropped preview
              </Typography>
              <canvas
                ref={previewCanvasRef}
                style={{
                  width: "100%",
                  maxWidth: 360,
                  maxHeight: 220,
                  marginTop: 8,
                  borderRadius: 6,
                  border: "1px solid rgba(255,255,255,0.08)"
                }}
              />
            </Box>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            OCR result
          </Typography>
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
        </CardContent>
      </Card>

      <Card>
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
          <Typography variant="caption" color="text.secondary">
            Line detection works best when grid lines are visible.
          </Typography>
          {levelTypeStatus && (
            <Typography variant="caption" color="text.secondary">
              {levelTypeStatus}
            </Typography>
          )}
          {gridStatus && (
            <Typography variant="caption" color="text.secondary">
              {gridStatus}
            </Typography>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Terminal detection
          </Typography>
          <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
            <TextField
              label="Sat threshold"
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
              label="BG threshold"
              type="number"
              value={bgThreshold}
              onChange={(event) => setBgThreshold(Number(event.target.value))}
              size="small"
            />
            <Button variant="outlined" onClick={handleDetectTerminals} disabled={!file}>
              Detect terminals
            </Button>
          </Stack>
          {terminalStatus && <Typography variant="caption">{terminalStatus}</Typography>}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Generate graph space
          </Typography>
          <Stack spacing={2}>
            <TextField
              label="Target type"
              select
              value={targetType}
              onChange={(event) => setTargetType(event.target.value as typeof targetType)}
              size="small"
              sx={{ maxWidth: 240 }}
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

            <Button variant="contained" onClick={handleGenerate} disabled={!file}>
              Generate puzzle
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {(status || saveError) && <Alert severity={saveError ? "error" : "info"}>{saveError ?? status}</Alert>}

      {generatedText && (
        <Card>
          <CardContent>
            <Typography variant="subtitle1" gutterBottom>
              Generated puzzle
            </Typography>
            <Stack spacing={2}>
              <TextField
                label="Generated name"
                value={generatedName}
                onChange={(event) => setGeneratedName(event.target.value)}
                size="small"
              />
              <TextField label="Puzzle text" value={generatedText} multiline minRows={8} />
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <Button variant="outlined" onClick={() => onGenerated(generatedName, generatedText)}>
                  Load into editor
                </Button>
                <Button variant="outlined" onClick={handleSave}>
                  Save to library
                </Button>
              </Stack>
              {saveStatus && <Alert severity="success">{saveStatus}</Alert>}
            </Stack>
          </CardContent>
        </Card>
      )}
    </>
  );

  if (compact) {
    return (
      <Stack spacing={2}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Image Import
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Upload an image, run the pipeline, and apply results to the builder.
            </Typography>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Stack spacing={2}>
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={(event) => {
                  const next = event.target.files?.[0] ?? null;
                  if (!next) {
                    setFile(null);
                    setImageSrc(null);
                    setImageName("");
                    setGeneratedText("");
                    return;
                  }
                  setFile(next);
                  setImageName(next.name);
                  setImageSrc(URL.createObjectURL(next));
                  setGeneratedText("");
                  setStatus(null);
                  setTerminalStatus(null);
                  setGridDetection(null);
                  setTerminalDetections([]);
                  setLevelType(null);
                  setLevelTypeStatus(null);
                  setPipelineChecks({ classify: "idle", ocr: "idle", grid: "idle", terminals: "idle" });
                }}
              />
              {imageSrc ? (
                <Box sx={{ display: "inline-block", maxWidth: "100%" }}>
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
                        maxHeight: 420,
                        width: "auto",
                        height: "auto",
                        display: "block"
                      }}
                      onLoad={() => {
                        if (imgRef.current) {
                          setImageDims({
                            width: imgRef.current.naturalWidth,
                            height: imgRef.current.naturalHeight
                          });
                        }
                        setCrop(DEFAULT_CROP);
                      }}
                    />
                  </ReactCrop>
                </Box>
              ) : (
                <Alert severity="info">No image selected yet.</Alert>
              )}
            </Stack>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <Typography variant="subtitle1" gutterBottom>
              Pipeline
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
                <Button variant="contained" onClick={handleRunPipeline} disabled={!file || pipelineBusy}>
                  Run pipeline
                </Button>
              </Box>
              <Box display="flex" flexWrap="wrap" gap={1}>
                {[
                  ["Classifier", pipelineChecks.classify],
                  ["OCR", pipelineChecks.ocr],
                  ["Grid", pipelineChecks.grid],
                  ["Terminals", pipelineChecks.terminals]
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
                  return <Chip key={label as string} label={`${label}`} icon={icon} color={color as any} size="small" />;
                })}
              </Box>
              {pipelineStatus && (
                <Alert severity={pipelineStatus.includes("failed") ? "error" : "info"}>{pipelineStatus}</Alert>
              )}
            </Stack>
          </CardContent>
        </Card>

        <details>
          <summary>Advanced import settings</summary>
          <Stack spacing={2} sx={{ mt: 2 }}>
            {advancedSections}
          </Stack>
        </details>

        <Divider />
        <Box component="pre" sx={{ fontSize: 12, color: "text.secondary" }}>
          {imageSrc ? `Image: ${imageName} (${imageSize})` : "Upload an image to begin."}
        </Box>
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Image Crop & Extraction
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Upload an image, crop, auto-detect grid lines, and generate a starter puzzle.
          </Typography>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack spacing={2}>
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(event) => {
                const next = event.target.files?.[0] ?? null;
                if (!next) {
                  setFile(null);
                  setImageSrc(null);
                  setImageName("");
                  setGeneratedText("");
                  return;
                }
                setFile(next);
                setImageName(next.name);
                setImageSrc(URL.createObjectURL(next));
                setGeneratedText("");
                setStatus(null);
                setTerminalStatus(null);
                setGridDetection(null);
                setTerminalDetections([]);
                setLevelType(null);
                setLevelTypeStatus(null);
                setPipelineChecks({ classify: "idle", ocr: "idle", grid: "idle", terminals: "idle" });
              }}
            />
            {imageSrc ? (
              <Box sx={{ display: "inline-block", maxWidth: "100%" }}>
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
                      maxHeight: 420,
                      width: "auto",
                      height: "auto",
                      display: "block"
                    }}
                    onLoad={() => {
                      if (imgRef.current) {
                        setImageDims({
                          width: imgRef.current.naturalWidth,
                          height: imgRef.current.naturalHeight
                        });
                      }
                      setCrop(DEFAULT_CROP);
                    }}
                  />
                </ReactCrop>
              </Box>
            ) : (
              <Alert severity="info">No image selected yet.</Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Pipeline
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
              <Button variant="contained" onClick={handleRunPipeline} disabled={!file || pipelineBusy}>
                Run pipeline
              </Button>
            </Box>
            <Box display="flex" flexWrap="wrap" gap={1}>
              {[
                ["Classifier", pipelineChecks.classify],
                ["OCR", pipelineChecks.ocr],
                ["Grid", pipelineChecks.grid],
                ["Terminals", pipelineChecks.terminals]
              ].map(([label, state]) => {
                const icon =
                  state === "ok" ? (
                    <CheckCircle fontSize="small" />
                  ) : state === "fail" ? (
                    <Cancel fontSize="small" />
                  ) : state === "pending" ? (
                    <HourglassEmpty fontSize="small" />
                  ) : state === "skipped" ? (
                    <RemoveCircleOutline fontSize="small" />
                  ) : (
                    <RemoveCircleOutline fontSize="small" />
                  );
                const color =
                  state === "ok" ? "success" : state === "fail" ? "error" : state === "pending" ? "warning" : "default";
                return <Chip key={label as string} label={`${label}`} icon={icon} color={color as any} size="small" />;
              })}
            </Box>
            {pipelineStatus && (
              <Alert severity={pipelineStatus.includes("failed") ? "error" : "info"}>{pipelineStatus}</Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      {compact ? (
        <details>
          <summary>Advanced import settings</summary>
          <Stack spacing={2} sx={{ mt: 2 }}>
            {/* Advanced sections below */}
          </Stack>
        </details>
      ) : null}

      {compact ? null : <Card>
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
            <Divider />
            <Typography variant="subtitle2">Import pipeline</Typography>
            <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
              <FormControlLabel
                control={
                  <Switch checked={pipelineUseClassifier} onChange={(event) => setPipelineUseClassifier(event.target.checked)} />
                }
                label="Classifier"
              />
              <FormControlLabel
                control={<Switch checked={pipelineUseOcr} onChange={(event) => setPipelineUseOcr(event.target.checked)} />}
                label="OCR"
              />
              <FormControlLabel
                control={<Switch checked={pipelineUseGrid} onChange={(event) => setPipelineUseGrid(event.target.checked)} />}
                label="Grid"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={pipelineUseTerminals}
                    onChange={(event) => setPipelineUseTerminals(event.target.checked)}
                  />
                }
                label="Terminals"
              />
              <Button variant="contained" onClick={handleRunPipeline} disabled={!file || pipelineBusy}>
                Run pipeline
              </Button>
            </Box>
            {pipelineStatus && (
              <Alert severity={pipelineStatus.includes("failed") ? "error" : "info"}>{pipelineStatus}</Alert>
            )}
          </Stack>
        </CardContent>
      </Card>}

      <Card>
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
            <Button variant="outlined" onClick={handleAutoCrop} disabled={!file}>
              Auto-crop
            </Button>
            <FormControlLabel
              control={
                <Switch checked={ocrWholeImage} onChange={(event) => setOcrWholeImage(event.target.checked)} />
              }
              label="OCR full screen"
            />
            <Button variant="outlined" onClick={handleOcr} disabled={!file}>
              OCR level name
            </Button>
          </Stack>
          {completedCrop && (
            <Box mt={2}>
              <Typography variant="caption" color="text.secondary">
                Cropped preview
              </Typography>
              <canvas
                ref={previewCanvasRef}
                style={{
                  width: "100%",
                  maxWidth: 360,
                  maxHeight: 220,
                  marginTop: 8,
                  borderRadius: 6,
                  border: "1px solid rgba(255,255,255,0.08)"
                }}
              />
            </Box>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            OCR result
          </Typography>
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
        </CardContent>
      </Card>

      <Card>
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
          <Typography variant="caption" color="text.secondary">
            Line detection works best when grid lines are visible.
          </Typography>
          {levelTypeStatus && (
            <Typography variant="caption" color="text.secondary">
              {levelTypeStatus}
            </Typography>
          )}
          {gridStatus && (
            <Typography variant="caption" color="text.secondary">
              {gridStatus}
            </Typography>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Terminal detection
          </Typography>
          <Stack spacing={2} direction={{ xs: "column", md: "row" }}>
            <TextField
              label="Sat threshold"
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
              label="BG threshold"
              type="number"
              value={bgThreshold}
              onChange={(event) => setBgThreshold(Number(event.target.value))}
              size="small"
            />
            <Button variant="outlined" onClick={handleDetectTerminals} disabled={!file}>
              Detect terminals
            </Button>
          </Stack>
          {terminalStatus && <Typography variant="caption">{terminalStatus}</Typography>}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Generate graph space
          </Typography>
          <Stack spacing={2}>
            <TextField
              label="Target type"
              select
              value={targetType}
              onChange={(event) => setTargetType(event.target.value as typeof targetType)}
              size="small"
              sx={{ maxWidth: 240 }}
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
              control={
                <Switch checked={autoTerminals} onChange={(event) => setAutoTerminals(event.target.checked)} />
              }
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

            <Button variant="contained" onClick={handleGenerate} disabled={!file}>
              Generate puzzle
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {(status || saveError) && <Alert severity={saveError ? "error" : "info"}>{saveError ?? status}</Alert>}

      {generatedText && (
        <Card>
          <CardContent>
            <Typography variant="subtitle1" gutterBottom>
              Generated puzzle
            </Typography>
            <Stack spacing={2}>
              <TextField
                label="Generated name"
                value={generatedName}
                onChange={(event) => setGeneratedName(event.target.value)}
                size="small"
              />
              <TextField label="Puzzle text" value={generatedText} multiline minRows={8} />
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <Button variant="outlined" onClick={() => onGenerated(generatedName, generatedText)}>
                  Load into editor
                </Button>
                <Button variant="outlined" onClick={handleSave}>
                  Save to library
                </Button>
              </Stack>
              {saveStatus && <Alert severity="success">{saveStatus}</Alert>}
            </Stack>
          </CardContent>
        </Card>
      )}

      <Divider />
      <Box component="pre" sx={{ fontSize: 12, color: "text.secondary" }}>
        {imageSrc ? `Image: ${imageName} (${imageSize})` : "Upload an image to begin."}
      </Box>
    </Stack>
  );
}
