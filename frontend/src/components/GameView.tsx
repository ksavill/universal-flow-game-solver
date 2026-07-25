import { Box } from "@mui/material";
import { useMemo } from "react";
import { SolveResponse } from "../api";
import { GAME_PALETTE, buildTerminalColorMaps } from "../colors";
import {
  SolutionPathEdges,
  SolutionPaths,
  buildAdjacencyKinds,
  buildBlockedAdjacencies,
  buildSolutionEdgeColors,
  canonicalEdgeKey
} from "../solutionEdges";
import {
  GameBoundarySegment,
  GamePoint,
  buildCirclePolygons,
  extractDisplayPolygons,
  extractNodeDataPolygons,
  findOuterBoundarySegments,
  terminalShapeForPolygon
} from "./gameViewGeometry";

type GameViewProps = {
  graph: SolveResponse["graph"];
  nodeColor?: Record<string, string | null> | null;
  pathEdges?: SolutionPathEdges | null;
  paths?: SolutionPaths | null;
  showSolution?: boolean;
  height?: number;
  cellSize?: number;
  compact?: boolean;
};

type RenderNode = {
  id: string;
  x: number;
  y: number;
  sx: number;
  sy: number;
  kind: string;
  tile: string | null;
  terminalColor: string | null;
  solutionColor: string | null;
  terminalShape?: {
    rx: number;
    ry: number;
    rotation: number;
  };
};

type RenderTile = {
  key: string;
  sx: number;
  sy: number;
  points: GamePoint[];
  solutionColor: string | null;
  isBridge: boolean;
};

type RenderBridge = {
  id: string;
  sx: number;
  sy: number;
  horizontalColor: string | null;
  verticalColor: string | null;
};

type RenderEdge = {
  u: string;
  v: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  length: number;
  warpLike: boolean;
  crossoverId: string | null;
  solutionColor: string | null;
};

type RenderCrossover = {
  id: string;
  u: string;
  v: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  cx: number;
  cy: number;
  underX: number;
  underY: number;
  overX: number;
  overY: number;
  solutionColor: string | null;
  surfaceColor: string | null;
};

type RenderBarrier = {
  id: string;
  u: string;
  v: string;
  cx: number;
  cy: number;
  perpendicularX: number;
  perpendicularY: number;
};

type RenderSegment = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

type RenderBoundary = GameBoundarySegment;

type RenderBounds = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
};

