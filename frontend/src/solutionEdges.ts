import { SolveResponse } from "./api";

export type SolutionPaths = Record<string, string[]>;
export type SolutionPathEdges = Record<string, Array<[string, string]>>;
export type BlockedAdjacency = { id: string; u: string; v: string };

export function canonicalEdgeKey(u: string, v: string): string {
  return u < v ? `${u}\u0000${v}` : `${v}\u0000${u}`;
}

export function buildSolutionEdgeColors(
  pathEdges?: SolutionPathEdges | null,
  paths?: SolutionPaths | null
): Map<string, string> {
  const out = new Map<string, string>();
  if (pathEdges && Object.keys(pathEdges).length > 0) {
    Object.entries(pathEdges).forEach(([color, edges]) => {
      edges.forEach(([u, v]) => out.set(canonicalEdgeKey(u, v), color));
    });
    return out;
  }

  // Compatibility with older API responses: ordered paths are still exact,
  // unlike inferring selected edges from equal endpoint colors.
  Object.entries(paths ?? {}).forEach(([color, nodes]) => {
    for (let index = 1; index < nodes.length; index += 1) {
      out.set(canonicalEdgeKey(nodes[index - 1], nodes[index]), color);
    }
  });
  return out;
}

export function buildAdjacencyKinds(
  graph: SolveResponse["graph"]
): Map<string, "local" | "seam" | "warp" | "custom"> {
  const out = new Map<string, "local" | "seam" | "warp" | "custom">();
  (graph.adjacencies ?? []).forEach((adjacency) => {
    if (adjacency.state !== "open") {
      return;
    }
    out.set(
      canonicalEdgeKey(adjacency.a.channel, adjacency.b.channel),
      adjacency.kind
    );
  });
  return out;
}

export function buildBlockedAdjacencies(
  graph: SolveResponse["graph"]
): BlockedAdjacency[] {
  return (graph.adjacencies ?? [])
    .filter((adjacency) => adjacency.state === "blocked")
    .map((adjacency) => ({
      id: adjacency.id,
      u: adjacency.a.channel,
      v: adjacency.b.channel
    }));
}
