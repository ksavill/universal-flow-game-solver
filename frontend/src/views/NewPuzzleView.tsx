import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  CardContent,
  FormControlLabel,
  Grid,
  MenuItem,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { AutoAwesome, BackspaceOutlined } from "@mui/icons-material";
import { LevelType, listPuzzles, savePuzzle } from "../api";
import { TERMINAL_PALETTE } from "../colors";
import {
  EMPTY_EDGE_OVERRIDES,
  EdgeOverrides,
  formatEdgePairsText,
  parseEdgeOverrideTexts
} from "../edgeOverrides";
import { ImageView } from "./ImageView";

type NewPuzzleViewProps = {
  onCreatePuzzle: (name: string, text: string, opts?: { autoSolve?: boolean }) => void;
};

type BuilderType = "square" | "hex" | "circle" | "graph" | "cube" | "star" | "figure8";
type FlowType = "square" | "hex" | "circle";
type GraphLikeType = "graph" | "cube" | "star" | "figure8";
type TerminalPayload = { row: number; col: number; letter: string; color?: number[] };
type NodeTerminalPayload = { nodeId: string; letter: string; color?: number[] };
type TopologyNode = { id: string; x: number; y: number; z: number };
type TopologySpec = { nodes: TopologyNode[]; edges: Array<[string, string]>; topology?: "cube" | "star" | "figure8" };

const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const LEVEL_PREFIX = "classic_level_";

function isGraphLikeType(value: BuilderType): value is GraphLikeType {
  return value === "graph" || value === "cube" || value === "star" || value === "figure8";
}

function buildGrid(rows: number, cols: number) {
  return Array.from({ length: rows }, () => Array.from({ length: cols }, () => "."));
}

