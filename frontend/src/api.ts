export type PuzzleEntry = {
  name: string;
  source: string;
  rel_path: string;
  kind: string;
  type_label: string;
  size_label: string;
  metrics: Record<string, number>;
  nodes: number | null;
  edges: number | null;
  tiles: number | null;
  colors: number | null;
  meta: Record<string, string>;
  error: string | null;
  mtime: number | null;
};

export type ParseResponse = {
  kind: string;
  type_label: string;
  size_label: string;
  metrics: Record<string, number>;
  counts: Record<string, number | boolean>;
  meta: Record<string, string>;
  terminals: Record<string, string[]>;
  validation?: ValidationResponse;
};

export type SolveResponse = {
  node_color: Record<string, string | null>;
  paths: Record<string, string[]>;
  path_edges: Record<string, Array<[string, string]>>;
  stats: Record<string, number | string | boolean | null>;
  unique: boolean | null;
  graph: {
    nodes: Array<{
      id: string;
      x: number;
      y: number;
      z: number;
      kind: string;
      data: Record<string, unknown>;
    }>;
    edges: Array<[string, string]>;
    adjacencies?: Array<{
      id: string;
      a: { channel: string; port: string };
      b: { channel: string; port: string };
      kind: "local" | "seam" | "warp" | "custom";
      state: "open" | "blocked";
      group?: string | null;
      data?: Record<string, unknown>;
    }>;
    terminals: Record<string, [string, string]>;
    tiles: Record<string, string[]>;
    terminal_colors?: Record<string, string>;
    schema_version?: number;
    topology?: {
      template?: { id?: string; parameters?: Record<string, unknown> } | null;
      data?: Record<string, unknown>;
    };
    display?: Record<string, unknown>;
    catalog?: Record<string, unknown>;
  };
};

export type ValidationIssue = {
  code: string;
  message: string;
  severity: "error" | "warning";
  nodes: string[];
  colors: string[];
};

export type ValidationResponse = {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  stats: Record<string, number | string | boolean | null>;
  solvable?: boolean;
  solve_error?: string;
  solution?: {
    path_lengths: Record<string, number>;
    stats: Record<string, number | string | boolean | null>;
    unique: boolean | null;
  };
};

export type GraphResponse = {
  graph: SolveResponse["graph"];
};

export type LevelGeometry = "square" | "hex" | "circle" | "graph" | "cube" | "star" | "figure8";
export type LevelModifier = "bridges" | "warps" | "walls";

export type LevelTypeCandidate = {
  id: string;
  geometry: LevelGeometry;
  modifiers: LevelModifier[];
  confidence: number;
  can_emit_flow: boolean;
  recommended_target_type: LevelGeometry;
  recommended_output_format: "flow" | "json";
  reason?: string;
};

export type LevelType = LevelTypeCandidate & {
  source: string;
  candidates: LevelTypeCandidate[];
  notes: string[];
  signals?: Record<string, unknown>;
};

export type ImageCropResponse = {
  crop: { x: number; y: number; width: number; height: number } | null;
  image_size: { width: number; height: number };
  seed_crop?: { x: number; y: number; width: number; height: number } | null;
  message?: string;
};

export type ImageClassifyResponse = {
  level_type: LevelType;
  candidates: LevelTypeCandidate[];
  warnings: string[];
  signals: Record<string, unknown>;
  image_size: { width: number; height: number };
  perspective?: Record<string, unknown> | null;
};

export type ImageGridResponse = {
  grid: { rows: number; cols: number; vertical_lines: number; horizontal_lines: number; mode?: string } | null;
  image_size: { width: number; height: number };
  message?: string;
  perspective?: Record<string, unknown> | null;
  circle?: Record<string, unknown>;
};

export type ImageTerminalsResponse = {
  terminals: Array<{ row: number; col: number; letter: string; color: number[] }>;
  info: { clusters: Array<{ color: number[]; count: number }>; candidates: number; warnings: string[] };
  perspective?: Record<string, unknown> | null;
  auto_crop?: Record<string, unknown> | null;
};