function median(values: number[]): number {
  if (!values.length) {
    return 1;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function buildWarpStubs(
  edge: RenderEdge,
  length: number,
  boardBounds?: RenderBounds
): RenderSegment[] {
  if (boardBounds) {
    const outwardStub = (x: number, y: number): RenderSegment => {
      const candidates = [
        { distance: Math.abs(x - boardBounds.minX), dx: -1, dy: 0 },
        { distance: Math.abs(boardBounds.maxX - x), dx: 1, dy: 0 },
        { distance: Math.abs(y - boardBounds.minY), dx: 0, dy: -1 },
        { distance: Math.abs(boardBounds.maxY - y), dx: 0, dy: 1 }
      ].sort((a, b) => a.distance - b.distance);
      const direction = candidates[0];
      return {
        x1: x,
        y1: y,
        x2: x + direction.dx * length,
        y2: y + direction.dy * length
      };
    };
    return [outwardStub(edge.x1, edge.y1), outwardStub(edge.x2, edge.y2)];
  }

  const dx = edge.x2 - edge.x1;
  const dy = edge.y2 - edge.y1;
  const magnitude = Math.hypot(dx, dy) || 1;
  const ux = dx / magnitude;
  const uy = dy / magnitude;

  return [
    {
      x1: edge.x1,
      y1: edge.y1,
      x2: edge.x1 - ux * length,
      y2: edge.y1 - uy * length
    },
    {
      x1: edge.x2,
      y1: edge.y2,
      x2: edge.x2 + ux * length,
      y2: edge.y2 + uy * length
    }
  ];
}

const LATTICE_EPS = 0.02;

function nearInteger(value: number): boolean {
  return Math.abs(value - Math.round(value)) < LATTICE_EPS;
}

export function GameView({
  graph,
  nodeColor,
  pathEdges,
  paths,
  showSolution = false,
  height = 320,
  cellSize,
  compact = false
}: GameViewProps) {
  const { colorToHex, terminalNodeColor } = useMemo(
    () => buildTerminalColorMaps(graph, nodeColor, GAME_PALETTE),
    [graph, nodeColor]
  );
  const solutionEdgeColors = useMemo(
    () => buildSolutionEdgeColors(pathEdges, paths),
    [pathEdges, paths]
  );
  const adjacencyKinds = useMemo(() => buildAdjacencyKinds(graph), [graph]);

  const rendered = useMemo(() => {
    const baseNodes = graph.nodes.map((node) => ({
      id: node.id,
      x: node.x,
      y: node.y,
      kind: node.kind,
      tile: typeof node.data?.tile === "string" ? node.data.tile : null,
      terminalColor: terminalNodeColor[node.id] ?? null,
      solutionColor: showSolution && nodeColor ? nodeColor[node.id] ?? null : null
    }));

    if (!baseNodes.length) {
      return {
        nodes: [] as RenderNode[],
        tiles: [] as RenderTile[],
        bridges: [] as RenderBridge[],
        crossovers: [] as RenderCrossover[],
        edges: [] as RenderEdge[],
        barriers: [] as RenderBarrier[],
        boundaries: [] as RenderBoundary[],
        gridMode: false,
        hexMode: false,
        polygonMode: false,
        circleMode: false,
        width: compact ? 220 : 320,
        height: compact ? 140 : height,
        scale: 24,
        cellMetric: 24,
        boardBounds: undefined as RenderBounds | undefined
      };
    }

    const displayPolygons = extractDisplayPolygons(graph);
    const nodeDataPolygons = displayPolygons.length ? [] : extractNodeDataPolygons(graph);
    const sourcePolygons = displayPolygons.length ? displayPolygons : nodeDataPolygons;
    const circlePolygons = sourcePolygons.length ? [] : buildCirclePolygons(graph);
    const rawPolygons = sourcePolygons.length ? sourcePolygons : circlePolygons;
    const polygonMode = sourcePolygons.length > 0;
    const circleMode = circlePolygons.length > 0;
    const geometryPoints = rawPolygons.length
      ? rawPolygons.flatMap((cell) => cell.points)
      : baseNodes.map((node) => ({ x: node.x, y: node.y }));
    const minX = Math.min(...geometryPoints.map((point) => point.x));
    const maxX = Math.max(...geometryPoints.map((point) => point.x));
    const minY = Math.min(...geometryPoints.map((point) => point.y));
    const maxY = Math.max(...geometryPoints.map((point) => point.y));
    const spanX = Math.max(0.001, maxX - minX);
    const spanY = Math.max(0.001, maxY - minY);

    const onLattice = baseNodes.every((node) => nearInteger(node.x) && nearInteger(node.y));
    const explicitHex = graph.topology?.template?.id === "hex_grid";
    const inferredHex =
      baseNodes.length > 1 &&
      baseNodes.some((node) => {
        const match = node.id.match(/^(-?\d+),(-?\d+)$/);
        return Boolean(match && Math.abs(Number(match[2])) % 2 === 1);
      }) &&
      baseNodes.every((node) => {
        const match = node.id.match(/^(-?\d+),(-?\d+)$/);
        if (!match) return false;
        const column = Number(match[1]);
        const row = Number(match[2]);
        return (
          Math.abs(node.x - (column + (Math.abs(row) % 2 ? 0.5 : 0))) < LATTICE_EPS &&
          Math.abs(node.y + row * (Math.sqrt(3) / 2)) < LATTICE_EPS
        );
      });
    const hexMode = !rawPolygons.length && (explicitHex || inferredHex);
    const boardLayout = rawPolygons.length > 0 || onLattice || hexMode;

    const padding = compact ? 12 : 18;
    const maxWidth = compact ? 320 : 700;
    const viewHeight = Math.max(96, compact ? 140 : height);
    const availableHeight = Math.max(1, viewHeight - padding * 2);
    const availableWidth = Math.max(1, maxWidth - padding * 2);
    // Grid boards reserve half a cell on every side so the outermost cell
    // squares fit inside the canvas (node coords are cell centers).
    const needsCellMargin = rawPolygons.length === 0 && boardLayout;
    const gridSpanX = spanX + (needsCellMargin ? 1 : 0);
    const gridSpanY = spanY + (needsCellMargin ? 1 : 0);
    const fitScale = Math.min(availableHeight / gridSpanY, availableWidth / gridSpanX);
    // Free-form imports use screenshot-pixel coordinates rather than grid units.
    // Let those graphs scale below a normal cell size so the canvas never grows
    // thousands of pixels tall. An explicit cell size is also capped to the fit.
    const scale = clamp(Math.min(cellSize ?? fitScale, fitScale), 0.001, 96);
    const contentWidth = gridSpanX * scale;
    const contentHeight = gridSpanY * scale;
    const width = Math.max(140, Math.min(maxWidth, contentWidth + padding * 2));
    const extraX = needsCellMargin ? scale / 2 : 0;
    const extraY = needsCellMargin ? scale / 2 : 0;
    const offsetX = (width - contentWidth) / 2 + extraX;
    const offsetY = (viewHeight - contentHeight) / 2 + extraY;

    const mapX = (x: number) => offsetX + (x - minX) * scale;
    const mapY = (y: number) => offsetY + (maxY - y) * scale;

    const polygonCells = rawPolygons.map((cell) => ({
      key: cell.key,
      center: { x: mapX(cell.center.x), y: mapY(cell.center.y) },
      points: cell.points.map((point) => ({ x: mapX(point.x), y: mapY(point.y) }))
    }));
    const polygonByKey = new Map(polygonCells.map((cell) => [cell.key, cell]));

    const nodeToPhysicalTile = new Map<string, string>();
    for (const [tile, channels] of Object.entries(graph.tiles ?? {})) {
      for (const channel of channels) {
        nodeToPhysicalTile.set(channel, tile);
      }
    }
    const nodes: RenderNode[] = baseNodes.map((node) => {
      const tileKey = node.tile ?? nodeToPhysicalTile.get(node.id) ?? node.id;
      const polygon = polygonByKey.get(tileKey) ?? polygonByKey.get(node.id);
      const fallbackRadius = clamp(scale * 0.31, compact ? 4 : 6, 30);
      return {
        ...node,
        tile: tileKey,
        sx: polygon?.center.x ?? mapX(node.x),
        sy: polygon?.center.y ?? mapY(node.y),
        terminalShape: polygon
          ? terminalShapeForPolygon(polygon.points, fallbackRadius)
          : undefined
      };
    });
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const barriers: RenderBarrier[] = [];
    for (const adjacency of buildBlockedAdjacencies(graph)) {
      const a = nodeById.get(adjacency.u);
      const b = nodeById.get(adjacency.v);
      if (!a || !b) {
        continue;
      }
      const dx = b.sx - a.sx;
      const dy = b.sy - a.sy;
      const magnitude = Math.hypot(dx, dy);
      if (magnitude < 0.001) {
        continue;
      }
      barriers.push({
        id: adjacency.id,
        u: a.id,
        v: b.id,
        cx: (a.sx + b.sx) / 2,
        cy: (a.sy + b.sy) / 2,
        perpendicularX: -dy / magnitude,
        perpendicularY: dx / magnitude
      });
    }
    const bridgeGroups = new Map<string, { horizontal?: RenderNode; vertical?: RenderNode }>();
    for (const node of nodes) {
      if (node.kind !== "bridge_h" && node.kind !== "bridge_v") {
        continue;
      }
      const tile = node.tile ?? node.id.replace(/:[hv]$/, "");
      const group = bridgeGroups.get(tile) ?? {};
      if (node.kind === "bridge_h") {
        group.horizontal = node;
      } else {
        group.vertical = node;
      }
      bridgeGroups.set(tile, group);
    }
    const bridges: RenderBridge[] = Array.from(bridgeGroups.entries()).map(([id, group]) => {
      const anchor = group.horizontal ?? group.vertical!;
      return {
        id,
        sx: anchor.sx,
        sy: anchor.sy,
        horizontalColor: group.horizontal?.solutionColor ?? null,
        verticalColor: group.vertical?.solutionColor ?? null
      };
    });

    type CrossoverDefinition = {
      id: string;
      underPath: string[];
      surfaceChannel: string | null;
      underChannel: string | null;
      surfaceNeighbors: string[];
    };
    const stringArray = (value: unknown): string[] =>
      Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];
    const crossoverDefinitions = new Map<string, CrossoverDefinition>();
    const topologyCrossovers = graph.topology?.data?.crossovers;
    if (Array.isArray(topologyCrossovers)) {
      for (const raw of topologyCrossovers) {
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
        const item = raw as Record<string, unknown>;
        if (typeof item.id !== "string") continue;
        crossoverDefinitions.set(item.id, {
          id: item.id,
          underPath: stringArray(item.under_path),
          surfaceChannel:
            typeof item.surface_channel === "string" ? item.surface_channel : null,
          underChannel:
            typeof item.under_channel === "string" ? item.under_channel : null,
          surfaceNeighbors: stringArray(item.surface_neighbors)
        });
      }
    }
    const crossoverAdjacencies = new Map<string, string>();
    for (const adjacency of graph.adjacencies ?? []) {
      const data = adjacency.data ?? {};
      const mechanic = typeof data.mechanic === "string" ? data.mechanic : "";
      const crossoverId =
        typeof data.crossover_id === "string"
          ? data.crossover_id
          : adjacency.group?.startsWith("crossover-")
            ? adjacency.group
            : null;
      if (
        adjacency.state === "open" &&
        adjacency.kind === "seam" &&
        mechanic === "crossover" &&
        crossoverId
      ) {
        const previous = crossoverDefinitions.get(crossoverId);
        crossoverDefinitions.set(crossoverId, {
          id: crossoverId,
          underPath:
            previous?.underPath.length
              ? previous.underPath
              : stringArray(data.continuation),
          surfaceChannel:
            previous?.surfaceChannel ??
            (typeof data.surface_channel === "string" ? data.surface_channel : null),
          underChannel:
            previous?.underChannel ??
            (typeof data.under_channel === "string" ? data.under_channel : null),
          surfaceNeighbors: previous?.surfaceNeighbors ?? []
        });
        crossoverAdjacencies.set(
          canonicalEdgeKey(adjacency.a.channel, adjacency.b.channel),
          crossoverId
        );
      }
    }

    const lengths: number[] = [];
    const rawEdges: Array<Omit<RenderEdge, "warpLike" | "crossoverId">> = [];
    for (const [u, v] of graph.edges) {
      const a = nodeById.get(u);
      const b = nodeById.get(v);
      if (!a || !b) {
        continue;
      }
      const length = Math.hypot(a.x - b.x, a.y - b.y);
      lengths.push(length);
      rawEdges.push({
        u,
        v,
        x1: a.sx,
        y1: a.sy,
        x2: b.sx,
        y2: b.sy,
        length,
        solutionColor: showSolution
          ? solutionEdgeColors.get(canonicalEdgeKey(u, v)) ?? null
          : null
      });
    }

    const medianLength = Math.max(0.001, median(lengths));
    const hasTypedAdjacencies = adjacencyKinds.size > 0;
    const edges: RenderEdge[] = rawEdges.map((edge) => {
      const key = canonicalEdgeKey(edge.u, edge.v);
      const kind = adjacencyKinds.get(key);
      return {
        ...edge,
        crossoverId: crossoverAdjacencies.get(key) ?? null,
        warpLike:
          kind === "warp" ||
          (!hasTypedAdjacencies &&
            rawPolygons.length === 0 &&
            (onLattice || hexMode) &&
            edge.length > medianLength * 1.7)
      };
    });
    const crossovers: RenderCrossover[] = edges.flatMap((edge) => {
      if (!edge.crossoverId) {
        return [];
      }
      const definition = crossoverDefinitions.get(edge.crossoverId);
      const pathNodes = (definition?.underPath ?? [])
        .map((nodeId) => nodeById.get(nodeId))
        .filter((node): node is RenderNode => node !== undefined);
      const approachNode = pathNodes.length >= 3 ? pathNodes[0] : null;
      const continuationNode =
        pathNodes.length >= 3 ? pathNodes[pathNodes.length - 1] : null;
      const underNode = definition?.underChannel
        ? nodeById.get(definition.underChannel)
        : undefined;
      const surfaceNode = definition?.surfaceChannel
        ? nodeById.get(definition.surfaceChannel)
        : undefined;
      const x1 = approachNode?.sx ?? edge.x1;
      const y1 = approachNode?.sy ?? edge.y1;
      const x2 = continuationNode?.sx ?? edge.x2;
      const y2 = continuationNode?.sy ?? edge.y2;
      const cx = surfaceNode?.sx ?? underNode?.sx ?? (edge.x1 + edge.x2) / 2;
      const cy = surfaceNode?.sy ?? underNode?.sy ?? (edge.y1 + edge.y2) / 2;
      const dx = x2 - x1;
      const dy = y2 - y1;
      const magnitude = Math.hypot(dx, dy);
      if (magnitude < 0.001) {
        return [];
      }
      const underX = dx / magnitude;
      const underY = dy / magnitude;
      const surfaceNeighborNodes = (definition?.surfaceNeighbors ?? [])
        .map((nodeId) => nodeById.get(nodeId))
        .filter((node): node is RenderNode => node !== undefined);
      let overX = -underY;
      let overY = underX;
      if (surfaceNeighborNodes.length >= 2) {
        let bestPair: [RenderNode, RenderNode] = [
          surfaceNeighborNodes[0],
          surfaceNeighborNodes[1]
        ];
        let bestDistance = -1;
        for (let leftIndex = 0; leftIndex < surfaceNeighborNodes.length; leftIndex += 1) {
          for (
            let rightIndex = leftIndex + 1;
            rightIndex < surfaceNeighborNodes.length;
            rightIndex += 1
          ) {
            const left = surfaceNeighborNodes[leftIndex];
            const right = surfaceNeighborNodes[rightIndex];
            const distance = Math.hypot(left.sx - right.sx, left.sy - right.sy);
            if (distance > bestDistance) {
              bestDistance = distance;
              bestPair = [left, right];
            }
          }
        }
        const overMagnitude =
          Math.hypot(bestPair[1].sx - bestPair[0].sx, bestPair[1].sy - bestPair[0].sy) || 1;
        overX = (bestPair[1].sx - bestPair[0].sx) / overMagnitude;
        overY = (bestPair[1].sy - bestPair[0].sy) / overMagnitude;
      } else if (surfaceNeighborNodes.length === 1) {
        const overMagnitude =
          Math.hypot(surfaceNeighborNodes[0].sx - cx, surfaceNeighborNodes[0].sy - cy) || 1;
        overX = (surfaceNeighborNodes[0].sx - cx) / overMagnitude;
        overY = (surfaceNeighborNodes[0].sy - cy) / overMagnitude;
      }
      return [
        {
          id: edge.crossoverId,
          u: edge.u,
          v: edge.v,
          x1,
          y1,
          x2,
          y2,
          cx,
          cy,
          underX,
          underY,
          overX,
          overY,
          solutionColor: underNode?.solutionColor ?? edge.solutionColor,
          surfaceColor: surfaceNode?.solutionColor ?? null
        }
      ];
    });

    // Square boards require an integer lattice and unit axis steps. Region and
    // circle boards are already represented by their physical cell polygons.
    const axisAligned = edges.every((edge) => {
      if (edge.warpLike) {
        return true;
      }
      const dx = Math.abs(edge.x2 - edge.x1) / scale;
      const dy = Math.abs(edge.y2 - edge.y1) / scale;
      return (
        (Math.abs(dx - 1) < LATTICE_EPS && dy < LATTICE_EPS) ||
        (Math.abs(dy - 1) < LATTICE_EPS && dx < LATTICE_EPS)
      );
    });
    const gridMode = !hexMode && onLattice && axisAligned && edges.length > 0;
    const gameBoardMode = gridMode || hexMode || rawPolygons.length > 0;

    const tileMap = new Map<string, RenderTile>();
    if (rawPolygons.length) {
      for (const polygon of polygonCells) {
        const channelIds = graph.tiles?.[polygon.key] ?? [polygon.key];
        const channelNodes = channelIds
          .map((channel) => nodeById.get(channel))
          .filter((node): node is RenderNode => node !== undefined);
        const isBridge = channelNodes.length > 1 || channelNodes.some(
          (node) => node.kind === "bridge_h" || node.kind === "bridge_v"
        );
        const solutionColors = [
          ...new Set(
            channelNodes
              .map((node) => node.solutionColor)
              .filter((color): color is string => Boolean(color))
          )
        ];
        tileMap.set(polygon.key, {
          key: polygon.key,
          sx: polygon.center.x,
          sy: polygon.center.y,
          points: polygon.points,
          solutionColor: !isBridge && solutionColors.length === 1 ? solutionColors[0] : null,
          isBridge
        });
      }
    } else if (gameBoardMode) {
      for (const node of nodes) {
        const key = hexMode ? node.id : `${Math.round(node.x)}:${Math.round(node.y)}`;
        const isBridge = node.kind === "bridge_h" || node.kind === "bridge_v";
        const existing = tileMap.get(key);
        if (existing) {
          existing.isBridge = existing.isBridge || isBridge;
          continue;
        }
        const points = hexMode
          ? Array.from({ length: 6 }, (_value, index) => {
              const radius = scale / Math.sqrt(3);
              const angle = (Math.PI / 180) * (30 + index * 60);
              return {
                x: node.sx + radius * Math.cos(angle),
                y: node.sy + radius * Math.sin(angle)
              };
            })
          : [
              { x: node.sx - scale / 2, y: node.sy - scale / 2 },
              { x: node.sx + scale / 2, y: node.sy - scale / 2 },
              { x: node.sx + scale / 2, y: node.sy + scale / 2 },
              { x: node.sx - scale / 2, y: node.sy + scale / 2 }
            ];
        tileMap.set(key, {
          key,
          sx: node.sx,
          sy: node.sy,
          points,
          solutionColor: isBridge ? null : node.solutionColor,
          isBridge
        });
      }
    }

    const tiles = Array.from(tileMap.values());
    const tilePoints = tiles.flatMap((tile) => tile.points);
    const boardBounds: RenderBounds | undefined = tilePoints.length
      ? {
          minX: Math.min(...tilePoints.map((point) => point.x)),
          maxX: Math.max(...tilePoints.map((point) => point.x)),
          minY: Math.min(...tilePoints.map((point) => point.y)),
          maxY: Math.max(...tilePoints.map((point) => point.y))
        }
      : undefined;
    const screenEdgeLengths = edges
      .filter((edge) => !edge.warpLike)
      .map((edge) => Math.hypot(edge.x2 - edge.x1, edge.y2 - edge.y1));
    const polygonCellSizes = tiles.map((tile) => {
      const xs = tile.points.map((point) => point.x);
      const ys = tile.points.map((point) => point.y);
      return Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    });
    const cellMetric = Math.max(
      6,
      median(screenEdgeLengths.length ? screenEdgeLengths : polygonCellSizes)
    );
    const boundaryCells = tiles.map((tile) => ({
      key: tile.key,
      center: { x: tile.sx, y: tile.sy },
      points: tile.points
    }));
    const boundaries = findOuterBoundarySegments(
      boundaryCells,
      Math.max(
        0.65,
        cellMetric * (nodeDataPolygons.length > 0 ? 0.24 : polygonMode ? 0.13 : 0.035)
      )
    );

    return {
      nodes,
      tiles,
      edges,
      bridges,
      crossovers,
      barriers,
      boundaries,
      gridMode,
      hexMode,
      polygonMode,
      circleMode,
      width,
      height: viewHeight,
      scale,
      cellMetric,
      boardBounds
    };
  }, [
    graph,
    nodeColor,
    showSolution,
    terminalNodeColor,
    compact,
    height,
    cellSize,
    solutionEdgeColors,
    adjacencyKinds
  ]);

  const boardMode =
    rendered.gridMode || rendered.hexMode || rendered.polygonMode || rendered.circleMode;
  const metric = rendered.cellMetric;
  const nodeRadius = clamp(metric * 0.12, compact ? 2.5 : 3, compact ? 6 : 9);
  const terminalRadius = boardMode
    ? clamp(metric * 0.32, compact ? 4 : 6, 30)
    : nodeRadius * 1.55;
  const baseEdgeWidth = clamp(metric * 0.055, 1, compact ? 1.8 : 2.4);
  const solutionEdgeWidth = boardMode
    ? clamp(metric * 0.3, compact ? 3.5 : 5, 26)
    : clamp(metric * 0.2, compact ? 2.5 : 3.2, compact ? 5 : 8);
  const warpStubLength = clamp(metric * 0.72, compact ? 8 : 12, compact ? 20 : 34);
  // Walls span the full shared cell boundary so they read as solid walls, the
  // way the game draws them; the fallback view keeps a shorter tick.
  const barrierHalfLength = boardMode
    ? metric / 2
    : clamp(metric * 0.24, compact ? 4 : 5, compact ? 7 : 10);
  const barrierWidth = boardMode
    ? clamp(metric * 0.13, 2.5, 8)
    : clamp(metric * 0.09, compact ? 2 : 2.5, compact ? 3 : 4);
  const catalogGeometry =
    graph.catalog && typeof graph.catalog.geometry === "string"
      ? graph.catalog.geometry
      : "";
  const metaGeometry =
    graph.meta && typeof graph.meta.level_type_geometry === "string"
      ? graph.meta.level_type_geometry
      : "";
  const referenceGeometry = (metaGeometry || catalogGeometry).toLowerCase();
  const circleTheme =
    rendered.circleMode || referenceGeometry === "circle" || referenceGeometry === "figure8";
  const boardAccent = circleTheme
    ? "#8bff90"
    : rendered.hexMode || rendered.polygonMode
      ? "#ff9099"
      : "#99b6ff";
  const innerGridColor = circleTheme
    ? "rgba(54,139,65,0.72)"
    : rendered.hexMode || rendered.polygonMode
      ? "rgba(139,67,78,0.72)"
      : "rgba(61,84,136,0.74)";
  const innerGridWidth = clamp(metric * 0.035, 0.9, 2.3);
  const outerBorderWidth = clamp(metric * 0.16, compact ? 3 : 4.5, compact ? 8 : 12);
  const crossoverArm = clamp(metric * 0.62, compact ? 7 : 10, compact ? 19 : 32);
  const crossoverLaneGap = clamp(
    outerBorderWidth * 0.5,
    compact ? 1.8 : 2.4,
    compact ? 4 : 6
  );
  const crossoverRailWidth = clamp(
    outerBorderWidth * 0.3,
    compact ? 1.2 : 1.5,
    compact ? 2.8 : 3.8
  );

  return (
    <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", width: "100%", overflowX: "auto" }}>
      <svg
        width={rendered.width}
        height={rendered.height}
        viewBox={`0 0 ${rendered.width} ${rendered.height}`}
        data-game-board={boardMode ? "true" : "false"}
        data-game-layout={
          rendered.circleMode
            ? "circle"
            : rendered.polygonMode
              ? "polygon"
              : rendered.hexMode
                ? "hex"
                : rendered.gridMode
                  ? "square"
                  : "graph"
        }
        data-cell-count={rendered.tiles.length}
        data-boundary-count={rendered.boundaries.length}
        data-crossover-count={rendered.crossovers.length}
        style={{
          background: "radial-gradient(circle at 50% 46%, #171721 0%, #050507 76%)",
          borderRadius: compact ? 6 : 10,
          border: "1px solid rgba(255,255,255,0.06)",
          minWidth: rendered.width
        }}
      >
        {boardMode &&
          rendered.tiles.map((tile) => {
            const highlight =
              showSolution && tile.solutionColor ? colorToHex[tile.solutionColor] ?? null : null;
            return (
              <polygon
                key={`tile-${tile.key}`}
                data-game-cell={tile.key}
                points={tile.points.map((point) => `${point.x},${point.y}`).join(" ")}
                fill={highlight ? `${highlight}20` : "rgba(255,255,255,0.01)"}
                stroke={innerGridColor}
                strokeWidth={innerGridWidth}
                strokeLinejoin="round"
              />
            );
          })}

        {boardMode &&
          rendered.boundaries.map((boundary, index) => (
            <line
              key={`outer-${boundary.key}-${index}`}
              data-game-boundary=""
              x1={boundary.x1}
              y1={boundary.y1}
              x2={boundary.x2}
              y2={boundary.y2}
              stroke={boardAccent}
              strokeWidth={outerBorderWidth}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}

        {rendered.edges.map((edge) => {
          if (!edge.warpLike) {
            if (boardMode) {
              return null;
            }
            return (
              <line
                key={`edge-${edge.u}-${edge.v}`}
                x1={edge.x1}
                y1={edge.y1}
                x2={edge.x2}
                y2={edge.y2}
                stroke="rgba(156,163,175,0.42)"
                strokeWidth={baseEdgeWidth}
              />
            );
          }

          const stubs = buildWarpStubs(
            edge,
            warpStubLength,
            boardMode ? rendered.boardBounds : undefined
          );
          return (
            <g
              key={`edge-${edge.u}-${edge.v}`}
              aria-label={`Warp connection from ${edge.u} to ${edge.v}`}
              data-game-warp=""
            >
              <title>Warp continues at the matching portal</title>
              {stubs.map((stub, index) => {
                const dx = stub.x2 - stub.x1;
                const dy = stub.y2 - stub.y1;
                const magnitude = Math.hypot(dx, dy) || 1;
                const px = (-dy / magnitude) * baseEdgeWidth * 1.45;
                const py = (dx / magnitude) * baseEdgeWidth * 1.45;
                return (
                  <g key={index}>
                    <line
                      x1={stub.x1}
                      y1={stub.y1}
                      x2={stub.x2}
                      y2={stub.y2}
                      stroke="rgba(5,5,7,0.98)"
                      strokeWidth={Math.max(outerBorderWidth + 2, baseEdgeWidth * 4.2)}
                      strokeLinecap="butt"
                    />
                    {[-1, 1].map((side) => (
                      <line
                        key={side}
                        x1={stub.x1 + px * side}
                        y1={stub.y1 + py * side}
                        x2={stub.x2 + px * side}
                        y2={stub.y2 + py * side}
                        stroke="rgba(153,174,224,0.78)"
                        strokeWidth={baseEdgeWidth}
                        strokeDasharray={`${baseEdgeWidth * 1.15} ${baseEdgeWidth * 1.7}`}
                        strokeLinecap="round"
                      />
                    ))}
                  </g>
                );
              })}
            </g>
          );
        })}

        {showSolution &&
          rendered.edges.map((edge) => {
            if (!edge.solutionColor || edge.crossoverId) {
              return null;
            }
            const hex = colorToHex[edge.solutionColor] ?? "#ff5252";
            if (edge.warpLike) {
                const stubs = buildWarpStubs(
                  edge,
                  warpStubLength,
                  boardMode ? rendered.boardBounds : undefined
                );
              return (
                <g key={`sol-${edge.u}-${edge.v}`} aria-label={`Solved warp from ${edge.u} to ${edge.v}`}>
                  <title>Solution warps to the matching portal</title>
                  {stubs.map((stub, index) => (
                    <line
                      key={index}
                      x1={stub.x1}
                      y1={stub.y1}
                      x2={stub.x2}
                      y2={stub.y2}
                      stroke={hex}
                      strokeWidth={solutionEdgeWidth}
                      strokeDasharray={`${solutionEdgeWidth * 0.95} ${solutionEdgeWidth * 0.38}`}
                      strokeLinecap="round"
                    />
                  ))}
                </g>
              );
            }
            return (
              <line
                key={`sol-${edge.u}-${edge.v}`}
                x1={edge.x1}
                y1={edge.y1}
                x2={edge.x2}
                y2={edge.y2}
                stroke={hex}
                strokeWidth={solutionEdgeWidth}
                strokeLinecap="round"
              />
            );
          })}

        {rendered.crossovers.map((crossover) => {
          const underColor = crossover.solutionColor
            ? colorToHex[crossover.solutionColor] ?? "#ff5252"
            : innerGridColor;
          const surfaceColor = crossover.surfaceColor
            ? colorToHex[crossover.surfaceColor] ?? "#ff5252"
            : null;
          const underWidth = crossover.solutionColor
            ? solutionEdgeWidth
            : Math.max(baseEdgeWidth * 1.35, innerGridWidth);
          const overStartX = crossover.cx - crossover.overX * crossoverArm;
          const overStartY = crossover.cy - crossover.overY * crossoverArm;
          const overEndX = crossover.cx + crossover.overX * crossoverArm;
          const overEndY = crossover.cy + crossover.overY * crossoverArm;
          return (
            <g
              key={`crossover-${crossover.id}-${crossover.u}-${crossover.v}`}
              aria-label={`Crossover from ${crossover.u} to ${crossover.v} under the overlapping track`}
              data-game-crossover=""
              data-crossover-id={crossover.id}
              data-crossover-under={`${crossover.u},${crossover.v}`}
            >
              <title>The path continues underneath the overlapping track</title>
              <line
                x1={crossover.x1}
                y1={crossover.y1}
                x2={crossover.x2}
                y2={crossover.y2}
                stroke="rgba(5,5,7,0.98)"
                strokeWidth={underWidth + 3}
                strokeLinecap="round"
              />
              <line
                data-crossover-layer="under"
                x1={crossover.x1}
                y1={crossover.y1}
                x2={crossover.x2}
                y2={crossover.y2}
                stroke={underColor}
                strokeWidth={underWidth}
                strokeLinecap="round"
              />
              <line
                x1={overStartX}
                y1={overStartY}
                x2={overEndX}
                y2={overEndY}
                stroke="rgba(5,5,7,0.99)"
                strokeWidth={Math.max(solutionEdgeWidth + 3, outerBorderWidth + 3)}
                strokeLinecap="butt"
              />
              {[-crossoverLaneGap, crossoverLaneGap].map((offset) => (
                <line
                  key={offset}
                  data-crossover-layer="over"
                  x1={overStartX + crossover.underX * offset}
                  y1={overStartY + crossover.underY * offset}
                  x2={overEndX + crossover.underX * offset}
                  y2={overEndY + crossover.underY * offset}
                  stroke={boardAccent}
                  strokeWidth={crossoverRailWidth}
                  strokeLinecap="round"
                />
              ))}
              {surfaceColor && (
                <>
                  <line
                    x1={overStartX}
                    y1={overStartY}
                    x2={overEndX}
                    y2={overEndY}
                    stroke="rgba(5,5,7,0.99)"
                    strokeWidth={solutionEdgeWidth + 3}
                    strokeLinecap="round"
                  />
                  <line
                    data-crossover-layer="surface"
                    x1={overStartX}
                    y1={overStartY}
                    x2={overEndX}
                    y2={overEndY}
                    stroke={surfaceColor}
                    strokeWidth={solutionEdgeWidth}
                    strokeLinecap="round"
                  />
                </>
              )}
            </g>
          );
        })}

        {rendered.bridges.map((bridge) => {
          const horizontal = bridge.horizontalColor
            ? colorToHex[bridge.horizontalColor] ?? "#ff5252"
            : "rgba(230,233,240,0.88)";
          const vertical = bridge.verticalColor
            ? colorToHex[bridge.verticalColor] ?? "#ff5252"
            : null;
          const arm = Math.max(nodeRadius * 2.55, baseEdgeWidth * 3.8);
          const laneGap = arm * 0.24;
          const archHalf = arm * 0.27;
          const archHeight = arm * 0.36;
          const channelWidth = showSolution ? solutionEdgeWidth : baseEdgeWidth * 1.5;
          const bridgePath = (y: number) =>
            `M ${bridge.sx - arm} ${y} L ${bridge.sx - archHalf} ${y} ` +
            `Q ${bridge.sx} ${y - archHeight} ${bridge.sx + archHalf} ${y} ` +
            `L ${bridge.sx + arm} ${y}`;
          return (
            <g
              key={`bridge-${bridge.id}`}
              aria-label={`Bridge cell ${bridge.id}`}
              data-game-bridge=""
            >
              <title>Two paths cross without connecting</title>
              {vertical && (
                <>
                  <line
                    x1={bridge.sx}
                    y1={bridge.sy - arm}
                    x2={bridge.sx}
                    y2={bridge.sy + arm}
                    stroke="rgba(5,5,7,0.98)"
                    strokeWidth={channelWidth + 3}
                    strokeLinecap="round"
                  />
                  <line
                    x1={bridge.sx}
                    y1={bridge.sy - arm}
                    x2={bridge.sx}
                    y2={bridge.sy + arm}
                    stroke={vertical}
                    strokeWidth={channelWidth}
                    strokeLinecap="round"
                  />
                </>
              )}
              {[-laneGap, laneGap].map((offset) => (
                <path
                  key={offset}
                  d={bridgePath(bridge.sy + offset)}
                  fill="none"
                  stroke={horizontal}
                  strokeWidth={showSolution && bridge.horizontalColor ? channelWidth * 0.48 : channelWidth}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ))}
            </g>
          );
        })}

        {rendered.barriers.map((barrier) => {
          const offsetX = barrier.perpendicularX * barrierHalfLength;
          const offsetY = barrier.perpendicularY * barrierHalfLength;
          return (
            <g
              key={`barrier-${barrier.id}`}
              aria-label={`Barrier between ${barrier.u} and ${barrier.v}`}
              data-game-wall=""
            >
              <title>Blocked path</title>
              <line
                x1={barrier.cx - offsetX}
                y1={barrier.cy - offsetY}
                x2={barrier.cx + offsetX}
                y2={barrier.cy + offsetY}
                stroke="rgba(15,15,26,0.98)"
                strokeWidth={barrierWidth + 3}
                strokeLinecap={boardMode ? "butt" : "round"}
              />
              <line
                x1={barrier.cx - offsetX}
                y1={barrier.cy - offsetY}
                x2={barrier.cx + offsetX}
                y2={barrier.cy + offsetY}
                stroke={boardMode ? "#e8ecf4" : "#ff9dad"}
                strokeWidth={barrierWidth}
                strokeLinecap={boardMode ? "butt" : "round"}
              />
            </g>
          );
        })}

        {rendered.nodes.map((node) => {
          if (node.kind === "bridge_h" || node.kind === "bridge_v") {
            return null;
          }
          const terminal = node.terminalColor ? colorToHex[node.terminalColor] ?? "#ff5252" : null;
          const solved = node.solutionColor ? colorToHex[node.solutionColor] ?? "#ff5252" : null;
          if (boardMode && !terminal) {
            // The game leaves non-terminal cells empty; pipes and cell tints
            // already show solved coverage.
            return null;
          }
          const fill = terminal ?? solved ?? "rgba(220,220,220,0.86)";
          const radius = terminal ? terminalRadius : nodeRadius;
          const shape =
            terminal && node.terminalShape
              ? node.terminalShape
              : { rx: radius, ry: radius, rotation: 0 };
          return (
            <ellipse
              key={`node-${node.id}`}
              cx={node.sx}
              cy={node.sy}
              rx={shape.rx}
              ry={shape.ry}
              transform={
                shape.rotation
                  ? `rotate(${shape.rotation} ${node.sx} ${node.sy})`
                  : undefined
              }
              fill={fill}
              stroke={terminal ? "rgba(255,255,255,0.22)" : "rgba(0,0,0,0.35)"}
              strokeWidth={terminal ? 1.2 : 1}
            />
          );
        })}
      </svg>
    </Box>
  );
}