function clampByte(value: number) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function rgbToHex(r: number, g: number, b: number) {
  const toHex = (value: number) => clampByte(value).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function buildDetectedColorMap(terminals: TerminalPayload[]) {
  const sums: Record<string, { r: number; g: number; b: number; count: number }> = {};
  terminals.forEach((terminal) => {
    if (!terminal.color || terminal.color.length < 3) {
      return;
    }
    const [r, g, b] = terminal.color;
    const entry = sums[terminal.letter] ?? { r: 0, g: 0, b: 0, count: 0 };
    entry.r += r;
    entry.g += g;
    entry.b += b;
    entry.count += 1;
    sums[terminal.letter] = entry;
  });

  const out: Record<string, string> = {};
  Object.entries(sums).forEach(([letter, entry]) => {
    if (entry.count > 0) {
      out[letter] = rgbToHex(entry.r / entry.count, entry.g / entry.count, entry.b / entry.count);
    }
  });
  return out;
}

function letterColor(letter: string, overrides?: Record<string, string>) {
  const override = overrides?.[letter];
  if (override) {
    return override;
  }
  const idx = letter.charCodeAt(0) - 65;
  return TERMINAL_PALETTE[idx % TERMINAL_PALETTE.length];
}

function normalizeHexColor(value: string | undefined): string | null {
  if (!value) {
    return null;
  }
  const raw = value.trim();
  const match = raw.match(/^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
  if (!match) {
    return null;
  }
  let hex = match[1].toLowerCase();
  if (hex.length === 3) {
    hex = `${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`;
  }
  return `#${hex}`;
}

function serializeTerminalColors(
  usedLetters: string[],
  detectedColors: Record<string, string>
): string | undefined {
  const entries = usedLetters
    .map((letter) => [letter, normalizeHexColor(detectedColors[letter])] as const)
    .filter((item): item is readonly [string, string] => item[1] !== null)
    .sort((a, b) => a[0].localeCompare(b[0]));
  if (!entries.length) {
    return undefined;
  }
  return JSON.stringify(Object.fromEntries(entries));
}

function buildFlowText(boardType: FlowType, grid: string[][], meta?: Record<string, string>) {
  const lines = [`# type: ${boardType}`, "# fill: true"];
  if (meta) {
    Object.entries(meta).forEach(([key, value]) => {
      if (value) {
        lines.push(`# ${key}: ${value}`);
      }
    });
  }
  grid.forEach((row) => lines.push(row.join("")));
  return `${lines.join("\n")}\n`;
}

function buildGraphTextFromTopology(
  type: GraphLikeType,
  spec: TopologySpec,
  nodeLetters: Record<string, string>,
  meta?: Record<string, string>,
  edgeOverrides?: EdgeOverrides,
  dimensions?: { cols: number; rows: number }
) {
  const terminalsByLetter: Record<string, string[]> = {};
  const knownNodeIds = new Set(spec.nodes.map((node) => node.id));
  Object.entries(nodeLetters).forEach(([nodeId, letter]) => {
    if (!knownNodeIds.has(nodeId) || letter === ".") {
      return;
    }
    const list = terminalsByLetter[letter] ?? [];
    list.push(nodeId);
    terminalsByLetter[letter] = list;
  });

  const terminals: Record<string, [string, string]> = {};
  Object.entries(terminalsByLetter).forEach(([letter, ids]) => {
    if (ids.length >= 2) {
      terminals[letter] = [ids[0], ids[1]];
    }
  });

  const canonicalPair = (u: string, v: string): [string, string] => (u < v ? [u, v] : [v, u]);
  const pairKey = (u: string, v: string) => JSON.stringify(canonicalPair(u, v));
  const edgeRecords = new Map<
    string,
    { pair: [string, string]; kind: "local" | "seam" | "warp" | "custom"; state: "open" | "blocked"; group?: string }
  >();
  spec.edges.forEach(([u, v]) => {
    const faceOf = (nodeId: string) => {
      const parts = nodeId.split(":");
      return parts.length >= 3 && (parts[0] === "cube" || parts[0] === "star") ? parts[1] : null;
    };
    const leftFace = faceOf(u);
    const rightFace = faceOf(v);
    const kind = leftFace !== null && rightFace !== null && leftFace !== rightFace ? "seam" : "local";
    edgeRecords.set(pairKey(u, v), { pair: canonicalPair(u, v), kind, state: "open" });
  });

  const overrides = edgeOverrides ?? EMPTY_EDGE_OVERRIDES;
  [...overrides.remove, ...overrides.walls].forEach(([u, v]) => {
    const key = pairKey(u, v);
    const previous = edgeRecords.get(key);
    edgeRecords.set(key, {
      pair: canonicalPair(u, v),
      kind: previous?.kind ?? "local",
      state: "blocked"
    });
  });
  overrides.add.forEach(([u, v]) => {
    edgeRecords.set(pairKey(u, v), { pair: canonicalPair(u, v), kind: "custom", state: "open" });
  });
  overrides.warps.forEach(([u, v], index) => {
    edgeRecords.set(pairKey(u, v), {
      pair: canonicalPair(u, v),
      kind: "warp",
      state: "open",
      group: `warp-${index + 1}`
    });
  });

  const portsByNode: Record<string, Record<string, Record<string, never>>> = {};
  spec.nodes.forEach((node) => {
    portsByNode[node.id] = {};
  });
  const adjacencyRecords = [...edgeRecords.values()]
    .sort((left, right) => pairKey(...left.pair).localeCompare(pairKey(...right.pair)))
    .map((record, index) => {
      const [u, v] = record.pair;
      const uPort = `edge-${index}:a`;
      const vPort = `edge-${index}:b`;
      portsByNode[u] = portsByNode[u] ?? {};
      portsByNode[v] = portsByNode[v] ?? {};
      portsByNode[u][uPort] = {};
      portsByNode[v][vPort] = {};
      return {
        id: `edge-${String(index).padStart(4, "0")}`,
        a: { channel: u, port: uPort },
        b: { channel: v, port: vPort },
        kind: record.kind,
        state: record.state,
        ...(record.group ? { group: record.group } : {})
      };
    });

  const topologyId = type === "graph" ? "grid" : type === "star" ? "radial_star" : type;
  const mechanics = [
    ...new Set(
      adjacencyRecords.flatMap((edge) => {
        const values: string[] = [];
        if (edge.kind === "warp") values.push("warps");
        if (edge.kind === "seam") values.push("seams");
        if (edge.kind === "custom") values.push("custom-edges");
        if (edge.state === "blocked") values.push("walls");
        return values;
      })
    )
  ];
  const cells = Object.fromEntries(spec.nodes.map((node) => [node.id, { kind: "ordinary" }]));
  const channels = Object.fromEntries(
    spec.nodes.map((node) => [
      node.id,
      { cell: node.id, kind: "cell", ports: portsByNode[node.id] ?? {} }
    ])
  );
  const displayChannels = Object.fromEntries(
    spec.nodes.map((node) => [node.id, { position: [node.x, node.y, node.z] }])
  );

  return `${JSON.stringify(
    {
      format: "flow-solver-puzzle",
      schema_version: 2,
      topology: {
        template: {
          id: topologyId,
          parameters: dimensions
            ? { width: dimensions.cols, height: dimensions.rows }
            : {}
        },
        cells,
        channels,
        adjacencies: adjacencyRecords
      },
      terminals: Object.fromEntries(
        Object.entries(terminals).map(([color, endpoints]) => [color, { endpoints }])
      ),
      rules: {
        coverage: { mode: "all-cells", overrides: {} },
        paths: { endpoint_degree: 1, internal_degree: 2, connected: true },
        multi_channel_cell_color_policy: "distinct"
      },
      display: { dimension: 2, channels: displayChannels },
      catalog: {
        variant: type === "graph" ? "custom" : "shapes",
        display_size: dimensions
          ? {
              label: `${dimensions.cols}x${dimensions.rows}`,
              width: dimensions.cols,
              height: dimensions.rows,
              unit: "template"
            }
          : undefined,
        mechanics
      },
      meta: meta ?? {}
    },
    null,
    2
  )}\n`;
}

function sanitizeNodeLetters(nodeLetters: Record<string, string>, spec: TopologySpec | null) {
  if (!spec) {
    return {};
  }
  const allowed = new Set(spec.nodes.map((node) => node.id));
  const out: Record<string, string> = {};
  Object.entries(nodeLetters).forEach(([nodeId, letter]) => {
    if (allowed.has(nodeId) && letter && letter !== ".") {
      out[nodeId] = letter;
    }
  });
  return out;
}

function edgeSubdivide(
  baseNodes: Record<string, [number, number, number]>,
  baseEdges: Array<[string, string]>,
  detail: number,
  prefix: string
) {
  const detailN = Math.max(1, Math.floor(detail));
  const nodes: TopologyNode[] = Object.entries(baseNodes).map(([id, pos]) => ({
    id,
    x: pos[0],
    y: pos[1],
    z: pos[2]
  }));
  const nodePos = new Map(nodes.map((node) => [node.id, node]));
  const edges: Array<[string, string]> = [];

  baseEdges.forEach(([u, v], edgeIdx) => {
    const from = nodePos.get(u);
    const to = nodePos.get(v);
    if (!from || !to) {
      return;
    }
    let prevId = u;
    for (let step = 1; step < detailN; step += 1) {
      const t = step / detailN;
      const midId = `${prefix}:${edgeIdx}:${step}`;
      nodes.push({
        id: midId,
        x: from.x + (to.x - from.x) * t,
        y: from.y + (to.y - from.y) * t,
        z: from.z + (to.z - from.z) * t
      });
      edges.push([prevId, midId]);
      prevId = midId;
    }
    edges.push([prevId, v]);
  });

  return { nodes, edges };
}

function buildGridTopology(cols: number, rows: number): TopologySpec {
  const width = Math.max(1, cols);
  const height = Math.max(1, rows);
  const nodes: TopologyNode[] = [];
  const edges: Array<[string, string]> = [];
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const id = `${x},${y}`;
      nodes.push({ id, x, y: -y, z: 0 });
      if (x > 0) {
        edges.push([`${x - 1},${y}`, id]);
      }
      if (y > 0) {
        edges.push([`${x},${y - 1}`, id]);
      }
    }
  }
  return { nodes, edges };
}

