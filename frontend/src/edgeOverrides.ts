export type EdgePair = [string, string];

export type EdgeOverrides = {
  add: EdgePair[];
  remove: EdgePair[];
  warps: EdgePair[];
  walls: EdgePair[];
};

export type EdgeOverrideTexts = {
  addText: string;
  removeText: string;
  warpsText: string;
  wallsText: string;
};

export const EMPTY_EDGE_OVERRIDES: EdgeOverrides = {
  add: [],
  remove: [],
  warps: [],
  walls: []
};

function normalizePair(a: string, b: string): EdgePair {
  return a < b ? [a, b] : [b, a];
}

function parseLinePair(line: string): EdgePair {
  const cleaned = line.trim();
  if (!cleaned) {
    throw new Error("Empty edge pair.");
  }

  let parts: string[];
  if (cleaned.includes("|")) {
    parts = cleaned.split("|").map((part) => part.trim()).filter(Boolean);
  } else if (cleaned.includes("<->")) {
    parts = cleaned.split("<->").map((part) => part.trim()).filter(Boolean);
  } else if (cleaned.includes("->")) {
    parts = cleaned.split("->").map((part) => part.trim()).filter(Boolean);
  } else {
    parts = cleaned.split(/\s+/).map((part) => part.trim()).filter(Boolean);
  }
  if (parts.length !== 2) {
    throw new Error(
      `Invalid edge pair "${line}". Use one pair per line (e.g. "0,0 1,0" or "0,0|1,0").`
    );
  }

  const u = parts[0];
  const v = parts[1];
  if (!u || !v) {
    throw new Error(`Invalid edge pair "${line}".`);
  }
  if (u === v) {
    throw new Error(`Self-loop is not allowed: "${line}".`);
  }
  return normalizePair(u, v);
}

export function parseEdgePairsText(text: string): EdgePair[] {
  const out: EdgePair[] = [];
  const seen = new Set<string>();
  const lines = text.split(/\r?\n/);

  for (const line of lines) {
    const noComment = line.includes("#") ? line.slice(0, line.indexOf("#")) : line;
    const trimmed = noComment.trim();
    if (!trimmed) {
      continue;
    }
    const pair = parseLinePair(trimmed);
    const key = `${pair[0]}__${pair[1]}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push(pair);
    }
  }

  return out;
}

export function parseEdgeOverrideTexts(texts: EdgeOverrideTexts): EdgeOverrides {
  return {
    add: parseEdgePairsText(texts.addText),
    remove: parseEdgePairsText(texts.removeText),
    warps: parseEdgePairsText(texts.warpsText),
    walls: parseEdgePairsText(texts.wallsText)
  };
}

export function formatEdgePairsText(pairs: EdgePair[] | undefined): string {
  if (!pairs?.length) {
    return "";
  }
  return pairs.map(([u, v]) => `${u} ${v}`).join("\n");
}

export function isEdgeOverridesEmpty(overrides: Partial<EdgeOverrides> | null | undefined): boolean {
  if (!overrides) {
    return true;
  }
  return (
    (overrides.add?.length ?? 0) === 0 &&
    (overrides.remove?.length ?? 0) === 0 &&
    (overrides.warps?.length ?? 0) === 0 &&
    (overrides.walls?.length ?? 0) === 0
  );
}
