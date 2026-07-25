import type { SolveResponse } from "../api";

export type GamePoint = {
  x: number;
  y: number;
};

export type GamePolygonCell = {
  key: string;
  center: GamePoint;
  points: GamePoint[];
};

export type GameBoundarySegment = {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

export type TerminalShape = {
  rx: number;
  ry: number;
  rotation: number;
};

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function pointFromUnknown(value: unknown): GamePoint | undefined {
  if (!Array.isArray(value) || value.length < 2) return undefined;
  const x = finiteNumber(value[0]);
  const y = finiteNumber(value[1]);
  return x === undefined || y === undefined ? undefined : { x, y };
}

function polygonCenter(points: GamePoint[], fallback?: GamePoint): GamePoint {
  if (fallback) return fallback;
  return {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  };
}

export function extractDisplayPolygons(graph: SolveResponse["graph"]): GamePolygonCell[] {
  const display = graph.display;
  if (!display || typeof display !== "object") return [];
  const cells = (display as Record<string, unknown>).cells;
  if (!cells || typeof cells !== "object" || Array.isArray(cells)) return [];

  const result: GamePolygonCell[] = [];
  for (const [key, rawCell] of Object.entries(cells as Record<string, unknown>)) {
    if (!rawCell || typeof rawCell !== "object" || Array.isArray(rawCell)) continue;
    const cell = rawCell as Record<string, unknown>;
    if (!Array.isArray(cell.polygon)) continue;
    const points = cell.polygon
      .map(pointFromUnknown)
      .filter((point): point is GamePoint => point !== undefined);
    if (points.length < 3) continue;

    const position = pointFromUnknown(cell.position);
    result.push({ key, center: polygonCenter(points, position), points });
  }
  return result;
}

export function extractNodeDataPolygons(graph: SolveResponse["graph"]): GamePolygonCell[] {
  const nodeToTile = new Map<string, string>();
  for (const [tile, channels] of Object.entries(graph.tiles ?? {})) {
    channels.forEach((channel) => nodeToTile.set(channel, tile));
  }

  const result = new Map<string, GamePolygonCell>();
  for (const node of graph.nodes) {
    const rawPolygon = node.data?.polygon;
    if (!Array.isArray(rawPolygon)) continue;
    const rawPoints = rawPolygon
      .map(pointFromUnknown)
      .filter((point): point is GamePoint => point !== undefined);
    if (rawPoints.length < 3) continue;

    const rawCenter = polygonCenter(rawPoints);
    const flipX = Math.abs(-rawCenter.x - node.x) < Math.abs(rawCenter.x - node.x);
    const flipY = Math.abs(-rawCenter.y - node.y) < Math.abs(rawCenter.y - node.y);
    const orientedCenter = {
      x: rawCenter.x * (flipX ? -1 : 1),
      y: rawCenter.y * (flipY ? -1 : 1),
    };
    const offsetX = node.x - orientedCenter.x;
    const offsetY = node.y - orientedCenter.y;
    const points = rawPoints.map((point) => ({
      x: point.x * (flipX ? -1 : 1) + offsetX,
      y: point.y * (flipY ? -1 : 1) + offsetY,
    }));
    const key =
      (typeof node.data?.tile === "string" ? node.data.tile : undefined) ??
      nodeToTile.get(node.id) ??
      node.id;
    if (!result.has(key)) {
      result.set(key, { key, center: { x: node.x, y: node.y }, points });
    }
  }
  return [...result.values()];
}

type CircleNode = {
  id: string;
  x: number;
  y: number;
  sector: number;
  ring: number;
  angle: number;
  radius: number;
};

function positiveAngleDifference(a: number, b: number): number {
  let difference = Math.abs(a - b) % (2 * Math.PI);
  if (difference > Math.PI) difference = 2 * Math.PI - difference;
  return difference;
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function buildCirclePolygons(graph: SolveResponse["graph"]): GamePolygonCell[] {
  const parsed: CircleNode[] = [];
  for (const node of graph.nodes) {
    const match = /^(\d+),(\d+)$/.exec(node.id);
    if (!match) return [];
    const sector = Number(match[1]);
    const ring = Number(match[2]);
    const radius = Math.hypot(node.x, node.y);
    if (!Number.isFinite(radius) || radius <= 0) return [];
    parsed.push({
      id: node.id,
      x: node.x,
      y: node.y,
      sector,
      ring,
      angle: Math.atan2(node.y, node.x),
      radius,
    });
  }

  const sectorIds = [...new Set(parsed.map((node) => node.sector))].sort((a, b) => a - b);
  const ringIds = [...new Set(parsed.map((node) => node.ring))].sort((a, b) => a - b);
  if (sectorIds.length < 5 || ringIds.length < 1) return [];
  if (sectorIds[0] !== 0 || ringIds[0] !== 0) return [];
  if (
    sectorIds[sectorIds.length - 1] !== sectorIds.length - 1 ||
    ringIds[ringIds.length - 1] !== ringIds.length - 1
  ) {
    return [];
  }

  const ringRadii = ringIds.map((ring) => median(parsed.filter((node) => node.ring === ring).map((node) => node.radius)));
  for (let index = 1; index < ringRadii.length; index += 1) {
    if (ringRadii[index] <= ringRadii[index - 1]) return [];
  }

  const sortedAngles = parsed
    .filter((node) => node.ring === ringIds[0])
    .sort((a, b) => a.sector - b.sector)
    .map((node) => node.angle);
  if (sortedAngles.length !== sectorIds.length) return [];
  const expectedStep = (2 * Math.PI) / sectorIds.length;
  const angleSteps = sortedAngles.map((angle, index) =>
    positiveAngleDifference(angle, sortedAngles[(index + 1) % sortedAngles.length]),
  );
  if (angleSteps.some((step) => Math.abs(step - expectedStep) > expectedStep * 0.35)) return [];

  const fallbackGap =
    ringRadii.length > 1
      ? median(ringRadii.slice(1).map((radius, index) => radius - ringRadii[index]))
      : ringRadii[0] * 0.75;
  const boundaries = [
    Math.max(0, ringRadii[0] - fallbackGap / 2),
    ...ringRadii.slice(0, -1).map((radius, index) => (radius + ringRadii[index + 1]) / 2),
    ringRadii[ringRadii.length - 1] + fallbackGap / 2,
  ];
  const halfSector = expectedStep / 2;
  const arcSteps = Math.max(4, Math.ceil(12 / sectorIds.length));

  return parsed.map((node) => {
    const innerRadius = boundaries[node.ring];
    const outerRadius = boundaries[node.ring + 1];
    const points: GamePoint[] = [];

    for (let step = 0; step <= arcSteps; step += 1) {
      const angle = node.angle - halfSector + (expectedStep * step) / arcSteps;
      points.push({ x: Math.cos(angle) * outerRadius, y: Math.sin(angle) * outerRadius });
    }
    for (let step = arcSteps; step >= 0; step -= 1) {
      const angle = node.angle - halfSector + (expectedStep * step) / arcSteps;
      points.push({ x: Math.cos(angle) * innerRadius, y: Math.sin(angle) * innerRadius });
    }

    return {
      key: node.id,
      center: { x: node.x, y: node.y },
      points,
    };
  });
}

function segmentsMatch(a: GameBoundarySegment, b: GameBoundarySegment, tolerance: number): boolean {
  const adx = a.x2 - a.x1;
  const ady = a.y2 - a.y1;
  const bdx = b.x2 - b.x1;
  const bdy = b.y2 - b.y1;
  const aLength = Math.hypot(adx, ady);
  const bLength = Math.hypot(bdx, bdy);
  if (aLength < 0.001 || bLength < 0.001) return false;

  const parallel = Math.abs((adx * bdx + ady * bdy) / (aLength * bLength));
  if (parallel < 0.965) return false;

  const distanceToALine = (x: number, y: number) =>
    Math.abs(ady * (x - a.x1) - adx * (y - a.y1)) / aLength;
  if (
    distanceToALine(b.x1, b.y1) > tolerance ||
    distanceToALine(b.x2, b.y2) > tolerance
  ) {
    return false;
  }

  const ux = adx / aLength;
  const uy = ady / aLength;
  const project = (x: number, y: number) => (x - a.x1) * ux + (y - a.y1) * uy;
  const bStart = Math.min(project(b.x1, b.y1), project(b.x2, b.y2));
  const bEnd = Math.max(project(b.x1, b.y1), project(b.x2, b.y2));
  const overlap = Math.max(0, Math.min(aLength, bEnd) - Math.max(0, bStart));
  return overlap >= Math.min(aLength, bLength) * 0.45;
}

export function findOuterBoundarySegments(
  cells: GamePolygonCell[],
  tolerance: number,
): GameBoundarySegment[] {
  const segments = cells.flatMap((cell) =>
    cell.points.map((point, index) => {
      const next = cell.points[(index + 1) % cell.points.length];
      return {
        key: cell.key,
        x1: point.x,
        y1: point.y,
        x2: next.x,
        y2: next.y,
      };
    }),
  );

  return segments.filter(
    (segment, index) =>
      !segments.some(
        (candidate, candidateIndex) =>
          candidateIndex !== index &&
          candidate.key !== segment.key &&
          segmentsMatch(segment, candidate, tolerance),
      ),
  );
}

export function terminalShapeForPolygon(points: GamePoint[], fallbackRadius: number): TerminalShape {
  if (points.length !== 4) {
    return { rx: fallbackRadius, ry: fallbackRadius, rotation: 0 };
  }

  const center = polygonCenter(points);
  let xx = 0;
  let yy = 0;
  let xy = 0;
  for (const point of points) {
    const dx = point.x - center.x;
    const dy = point.y - center.y;
    xx += dx * dx;
    yy += dy * dy;
    xy += dx * dy;
  }
  xx /= points.length;
  yy /= points.length;
  xy /= points.length;
  const trace = xx + yy;
  const determinant = xx * yy - xy * xy;
  const discriminant = Math.sqrt(Math.max(0, trace * trace - 4 * determinant));
  const majorVariance = (trace + discriminant) / 2;
  const minorVariance = (trace - discriminant) / 2;
  const ratio = minorVariance > 0 ? Math.sqrt(majorVariance / minorVariance) : 1;
  if (ratio < 1.22) return { rx: fallbackRadius, ry: fallbackRadius, rotation: 0 };

  const rotation = (Math.atan2(2 * xy, xx - yy) * 90) / Math.PI;
  return {
    rx: Math.min(fallbackRadius * 1.35, Math.sqrt(majorVariance) * 0.42),
    ry: Math.min(fallbackRadius, Math.sqrt(Math.max(minorVariance, 0)) * 0.52),
    rotation,
  };
}