const SQRT3_OVER_2 = Math.sqrt(3) / 2;

function axialToXY(q: number, r: number) {
  return {
    x: q + 0.5 * r,
    y: -SQRT3_OVER_2 * r
  };
}

function pointInPolygon(x: number, y: number, polygon: Array<{ x: number; y: number }>) {
  if (polygon.length < 3) {
    return false;
  }
  let inside = false;
  let j = polygon.length - 1;
  for (let i = 0; i < polygon.length; i += 1) {
    const pi = polygon[i];
    const pj = polygon[j];
    const intersects = (pi.y > y) !== (pj.y > y);
    if (intersects) {
      const denom = pj.y - pi.y;
      if (Math.abs(denom) > 1e-12) {
        const xCross = ((pj.x - pi.x) * (y - pi.y)) / denom + pi.x;
        if (x < xCross) {
          inside = !inside;
        }
      }
    }
    j = i;
  }
  return inside;
}

function buildLatticeTopologyFromMask(
  prefix: string,
  qExtent: number,
  rExtent: number,
  mask: (x: number, y: number) => boolean
) {
  const qLim = Math.max(2, Math.floor(qExtent));
  const rLim = Math.max(2, Math.floor(rExtent));
  const nodes: TopologyNode[] = [];
  const nodeByCoord = new Map<string, TopologyNode>();

  for (let q = -qLim; q <= qLim; q += 1) {
    for (let r = -rLim; r <= rLim; r += 1) {
      const p = axialToXY(q, r);
      if (!mask(p.x, p.y)) {
        continue;
      }
      const id = `${prefix}:${q},${r}`;
      const node = { id, x: p.x, y: p.y, z: 0 };
      nodes.push(node);
      nodeByCoord.set(`${q},${r}`, node);
    }
  }

  const edgeSet = new Set<string>();
  const edges: Array<[string, string]> = [];
  const dirs: Array<[number, number]> = [
    [1, 0],
    [0, 1],
    [1, -1]
  ];

  nodes.forEach((node) => {
    const raw = node.id.slice(prefix.length + 1);
    const parts = raw.split(",");
    if (parts.length !== 2) {
      return;
    }
    const q = Number(parts[0]);
    const r = Number(parts[1]);
    if (!Number.isFinite(q) || !Number.isFinite(r)) {
      return;
    }
    dirs.forEach(([dq, dr]) => {
      const nb = nodeByCoord.get(`${q + dq},${r + dr}`);
      if (!nb) {
        return;
      }
      const mx = (node.x + nb.x) * 0.5;
      const my = (node.y + nb.y) * 0.5;
      if (!mask(mx, my)) {
        return;
      }
      const a = node.id < nb.id ? node.id : nb.id;
      const b = node.id < nb.id ? nb.id : node.id;
      const key = `${a}|${b}`;
      if (!edgeSet.has(key)) {
        edgeSet.add(key);
        edges.push([a, b]);
      }
    });
  });

  return { nodes, edges };
}

function buildRadialFanTopology(
  prefix: "cube" | "star",
  topology: "cube" | "star",
  faces: number,
  size: number
): TopologySpec {
  const side = Math.max(1, Math.floor(size));
  const nodes: TopologyNode[] = [];
  const edges: Array<[string, string]> = [];
  const rays = Array.from({ length: faces }, (_, face) => {
    const angle = Math.PI / 2 - (2 * Math.PI * face) / faces;
    return { x: Math.cos(angle), y: Math.sin(angle) };
  });
  const nodeId = (face: number, u: number, v: number) => `${prefix}:${face}:${u},${v}`;

  for (let face = 0; face < faces; face += 1) {
    const rayA = rays[face];
    const rayB = rays[(face + 1) % faces];
    for (let v = 0; v < side; v += 1) {
      for (let u = 0; u < side; u += 1) {
        const alongA = (u + 0.5) / side;
        const alongB = (v + 0.5) / side;
        const id = nodeId(face, u, v);
        nodes.push({
          id,
          x: alongA * rayA.x + alongB * rayB.x,
          y: alongA * rayA.y + alongB * rayB.y,
          z: 0
        });
        if (u + 1 < side) {
          edges.push([id, nodeId(face, u + 1, v)]);
        }
        if (v + 1 < side) {
          edges.push([id, nodeId(face, u, v + 1)]);
        }
      }
    }
  }

  for (let face = 0; face < faces; face += 1) {
    const nextFace = (face + 1) % faces;
    for (let offset = 0; offset < side; offset += 1) {
      edges.push([nodeId(face, 0, offset), nodeId(nextFace, offset, 0)]);
    }
  }

  return { nodes, edges, topology };
}

function buildCubeTopology(cols: number, rows: number): TopologySpec {
  return buildRadialFanTopology("cube", "cube", 3, Math.max(cols, rows));
}

function buildStarTopology(cols: number, rows: number): TopologySpec {
  return buildRadialFanTopology("star", "star", 5, Math.max(cols, rows));
}

