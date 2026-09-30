import { describe, expect, it } from "vitest";
import { staticRoute } from "./staticRoutes";

describe("device-only navigation", () => {
  it("opens local solving for root and old entry links", () => {
    for (const path of ["/", "/local", "/local/", "/screenshot"]) expect(staticRoute(path)).toBe("solve");
  });
  it("supports direct links to local creation, library and help", () => {
    expect(staticRoute("/create")).toBe("create");
    expect(staticRoute("/library/")).toBe("library");
    expect(staticRoute("/docs/architecture")).toBe("help");
  });
  it("does not route old server records or APIs to a server view", () => {
    for (const path of ["/flagged", "/imports/123", "/puzzles/user/test.flow", "/library/screenshots", "/api/solve", "/solve/draft", "/unknown"]) expect(staticRoute(path)).toBe("not-found");
  });
});
