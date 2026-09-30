export type StaticRoute = "solve" | "create" | "library" | "help" | "not-found";

export function staticRoute(pathname: string): StaticRoute {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (["/", "/local", "/screenshot"].includes(path)) return "solve";
  if (path === "/create") return "create";
  if (path === "/library") return "library";
  if (["/docs", "/docs/architecture", "/docs/variants", "/docs/production-readiness"].includes(path)) return "help";
  return "not-found";
}

export const staticPaths = { solve: "/", create: "/create", library: "/library", help: "/docs" } as const;