function buildFigure8Topology(): TopologySpec {
  const positions: Array<[number, number]> = [
    [0, 5.28], [0.99, 4.94], [-0.99, 4.94], [1.55, 4.05], [-1.55, 4.05],
    [1.43, 3.01], [-1.43, 3.01], [-0.74, 2.22], [0.74, 2.22], [0, 1.48],
    [0.73, 0.73], [-0.73, 0.73], [0, 0], [1.47, -0.01], [-1.47, -0.01],
    [0.75, -0.73], [-0.75, -0.73], [2.33, -1.1], [-2.33, -1.1],
    [1.44, -1.52], [-1.44, -1.52], [-1.56, -2.57], [1.56, -2.57],
    [2.51, -2.79], [-2.51, -2.79], [1, -3.47], [-1, -3.47], [0, -3.82],
    [-1.6, -4.22], [1.6, -4.22], [0, -4.79]
  ];
  const indexEdges: Array<[number, number]> = [
    [0, 1], [1, 3], [3, 5], [5, 8], [8, 9], [9, 7], [7, 6], [6, 4],
    [4, 2], [2, 0], [9, 10], [9, 11], [10, 12], [10, 13], [11, 12],
    [11, 14], [12, 15], [12, 16], [13, 15], [13, 17], [14, 16], [14, 18],
    [15, 19], [16, 20], [17, 19], [17, 23], [18, 20], [18, 24], [19, 22],
    [20, 21], [21, 24], [21, 26], [22, 23], [22, 25], [23, 29], [24, 28],
    [25, 27], [25, 29], [26, 27], [26, 28], [27, 30], [28, 30], [29, 30]
  ];
  const nodeId = (index: number) => `fig8:n${String(index).padStart(2, "0")}`;
  return {
    topology: "figure8",
    nodes: positions.map(([x, y], index) => ({ id: nodeId(index), x, y, z: 0 })),
    edges: indexEdges.map(([u, v]) => [nodeId(u), nodeId(v)])
  };
}

function buildTopologySpec(type: GraphLikeType, cols: number, rows: number): TopologySpec {
  if (type === "graph") {
    return buildGridTopology(cols, rows);
  }
  if (type === "cube") {
    return buildCubeTopology(Math.max(1, cols), Math.max(1, rows));
  }
  if (type === "star") {
    return buildStarTopology(Math.max(1, cols), Math.max(1, rows));
  }
  return buildFigure8Topology();
}

function polarPoint(cx: number, cy: number, radius: number, angle: number) {
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle)
  };
}

function describeAnnularSector(
  cx: number,
  cy: number,
  innerRadius: number,
  outerRadius: number,
  startAngle: number,
  endAngle: number
) {
  const sweep = endAngle - startAngle;
  const largeArc = sweep > Math.PI ? 1 : 0;
  const outerStart = polarPoint(cx, cy, outerRadius, startAngle);
  const outerEnd = polarPoint(cx, cy, outerRadius, endAngle);
  if (innerRadius <= 0.001) {
    return `M ${cx} ${cy} L ${outerStart.x} ${outerStart.y} A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y} Z`;
  }
  const innerStart = polarPoint(cx, cy, innerRadius, startAngle);
  const innerEnd = polarPoint(cx, cy, innerRadius, endAngle);
  return `M ${innerStart.x} ${innerStart.y} L ${outerStart.x} ${outerStart.y} A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y} L ${innerEnd.x} ${innerEnd.y} A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${innerStart.x} ${innerStart.y} Z`;
}