export type ImageGenerateResponse = {
  name: string;
  text: string;
  metadata: Record<string, string>;
  import_id?: string;
  archived_at?: number;
  detection: {
    grid?: { rows: number; cols: number; vertical_lines?: number; horizontal_lines?: number };
    terminals?: Array<{ row?: number; col?: number; node_id?: string; letter: string; color?: number[] }>;
    terminal_info?: Record<string, unknown>;
    warnings?: string[];
    perspective?: Record<string, unknown> | null;
    level_type?: LevelType;
    level_type_candidates?: LevelTypeCandidate[];
    target_type_requested?: string;
    target_type_used?: string;
    [key: string]: unknown;
  };
};

export type ImageImportEntry = {
  id: string;
  created_at: number;
  updated_at?: number;
  status: "processed" | "failed";
  original_name: string;
  content_type: string;
  byte_size: number;
  image_size: { width: number; height: number };
  generated_name: string;
  geometry?: string | null;
  grid?: { rows?: number; cols?: number; [key: string]: unknown } | null;
  terminal_count: number;
  error?: string;
  solve_status?: "solved" | "failed";
  solve_error?: string | null;
  solve_ms?: number | null;
  solver?: string | null;
  run_count?: number;
};

export type ImageImportRecord = ImageImportEntry & {
  processing: Record<string, unknown>;
  result?: ImageGenerateResponse;
  solve?: {
    status: "solved" | "failed";
    updated_at: number;
    result?: SolveResponse;
    error?: string;
  };
  runs?: Array<{
    completed_at?: number;
    status: "processed" | "failed";
    geometry?: string | null;
    grid?: Record<string, unknown> | null;
    terminal_count?: number;
    processing?: Record<string, unknown>;
    solve_status?: "solved" | "failed" | null;
    solve_error?: string | null;
    solve_ms?: number | null;
    solver?: string | null;
    error?: string | null;
  }>;
};

export type ImageJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  created_at: number;
  updated_at: number;
  total: number;
  completed: number;
  failed: number;
  items: Array<{
    index: number;
    original_name: string;
    status: "queued" | "processed" | "failed";
    import_id?: string;
    generated_name?: string;
    solve_status?: "solved" | "failed";
    solve_ms?: number;
    solve_error?: string;
    error?: string;
  }>;
};

export type CropTemplate = {
  id: string;
  name: string;
  image_width: number;
  image_height: number;
  crop: { x: number; y: number; width: number; height: number };
  crop_pct: { x: number; y: number; width: number; height: number };
  note?: string;
  created_at?: number;
  has_preview?: boolean;
  pipeline?: {
    classifier?: boolean;
    ocr?: boolean;
    grid?: boolean;
    terminals?: boolean;
    ocr_full?: boolean;
  };
};

function resolveApiUrl() {
  const explicit = (import.meta.env.VITE_API_URL ?? "").trim();
  if (explicit) {
    return explicit.replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    const host = window.location.hostname;
    const port = (import.meta.env.VITE_API_PORT ?? "8000").trim() || "8000";
    return `${protocol}//${host}:${port}`;
  }
  return "http://localhost:8000";
}

export const API_URL = resolveApiUrl();

