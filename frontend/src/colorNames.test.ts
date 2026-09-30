import { describe, expect, it } from "vitest";
import { GAME_PALETTE } from "./colors";
import { colorName } from "./colorNames";

describe("colorName", () => {
  it("names the common game colors", () => {
    expect(colorName("#ff0000")).toBe("red");
    expect(colorName("#008d00")).toBe("green");
    expect(colorName("#0a29ff")).toBe("blue");
    expect(colorName("#eeee00")).toBe("yellow");
    expect(colorName("#ff8800")).toBe("orange");
    expect(colorName("#00ffff")).toBe("cyan");
    expect(colorName("#ff00ff")).toBe("magenta");
    expect(colorName("#800080")).toBe("purple");
    expect(colorName("#5c1010")).toBe("maroon");
    expect(colorName("#795548")).toBe("brown");
    expect(colorName("#ffffff")).toBe("white");
    expect(colorName("#808080")).toBe("gray");
  });

  it("returns a name for every palette entry and tolerates bad input", () => {
    for (const hex of GAME_PALETTE) expect(colorName(hex)).toMatch(/^[a-z ]+$/);
    expect(colorName("nonsense")).toBe("colored");
  });
});