export function NewPuzzleView({ onCreatePuzzle }: NewPuzzleViewProps) {
  const [spaceType, setSpaceType] = useState<BuilderType>("square");
  const [cols, setCols] = useState(5);
  const [rows, setRows] = useState(5);
  const [grid, setGrid] = useState<string[][]>(() => buildGrid(5, 5));
  const [graphNodeLetters, setGraphNodeLetters] = useState<Record<string, string>>({});
  const [selectedColor, setSelectedColor] = useState<string | null>("A");
  const [detectedColors, setDetectedColors] = useState<Record<string, string>>({});
  const [name, setName] = useState(`${LEVEL_PREFIX}1.flow`);
  const [autoName, setAutoName] = useState(true);
  const [levelNumber, setLevelNumber] = useState(1);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [edgeAddText, setEdgeAddText] = useState("");
  const [edgeRemoveText, setEdgeRemoveText] = useState("");
  const [edgeWarpsText, setEdgeWarpsText] = useState("");
  const [edgeWallsText, setEdgeWallsText] = useState("");
  const [mobilePanel, setMobilePanel] = useState<"builder" | "import" | "preview">("builder");
  const suppressAutoResetRef = useRef(false);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));

  const graphLike = isGraphLikeType(spaceType);
  const topologySpec = useMemo(
    () => (graphLike ? buildTopologySpec(spaceType, cols, rows) : null),
    [graphLike, spaceType, cols, rows]
  );

  useEffect(() => {
    if (suppressAutoResetRef.current) {
      suppressAutoResetRef.current = false;
      return;
    }
    if (graphLike) {
      setGraphNodeLetters((prev) => sanitizeNodeLetters(prev, topologySpec));
      return;
    }
    setGrid(buildGrid(rows, cols));
  }, [rows, cols, graphLike, topologySpec]);

  useEffect(() => {
    if (!autoName) {
      return;
    }
    const ext = graphLike ? ".json" : ".flow";
    setName(`${LEVEL_PREFIX}${levelNumber}${ext}`);
  }, [autoName, levelNumber, graphLike]);

  useEffect(() => {
    async function seedLevelNumber() {
      try {
        const entries = await listPuzzles();
        const matches = entries
          .map((entry) => entry.name)
          .filter((entryName) => entryName.startsWith(LEVEL_PREFIX))
          .map((entryName) => {
            const rest = entryName.slice(LEVEL_PREFIX.length).replace(/\.(flow|json)$/i, "");
            const num = Number(rest);
            return Number.isFinite(num) ? num : null;
          })
          .filter((num): num is number => num !== null);
        const next = matches.length ? Math.max(...matches) + 1 : 1;
        setLevelNumber(next);
      } catch {
        // ignore lookup errors
      }
    }
    seedLevelNumber();
  }, []);

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    LETTERS.forEach((letter) => {
      out[letter] = 0;
    });
    if (graphLike) {
      Object.values(graphNodeLetters).forEach((value) => {
        if (value !== ".") {
          out[value] = (out[value] ?? 0) + 1;
        }
      });
      return out;
    }
    grid.forEach((row) =>
      row.forEach((cell) => {
        if (cell !== ".") {
          out[cell] = (out[cell] ?? 0) + 1;
        }
      })
    );
    return out;
  }, [graphLike, graphNodeLetters, grid]);

  const parsedEdgeOverrides = useMemo(() => {
    try {
      return {
        value: parseEdgeOverrideTexts({
          addText: edgeAddText,
          removeText: edgeRemoveText,
          warpsText: edgeWarpsText,
          wallsText: edgeWallsText
        }),
        error: null as string | null
      };
    } catch (err) {
      return {
        value: undefined,
        error: err instanceof Error ? err.message : "Invalid edge overrides."
      };
    }
  }, [edgeAddText, edgeRemoveText, edgeWarpsText, edgeWallsText]);

  const invalidColors = useMemo(() => {
    return Object.entries(counts)
      .filter(([, count]) => count !== 0 && count !== 2)
      .map(([letter, count]) => `${letter}=${count}`);
  }, [counts]);

  const usedColors = useMemo(() => {
    return Object.entries(counts)
      .filter(([, count]) => count === 2)
      .map(([letter]) => letter);
  }, [counts]);

  const isValid = invalidColors.length === 0 && usedColors.length > 0;
  const canSubmit = isValid && (!graphLike || !parsedEdgeOverrides.error);
  const gridLikeWidth = graphLike ? Math.max(6, cols) : cols;
  const cellSize = useMemo(() => {
    if (!isMobile) {
      return 32;
    }
    if (gridLikeWidth <= 8) {
      return 30;
    }
    if (gridLikeWidth <= 12) {
      return 26;
    }
    return 22;
  }, [gridLikeWidth, isMobile]);

  const puzzleText = useMemo(() => {
    const meta: Record<string, string> = { size: `${cols}x${rows}` };
    const terminalColorsMeta = serializeTerminalColors(usedColors, detectedColors);
    if (terminalColorsMeta) {
      meta.terminal_colors = terminalColorsMeta;
    }
    if (graphLike && topologySpec) {
      return buildGraphTextFromTopology(
        spaceType,
        topologySpec,
        graphNodeLetters,
        meta,
        parsedEdgeOverrides.value,
        { cols, rows }
      );
    }
    return buildFlowText(spaceType as FlowType, grid, meta);
  }, [
    graphLike,
    spaceType,
    topologySpec,
    graphNodeLetters,
    parsedEdgeOverrides.value,
    grid,
    cols,
    rows,
    usedColors,
    detectedColors
  ]);

  // After a color has both endpoints placed, jump to the next unused color so
  // plotting a whole board is a continuous tap-tap-tap flow.
  const advanceColorIfPairComplete = (placedLetter: string, countForLetter: number) => {
    if (countForLetter !== 2 || selectedColor !== placedLetter) {
      return;
    }
    const nextLetter = LETTERS.find((letter) => (letter === placedLetter ? false : (counts[letter] ?? 0) === 0));
    if (nextLetter) {
      setSelectedColor(nextLetter);
    }
  };

  const handleGridCellClick = (r: number, c: number) => {
    const next = grid.map((row) => row.slice());
    const current = next[r][c];
    if (!selectedColor || current === selectedColor) {
      next[r][c] = ".";
    } else {
      next[r][c] = selectedColor;
    }
    setGrid(next);
    if (selectedColor && next[r][c] === selectedColor) {
      const count = next.reduce(
        (total, row) => total + row.filter((cell) => cell === selectedColor).length,
        0
      );
      advanceColorIfPairComplete(selectedColor, count);
    }
  };

  const handleNodeClick = (nodeId: string) => {
    const next = { ...graphNodeLetters };
    const current = next[nodeId];
    if (!selectedColor || current === selectedColor) {
      delete next[nodeId];
    } else {
      next[nodeId] = selectedColor;
    }
    setGraphNodeLetters(next);
    if (selectedColor && next[nodeId] === selectedColor) {
      const count = Object.values(next).filter((letter) => letter === selectedColor).length;
      advanceColorIfPairComplete(selectedColor, count);
    }
  };

  const applyDetectedGrid = (payload: {
    type: BuilderType;
    rows: number;
    cols: number;
    terminals: TerminalPayload[];
    nodeTerminals?: NodeTerminalPayload[];
    suggestedName?: string | null;
    levelType?: LevelType | null;
    edgeOverrides?: EdgeOverrides;
  }) => {
    suppressAutoResetRef.current = true;
    const nextType = payload.type;
    const nextGraphLike = isGraphLikeType(nextType);
    setSpaceType(nextType);
    setRows(payload.rows);
    setCols(payload.cols);
    if (nextGraphLike) {
      if (nextType === "graph") {
        const nextAssignments: Record<string, string> = {};
        payload.terminals.forEach((terminal) => {
          if (
            terminal.row >= 0 &&
            terminal.row < payload.rows &&
            terminal.col >= 0 &&
            terminal.col < payload.cols
          ) {
            nextAssignments[`${terminal.col},${terminal.row}`] = terminal.letter;
          }
        });
        setGraphNodeLetters(nextAssignments);
      } else {
        const nextAssignments: Record<string, string> = {};
        (payload.nodeTerminals ?? []).forEach((terminal) => {
          if (terminal.nodeId) {
            nextAssignments[terminal.nodeId] = terminal.letter;
          }
        });
        setGraphNodeLetters(nextAssignments);
      }
    } else {
      const next = buildGrid(payload.rows, payload.cols);
      payload.terminals.forEach((terminal) => {
        if (
          terminal.row >= 0 &&
          terminal.row < payload.rows &&
          terminal.col >= 0 &&
          terminal.col < payload.cols
        ) {
          next[terminal.row][terminal.col] = terminal.letter;
        }
      });
      setGrid(next);
    }
    const detectedColorMap = buildDetectedColorMap([
      ...payload.terminals,
      ...(payload.nodeTerminals ?? []).map((terminal) => ({
        row: -1,
        col: -1,
        letter: terminal.letter,
        color: terminal.color
      }))
    ]);
    if (Object.keys(detectedColorMap).length > 0) {
      setDetectedColors(detectedColorMap);
    }
    if (payload.suggestedName) {
      setName(payload.suggestedName);
      setAutoName(false);
    }
    if (payload.edgeOverrides) {
      setEdgeAddText(formatEdgePairsText(payload.edgeOverrides.add));
      setEdgeRemoveText(formatEdgePairsText(payload.edgeOverrides.remove));
      setEdgeWarpsText(formatEdgePairsText(payload.edgeOverrides.warps));
      setEdgeWallsText(formatEdgePairsText(payload.edgeOverrides.walls));
    }
  };

  const handleSave = async () => {
    try {
      setSaveError(null);
      const res = await savePuzzle({ name, text: puzzleText, overwrite: false });
      setSaveStatus(`Saved to ${res.path}`);
      if (autoName) {
        setLevelNumber((prev) => prev + 1);
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed.");
    }
  };

  const graphNodeHint = useMemo(() => {
    if (!topologySpec?.nodes.length) {
      return "x,y";
    }
    return topologySpec.nodes
      .slice(0, 6)
      .map((node) => node.id)
      .join(", ");
  }, [topologySpec]);

  const board = useMemo(() => {
    if (spaceType === "square") {
      return (
        <Box
          display="grid"
          gridTemplateColumns={`repeat(${cols}, ${cellSize}px)`}
          gap={0.6}
          sx={{ maxWidth: "100%", overflowX: "auto", py: 1 }}
        >
          {grid.map((row, r) =>
            row.map((cell, c) => {
              const active = cell !== ".";
              const bg = active ? letterColor(cell, detectedColors) : "rgba(255,255,255,0.06)";
              const color = active ? "#0f1116" : "rgba(255,255,255,0.5)";
              return (
                <Box
                  key={`${r}-${c}`}
                  data-cell={`${r}-${c}`}
                  onClick={() => handleGridCellClick(r, c)}
                  sx={{
                    width: cellSize,
                    height: cellSize,
                    borderRadius: 1,
                    border: "1px solid rgba(255,255,255,0.1)",
                    backgroundColor: bg,
                    color,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: Math.max(10, Math.floor(cellSize * 0.36)),
                    cursor: "pointer",
                    userSelect: "none"
                  }}
                >
                  {cell !== "." ? cell : ""}
                </Box>
              );
            })
          )}
        </Box>
      );
    }

    if (spaceType === "hex") {
      const hexWidth = cellSize * 1.08;
      const hexHeight = cellSize;
      const rowOffset = hexWidth * 0.5;
      const verticalStep = hexHeight * 0.82;
      const boardWidth = cols * hexWidth + rowOffset + 4;
      const boardHeight = rows * verticalStep + hexHeight + 4;
      return (
        <Box sx={{ maxWidth: "100%", overflowX: "auto", py: 1 }}>
          <Box sx={{ position: "relative", width: boardWidth, height: boardHeight }}>
            {grid.map((row, r) =>
              row.map((cell, c) => {
                const active = cell !== ".";
                const bg = active ? letterColor(cell, detectedColors) : "rgba(255,255,255,0.05)";
                const fg = active ? "#0f1116" : "rgba(255,255,255,0.5)";
                const left = c * hexWidth + (r % 2 ? rowOffset : 0);
                const top = r * verticalStep;
                return (
                  <Box
                    key={`${r}-${c}`}
                    onClick={() => handleGridCellClick(r, c)}
                    sx={{
                      position: "absolute",
                      left,
                      top,
                      width: hexWidth,
                      height: hexHeight,
                      clipPath: "polygon(25% 7%, 75% 7%, 100% 50%, 75% 93%, 25% 93%, 0 50%)",
                      border: "1px solid rgba(255,255,255,0.15)",
                      backgroundColor: bg,
                      color: fg,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: Math.max(10, Math.floor(cellSize * 0.34)),
                      cursor: "pointer",
                      userSelect: "none"
                    }}
                  >
                    {cell !== "." ? cell : ""}
                  </Box>
                );
              })
            )}
          </Box>
        </Box>
      );
    }

    if (spaceType === "circle") {
      const minSide = Math.max(220, Math.min(560, Math.max(cols, rows) * 34));
      const center = minSide / 2;
      const outerRadius = center - 10;
      const ringStep = outerRadius / Math.max(1, rows);
      return (
        <Box sx={{ maxWidth: "100%", overflowX: "auto", py: 1 }}>
          <svg width={minSide} height={minSide} viewBox={`0 0 ${minSide} ${minSide}`}>
            {grid.map((row, r) =>
              row.map((cell, c) => {
                const innerRadius = ringStep * r;
                const outer = ringStep * (r + 1);
                const start = -Math.PI / 2 + (2 * Math.PI * c) / Math.max(1, cols);
                const end = -Math.PI / 2 + (2 * Math.PI * (c + 1)) / Math.max(1, cols);
                const path = describeAnnularSector(center, center, innerRadius, outer, start, end);
                const active = cell !== ".";
                const fill = active ? letterColor(cell, detectedColors) : "rgba(255,255,255,0.05)";
                const fg = active ? "#0f1116" : "rgba(255,255,255,0.45)";
                const labelAngle = (start + end) / 2;
                const labelRadius = innerRadius + (outer - innerRadius) * 0.58;
                const label = polarPoint(center, center, labelRadius, labelAngle);
                return (
                  <g key={`${r}-${c}`} onClick={() => handleGridCellClick(r, c)} style={{ cursor: "pointer" }}>
                    <path d={path} fill={fill} stroke="rgba(255,255,255,0.14)" strokeWidth={1} />
                    {cell !== "." && (
                      <text
                        x={label.x}
                        y={label.y}
                        textAnchor="middle"
                        dominantBaseline="middle"
                        fill={fg}
                        fontSize={Math.max(10, Math.floor(cellSize * 0.34))}
                        fontWeight={700}
                      >
                        {cell}
                      </text>
                    )}
                  </g>
                );
              })
            )}
          </svg>
        </Box>
      );
    }

    if (!topologySpec) {
      return null;
    }
    const nodes = topologySpec.nodes;
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const minX = Math.min(...nodes.map((node) => node.x));
    const maxX = Math.max(...nodes.map((node) => node.x));
    const minY = Math.min(...nodes.map((node) => node.y));
    const maxY = Math.max(...nodes.map((node) => node.y));
    const spanX = Math.max(0.001, maxX - minX);
    const spanY = Math.max(0.001, maxY - minY);
    const padding = 22;
    const maxSide = isMobile ? 360 : 560;
    const scale = Math.max(28, Math.min(82, (maxSide - padding * 2) / Math.max(spanX, spanY, 1)));
    const width = spanX * scale + padding * 2;
    const height = spanY * scale + padding * 2;
    const mapX = (x: number) => padding + (x - minX) * scale;
    const mapY = (y: number) => padding + (maxY - y) * scale;
    const nodeRadius = Math.max(9, Math.min(15, scale * 0.14));
    const fontSize = Math.max(10, Math.floor(nodeRadius * 0.95));
    return (
      <Box sx={{ maxWidth: "100%", overflowX: "auto", py: 1 }}>
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
          {topologySpec.edges.map(([u, v], idx) => {
            const a = nodeById.get(u);
            const b = nodeById.get(v);
            if (!a || !b) {
              return null;
            }
            return (
              <line
                key={`${u}-${v}-${idx}`}
                x1={mapX(a.x)}
                y1={mapY(a.y)}
                x2={mapX(b.x)}
                y2={mapY(b.y)}
                stroke="rgba(255,255,255,0.32)"
                strokeWidth={Math.max(2, scale * 0.04)}
              />
            );
          })}
          {nodes.map((node) => {
            const letter = graphNodeLetters[node.id] ?? ".";
            const active = letter !== ".";
            const fill = active ? letterColor(letter, detectedColors) : "rgba(255,255,255,0.08)";
            const fg = active ? "#0f1116" : "rgba(255,255,255,0.5)";
            return (
              <g key={node.id} onClick={() => handleNodeClick(node.id)} style={{ cursor: "pointer" }}>
                <circle
                  cx={mapX(node.x)}
                  cy={mapY(node.y)}
                  r={nodeRadius}
                  fill={fill}
                  stroke="rgba(255,255,255,0.22)"
                  strokeWidth={1.4}
                />
                {active && (
                  <text
                    x={mapX(node.x)}
                    y={mapY(node.y)}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill={fg}
                    fontSize={fontSize}
                    fontWeight={700}
                  >
                    {letter}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </Box>
    );
  }, [spaceType, cols, rows, cellSize, grid, graphNodeLetters, topologySpec, detectedColors, isMobile]);

  return (
    <Stack spacing={3}>
      {isMobile && (
        <Tabs
          value={mobilePanel}
          onChange={(_, value) => setMobilePanel(value)}
          variant="fullWidth"
          sx={{
            position: "sticky",
            top: 56,
            zIndex: 5,
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 2,
            backgroundColor: "rgba(15,17,22,0.96)",
            backdropFilter: "blur(12px)"
          }}
        >
          <Tab value="builder" label="Builder" />
          <Tab value="import" label="Screenshot" />
          <Tab value="preview" label="Preview" />
        </Tabs>
      )}
      <Grid container spacing={3}>
        {(!isMobile || mobilePanel === "builder") && (
          <Grid item xs={12} md={7}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  New Puzzle Builder
                </Typography>
                <Stack spacing={2}>
                  <Box display="flex" flexWrap="wrap" gap={2}>
                    <TextField
                      label="Type"
                      select
                      value={spaceType}
                      onChange={(event) => setSpaceType(event.target.value as BuilderType)}
                      size="small"
                      sx={{ width: 170 }}
                    >
                      <MenuItem value="square">square</MenuItem>
                      <MenuItem value="hex">hex</MenuItem>
                      <MenuItem value="circle">circle</MenuItem>
                      <MenuItem value="graph">graph</MenuItem>
                      <MenuItem value="cube">cube</MenuItem>
                      <MenuItem value="star">star</MenuItem>
                      <MenuItem value="figure8">figure8</MenuItem>
                    </TextField>
                    <TextField
                      label={spaceType === "circle" ? "Sectors" : graphLike ? "Detail / Width" : "Width"}
                      type="number"
                      value={cols}
                      onChange={(event) => setCols(Math.max(1, Number(event.target.value)))}
                      size="small"
                      inputProps={{ min: 1, max: 40 }}
                    />
                    <TextField
                      label={spaceType === "circle" ? "Rings" : graphLike ? "Height hint" : "Height"}
                      type="number"
                      value={rows}
                      onChange={(event) => setRows(Math.max(1, Number(event.target.value)))}
                      size="small"
                      inputProps={{ min: 1, max: 40 }}
                    />
                  </Box>
                  <Box display="flex" flexWrap="wrap" gap={2} alignItems="center">
                    <TextField
                      label="Name"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      size="small"
                      sx={{ minWidth: 220 }}
                    />
                    <FormControlLabel
                      control={<Switch checked={autoName} onChange={(event) => setAutoName(event.target.checked)} />}
                      label="Auto name"
                    />
                  </Box>
                  <Box>
                    <Typography variant="subtitle2" gutterBottom>
                      Color palette
                    </Typography>
                    <Box display="flex" flexWrap="wrap" gap={1} alignItems="center">
                      <Box
                        component="button"
                        type="button"
                        onClick={() => setSelectedColor(null)}
                        aria-label="Eraser"
                        title="Eraser"
                        sx={{
                          width: 38,
                          height: 38,
                          borderRadius: "50%",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          p: 0,
                          border:
                            selectedColor === null
                              ? "3px solid rgba(255,255,255,0.9)"
                              : "2px dashed rgba(255,255,255,0.35)",
                          backgroundColor: "transparent",
                          color: "rgba(255,255,255,0.75)"
                        }}
                      >
                        <BackspaceOutlined sx={{ fontSize: 18 }} />
                      </Box>
                      {LETTERS.map((letter) => {
                        const swatch = letterColor(letter, detectedColors);
                        const isSelected = selectedColor === letter;
                        const count = counts[letter] ?? 0;
                        return (
                          <Badge
                            key={letter}
                            overlap="circular"
                            badgeContent={count}
                            invisible={count === 0}
                            color={count === 2 ? "success" : "warning"}
                          >
                            <Box
                              component="button"
                              type="button"
                              onClick={() => setSelectedColor(letter)}
                              aria-label={`Color ${letter}`}
                              sx={{
                                width: 38,
                                height: 38,
                                borderRadius: "50%",
                                cursor: "pointer",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                p: 0,
                                fontWeight: 800,
                                fontSize: 14,
                                fontFamily: "inherit",
                                border: isSelected
                                  ? "3px solid rgba(255,255,255,0.95)"
                                  : "2px solid rgba(255,255,255,0.18)",
                                boxShadow: isSelected ? `0 0 0 3px ${swatch}55` : "none",
                                backgroundColor: swatch,
                                color: "#0f1116"
                              }}
                            >
                              {letter}
                            </Box>
                          </Badge>
                        );
                      })}
                    </Box>
                    <Typography variant="caption" color="text.secondary" display="block" mt={1}>
                      Tap two cells to place a pair — the palette advances to the next color automatically.
                      Tap a placed cell (or use the eraser) to remove it.
                    </Typography>
                  </Box>

                  {board}

                  <Box>
                    {invalidColors.length > 0 ? (
                      <Alert severity="warning">
                        Invalid colors (must be exactly 2): {invalidColors.join(", ")}
                      </Alert>
                    ) : usedColors.length === 0 ? (
                      <Alert severity="info">Add at least one color with exactly 2 nodes.</Alert>
                    ) : (
                      <Alert severity="success">Valid terminal pairs: {usedColors.join(", ")}</Alert>
                    )}
                  </Box>

                  {graphLike && (
                    <Stack spacing={2}>
                      <Typography variant="subtitle2">Graph edge overrides (optional)</Typography>
                      <Typography variant="caption" color="text.secondary">
                        One pair per line using <code>u v</code> or <code>u|v</code>. Node id examples:{" "}
                        <code>{graphNodeHint}</code>.
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
                      <Box>
                        <Button
                          variant="text"
                          size="small"
                          onClick={() => {
                            setEdgeAddText("");
                            setEdgeRemoveText("");
                            setEdgeWarpsText("");
                            setEdgeWallsText("");
                          }}
                        >
                          Clear edge overrides
                        </Button>
                      </Box>
                    </Stack>
                  )}

                  <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                    <Button
                      variant="contained"
                      size="large"
                      startIcon={<AutoAwesome />}
                      disabled={!canSubmit}
                      onClick={() => onCreatePuzzle(name, puzzleText, { autoSolve: true })}
                      sx={{ minHeight: 48 }}
                      fullWidth={isMobile}
                    >
                      Solve puzzle
                    </Button>
                    <Button
                      variant="outlined"
                      disabled={!canSubmit}
                      onClick={() => onCreatePuzzle(name, puzzleText)}
                      fullWidth={isMobile}
                    >
                      Open in editor
                    </Button>
                    <Button variant="outlined" disabled={!canSubmit} onClick={handleSave} fullWidth={isMobile}>
                      Save to library
                    </Button>
                    <Button
                      variant="text"
                      fullWidth={isMobile}
                      onClick={() => {
                        if (graphLike) {
                          setGraphNodeLetters({});
                        } else {
                          setGrid(buildGrid(rows, cols));
                        }
                        setSelectedColor("A");
                      }}
                    >
                      Clear board
                    </Button>
                  </Stack>
                  {(saveStatus || saveError) && (
                    <Alert severity={saveError ? "error" : "success"}>{saveError ?? saveStatus}</Alert>
                  )}
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        )}
        {(!isMobile || mobilePanel !== "builder") && (
          <Grid item xs={12} md={5}>
            <Stack spacing={2}>
              {(!isMobile || mobilePanel === "import") && (
                <ImageView
                  embedded
                  preferredTargetType={
                    spaceType === "cube" ||
                    spaceType === "star" ||
                    spaceType === "figure8" ||
                    spaceType === "graph"
                      ? spaceType
                      : undefined
                  }
                  onGenerated={(generatedName, generatedText) =>
                    onCreatePuzzle(generatedName, generatedText, { autoSolve: true })
                  }
                  onApplied={() => setMobilePanel("builder")}
                  onSuggestedName={(suggested) => {
                    setName(suggested);
                    setAutoName(false);
                  }}
                  onApplyGrid={applyDetectedGrid}
                />
              )}
              {(!isMobile || mobilePanel === "preview") && (
                <Card>
                  <CardContent>
                    <Typography variant="h6" gutterBottom>
                      Puzzle text
                    </Typography>
                    <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
                      {puzzleText}
                    </Box>
                  </CardContent>
                </Card>
              )}
            </Stack>
          </Grid>
        )}
      </Grid>
    </Stack>
  );
}