function encodePath(path: string) {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export async function listPuzzles(): Promise<PuzzleEntry[]> {
  const data = await apiRequest<{ entries: PuzzleEntry[] }>("/puzzles");
  return data.entries;
}

export type DocPageInfo = { id: string; title: string };

export async function listDocPages(): Promise<DocPageInfo[]> {
  const data = await apiRequest<{ pages: DocPageInfo[] }>("/docs-pages");
  return data.pages;
}

export async function getDocPage(pageId: string): Promise<DocPageInfo & { markdown: string }> {
  return apiRequest<DocPageInfo & { markdown: string }>(`/docs-pages/${encodeURIComponent(pageId)}`);
}

export async function getPuzzle(source: string, name: string): Promise<{ name: string; text: string }> {
  return apiRequest<{ name: string; text: string }>(`/puzzles/${source}/${encodePath(name)}`);
}

export async function getPuzzleGraph(source: string, name: string): Promise<GraphResponse> {
  return apiRequest<GraphResponse>(`/puzzles/${source}/${encodePath(name)}/graph`);
}

export async function parsePuzzle(payload: { name: string; text: string; fill?: boolean }): Promise<ParseResponse> {
  return apiRequest<ParseResponse>("/parse", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function graphFromText(
  payload: { name: string; text: string; fill?: boolean },
  signal?: AbortSignal
): Promise<GraphResponse> {
  return apiRequest<GraphResponse>("/graph", {
    method: "POST",
    body: JSON.stringify(payload),
    signal
  });
}

export async function solvePuzzle(payload: {
  name: string;
  text: string;
  fill?: boolean;
  solver?: string;
  timeout_ms?: number;
  check_unique?: boolean;
  import_id?: string;
}): Promise<SolveResponse> {
  return apiRequest<SolveResponse>("/solve", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function savePuzzle(payload: {
  name: string;
  text: string;
  overwrite?: boolean;
  drop_empty?: boolean;
  metadata?: Record<string, string>;
}): Promise<{ path: string; text?: string }> {
  return apiRequest<{ path: string; text?: string }>("/puzzles/save", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function renamePuzzle(payload: {
  source: string;
  old_name: string;
  new_name: string;
}): Promise<{ old_path: string; new_path: string }> {
  return apiRequest<{ old_path: string; new_path: string }>("/puzzles/rename", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function deletePuzzle(source: string, name: string): Promise<{ deleted: boolean; path: string }> {
  const res = await fetch(`${API_URL}/puzzles/${source}/${encodePath(name)}`, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  return res.json() as Promise<{ deleted: boolean; path: string }>;
}

export type ImageImportPage = {
  entries: ImageImportEntry[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
};

export async function listImageImports(params: {
  limit?: number;
  offset?: number;
  status?: "all" | "processed" | "solved" | "unknown" | "failed";
  search?: string;
  order?: "newest" | "oldest";
} = {}): Promise<ImageImportPage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
    status: params.status ?? "all",
    search: params.search ?? "",
    order: params.order ?? "newest"
  });
  return apiRequest<ImageImportPage>(`/image-imports?${query.toString()}`);
}

export async function archiveImageImportFailure(params: {
  file: File;
  error: string;
  stage?: string;
}): Promise<ImageImportEntry> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("error", params.error);
  form.append("stage", params.stage ?? "processing");
  return imageRequest<ImageImportEntry>("/image-imports/failed", form);
}

export async function createImageJob(
  files: File[],
  options: Record<string, unknown>
): Promise<ImageJob> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  form.append("options_json", JSON.stringify(options));
  return imageRequest<ImageJob>("/image/jobs", form);
}

export async function getImageJob(jobId: string): Promise<ImageJob> {
  return apiRequest<ImageJob>(`/image/jobs/${encodeURIComponent(jobId)}`);
}

export async function fetchImageJobItemFile(
  jobId: string,
  itemIndex: number,
  originalName: string
): Promise<File> {
  const path = `/image/jobs/${encodeURIComponent(jobId)}/items/${itemIndex}/image`;
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? response.statusText;
    throw new Error(message);
  }
  const blob = await response.blob();
  return new File([blob], originalName, {
    type: blob.type || "application/octet-stream",
    lastModified: Date.now()
  });
}

export async function getImageImport(importId: string): Promise<ImageImportRecord> {
  return apiRequest<ImageImportRecord>(`/image-imports/${encodeURIComponent(importId)}`);
}

export function imageImportImageUrl(importId: string) {
  return `${API_URL}/image-imports/${encodeURIComponent(importId)}/image`;
}

export async function deleteImageImport(importId: string): Promise<{ deleted: boolean; id: string }> {
  const res = await fetch(`${API_URL}/image-imports/${encodeURIComponent(importId)}`, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  return res.json() as Promise<{ deleted: boolean; id: string }>;
}

export async function bulkDeleteImageImports(
  importIds: string[]
): Promise<{ deleted: string[]; missing: string[] }> {
  return apiRequest<{ deleted: string[]; missing: string[] }>("/image-imports/bulk-delete", {
    method: "POST",
    body: JSON.stringify({ ids: importIds })
  });
}

export async function recordImageImportReprocessFailure(params: {
  importId: string;
  error: string;
  stage?: string;
}): Promise<ImageImportEntry> {
  return apiRequest<ImageImportEntry>(`/image-imports/${encodeURIComponent(params.importId)}/failure`, {
    method: "POST",
    body: JSON.stringify({ error: params.error, stage: params.stage ?? "screenshot-library" })
  });
}

export async function fetchImageImportFile(entry: ImageImportEntry): Promise<File> {
  // A distinct URL + no-store keeps this download from being coalesced with a
  // thumbnail <img> request for the same image; if that request is aborted by
  // an unmount (e.g. leaving the library), a shared network job would fail too.
  const res = await fetch(`${imageImportImageUrl(entry.id)}?download=1`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  const blob = await res.blob();
  return new File([blob], entry.original_name, {
    type: entry.content_type || blob.type || "application/octet-stream",
    lastModified: Math.round((entry.updated_at ?? entry.created_at) * 1000)
  });
}

async function imageRequest<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    body: formData
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export async function imageAutoCrop(params: {
  file: File;
  threshold: number;
  invert: boolean;
  padding: number;
  crop?: { x: number; y: number; width: number; height: number } | null;
}): Promise<ImageCropResponse> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  form.append("threshold", String(params.threshold));
  form.append("invert", String(params.invert));
  form.append("padding", String(params.padding));
  return imageRequest<ImageCropResponse>("/image/crop/auto", form);
}

export async function validatePuzzle(payload: {
  name: string;
  text: string;
  fill?: boolean;
  check_solvable?: boolean;
  solver?: string;
  timeout_ms?: number;
}): Promise<ValidationResponse> {
  return apiRequest<ValidationResponse>("/validate", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function imageDetectGrid(params: {
  file: File;
  targetType?: string;
  threshold: number;
  lineThreshold: number;
  invert: boolean;
  perspective?: boolean;
  crop?: { x: number; y: number; width: number; height: number } | null;
}): Promise<ImageGridResponse> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.targetType) {
    form.append("target_type", params.targetType);
  }
  form.append("threshold", String(params.threshold));
  form.append("line_threshold", String(params.lineThreshold));
  form.append("invert", String(params.invert));
  if (params.perspective !== undefined) {
    form.append("perspective", String(params.perspective));
  }
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  return imageRequest<ImageGridResponse>("/image/grid/detect", form);
}

export async function imageClassify(params: {
  file: File;
  threshold: number;
  lineThreshold: number;
  invert: boolean;
  perspective?: boolean;
  levelHint?: string;
  crop?: { x: number; y: number; width: number; height: number } | null;
}): Promise<ImageClassifyResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("threshold", String(params.threshold));
  form.append("line_threshold", String(params.lineThreshold));
  form.append("invert", String(params.invert));
  if (params.perspective !== undefined) {
    form.append("perspective", String(params.perspective));
  }
  if (params.levelHint) {
    form.append("level_hint", params.levelHint);
  }
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  return imageRequest<ImageClassifyResponse>("/image/classify", form);
}

export async function imageDetectTerminals(params: {
  file: File;
  targetType?: string;
  rows: number;
  cols: number;
  satThreshold: number;
  brightnessMin: number;
  brightnessMax: number;
  marginRatio: number;
  clusterThreshold: number;
  bgThreshold: number;
  perspective?: boolean;
  crop?: { x: number; y: number; width: number; height: number } | null;
}): Promise<ImageTerminalsResponse> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.targetType) {
    form.append("target_type", params.targetType);
  }
  form.append("rows", String(params.rows));
  form.append("cols", String(params.cols));
  form.append("sat_threshold", String(params.satThreshold));
  form.append("brightness_min", String(params.brightnessMin));
  form.append("brightness_max", String(params.brightnessMax));
  form.append("margin_ratio", String(params.marginRatio));
  form.append("cluster_threshold", String(params.clusterThreshold));
  form.append("bg_threshold", String(params.bgThreshold));
  if (params.perspective !== undefined) {
    form.append("perspective", String(params.perspective));
  }
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  return imageRequest<ImageTerminalsResponse>("/image/terminals/detect", form);
}

export async function imageGenerate(params: {
  file: File;
  replaceImportId?: string;
  targetType: string;
  gridWidth?: number;
  gridHeight?: number;
  graphLayout?: string;
  graphNodes?: number;
  autoTerminals?: boolean;
  autoClassify?: boolean;
  levelType?: LevelType | null;
  edgeOverrides?: {
    add?: Array<[string, string]>;
    remove?: Array<[string, string]>;
    warps?: Array<[string, string]>;
    walls?: Array<[string, string]>;
  };
  metadata?: Record<string, string>;
  crop?: { x: number; y: number; width: number; height: number } | null;
  threshold?: number;
  lineThreshold?: number;
  invert?: boolean;
  perspective?: boolean;
  satThreshold?: number;
  brightnessMin?: number;
  brightnessMax?: number;
  marginRatio?: number;
  clusterThreshold?: number;
  bgThreshold?: number;
}): Promise<ImageGenerateResponse> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.replaceImportId) {
    form.append("replace_import_id", params.replaceImportId);
  }
  form.append("target_type", params.targetType);
  form.append("output_schema_version", "2");
  if (params.gridWidth !== undefined) {
    form.append("grid_width", String(params.gridWidth));
  }
  if (params.gridHeight !== undefined) {
    form.append("grid_height", String(params.gridHeight));
  }
  if (params.graphLayout) {
    form.append("graph_layout", params.graphLayout);
  }
  if (params.graphNodes !== undefined) {
    form.append("graph_nodes", String(params.graphNodes));
  }
  if (params.autoTerminals !== undefined) {
    form.append("auto_terminals", String(params.autoTerminals));
  }
  if (params.autoClassify !== undefined) {
    form.append("auto_classify", String(params.autoClassify));
  }
  if (params.levelType) {
    form.append("level_type_json", JSON.stringify(params.levelType));
  }
  if (params.edgeOverrides) {
    form.append("edge_overrides_json", JSON.stringify(params.edgeOverrides));
  }
  if (params.metadata) {
    form.append("metadata_json", JSON.stringify(params.metadata));
  }
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  if (params.threshold !== undefined) {
    form.append("threshold", String(params.threshold));
  }
  if (params.lineThreshold !== undefined) {
    form.append("line_threshold", String(params.lineThreshold));
  }
  if (params.invert !== undefined) {
    form.append("invert", String(params.invert));
  }
  if (params.perspective !== undefined) {
    form.append("perspective", String(params.perspective));
  }
  if (params.satThreshold !== undefined) {
    form.append("sat_threshold", String(params.satThreshold));
  }
  if (params.brightnessMin !== undefined) {
    form.append("brightness_min", String(params.brightnessMin));
  }
  if (params.brightnessMax !== undefined) {
    form.append("brightness_max", String(params.brightnessMax));
  }
  if (params.marginRatio !== undefined) {
    form.append("margin_ratio", String(params.marginRatio));
  }
  if (params.clusterThreshold !== undefined) {
    form.append("cluster_threshold", String(params.clusterThreshold));
  }
  if (params.bgThreshold !== undefined) {
    form.append("bg_threshold", String(params.bgThreshold));
  }
  return imageRequest<ImageGenerateResponse>("/image/generate", form);
}

export async function imageOcr(params: {
  file: File;
  crop?: { x: number; y: number; width: number; height: number } | null;
  perspective?: boolean;
}): Promise<{ text: string; suggested_name?: string; message?: string }> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.perspective !== undefined) {
    form.append("perspective", String(params.perspective));
  }
  if (params.crop) {
    form.append("crop_x", String(params.crop.x));
    form.append("crop_y", String(params.crop.y));
    form.append("crop_width", String(params.crop.width));
    form.append("crop_height", String(params.crop.height));
  }
  return imageRequest<{ text: string; suggested_name?: string; message?: string }>("/image/ocr", form);
}

export async function listCropTemplates(): Promise<CropTemplate[]> {
  const data = await apiRequest<{ templates: CropTemplate[] }>("/templates/crop");
  return data.templates;
}

export async function saveCropTemplate(payload: {
  name: string;
  image_width: number;
  image_height: number;
  crop: { x: number; y: number; width: number; height: number };
  note?: string;
  preview_png_base64?: string;
  pipeline?: {
    classifier?: boolean;
    ocr?: boolean;
    grid?: boolean;
    terminals?: boolean;
    ocr_full?: boolean;
  };
}): Promise<{ id: string }> {
  return apiRequest<{ id: string }>("/templates/crop", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function cropTemplatePreviewUrl(templateId: string) {
  return `${API_URL}/templates/crop/${encodeURIComponent(templateId)}/preview`;
}

export async function deleteCropTemplate(templateId: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_URL}/templates/crop/${encodeURIComponent(templateId)}`, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message = (detail as { detail?: string }).detail ?? res.statusText;
    throw new Error(message);
  }
  return res.json() as Promise<{ deleted: boolean }>;
}
