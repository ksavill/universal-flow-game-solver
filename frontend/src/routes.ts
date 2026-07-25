export type PrimaryView = "import" | "new" | "library" | "flagged";
export type LibrarySection = "puzzles" | "screenshots";
export type DocPageId = "architecture" | "variants" | "production-readiness";

export type AppRoute =
  | { kind: "primary"; view: Exclude<PrimaryView, "library"> }
  | { kind: "library"; section: LibrarySection }
  | { kind: "docs"; pageId: DocPageId }
  | { kind: "puzzle"; source: "examples" | "user"; relPath: string }
  | { kind: "import"; importId: string }
  | { kind: "draft" }
  | { kind: "not-found"; requestedPath: string };

const DOC_PAGE_IDS = new Set(["architecture", "variants", "production-readiness"]);

function decodePathPart(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function normalizedSegments(pathname: string): string[] {
  return pathname
    .split("/")
    .filter(Boolean);
}

export function parseAppRoute(pathname: string): AppRoute {
  const segments = normalizedSegments(pathname);
  if (segments.length === 0) return { kind: "primary", view: "import" };
  if (segments.length === 1) {
    if (segments[0] === "screenshot") return { kind: "primary", view: "import" };
    if (segments[0] === "create") return { kind: "primary", view: "new" };
    if (segments[0] === "library") return { kind: "library", section: "puzzles" };
    if (segments[0] === "flagged") return { kind: "primary", view: "flagged" };
    if (segments[0] === "docs") return { kind: "docs", pageId: "architecture" };
  }
  if (
    segments.length === 2 &&
    segments[0] === "library" &&
    segments[1] === "screenshots"
  ) {
    return { kind: "library", section: "screenshots" };
  }
  if (segments.length === 2 && segments[0] === "docs" && DOC_PAGE_IDS.has(segments[1])) {
    return { kind: "docs", pageId: segments[1] as DocPageId };
  }
  if (segments.length === 2 && segments[0] === "imports") {
    const importId = decodePathPart(segments[1]);
    if (importId) return { kind: "import", importId };
  }
  if (segments.length === 2 && segments[0] === "solve" && segments[1] === "draft") {
    return { kind: "draft" };
  }
  if (
    segments.length >= 3 &&
    segments[0] === "puzzles" &&
    (segments[1] === "examples" || segments[1] === "user")
  ) {
    const decoded = segments.slice(2).map(decodePathPart);
    if (decoded.every((part): part is string => part !== null && part.length > 0)) {
      return {
        kind: "puzzle",
        source: segments[1],
        relPath: decoded.join("/")
      };
    }
  }
  return { kind: "not-found", requestedPath: pathname };
}

function encodeRelativePath(relPath: string): string {
  return relPath
    .split(/[\\/]/)
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
}

export function appRoutePath(route: AppRoute): string {
  switch (route.kind) {
    case "primary":
      if (route.view === "import") return "/screenshot";
      if (route.view === "new") return "/create";
      return "/flagged";
    case "library":
      return route.section === "screenshots" ? "/library/screenshots" : "/library";
    case "docs":
      return `/docs/${encodeURIComponent(route.pageId)}`;
    case "puzzle":
      return `/puzzles/${route.source}/${encodeRelativePath(route.relPath)}`;
    case "import":
      return `/imports/${encodeURIComponent(route.importId)}`;
    case "draft":
      return "/solve/draft";
    case "not-found":
      return route.requestedPath;
  }
}

export function primaryRoute(view: PrimaryView): AppRoute {
  return view === "library"
    ? { kind: "library", section: "puzzles" }
    : { kind: "primary", view };
}

export function routePrimaryView(route: AppRoute): PrimaryView | null {
  if (route.kind === "primary") return route.view;
  if (route.kind === "library" || route.kind === "puzzle" || route.kind === "import") {
    return "library";
  }
  return null;
}
