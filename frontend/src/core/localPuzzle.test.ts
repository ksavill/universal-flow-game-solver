import { describe, expect, it } from "vitest";
import { parseLocalPuzzle, validateLocalSolution } from "./localPuzzle";

describe("local square puzzle contract", () => {
  it("preserves holes, fill rules, and endpoints", () => {
    const puzzle = parseLocalPuzzle("# type: square\n# fill: false\nA#\n..\nA#\n");
    expect(puzzle.terminals.A).toEqual([0, 4]);
    expect(puzzle.fill).toBe(false);
    validateLocalSolution(puzzle, { A: [0, 2, 4] });
  });
  it.each(["# type: hex\nAA\n..", "# type: square\nA+\n.A", "# warps: 0,0 1,1\nAA\n..", "AA\nA.", "AA\n..."])("rejects unsupported or malformed input: %s", text => {
    expect(() => parseLocalPuzzle(text)).toThrow();
  });
  it("independently rejects overlaps and uncovered cells", () => {
    const puzzle = parseLocalPuzzle("AA\nBB");
    expect(() => validateLocalSolution(puzzle, { A: [0, 2, 3, 1], B: [2, 3] })).toThrow();
    expect(() => validateLocalSolution(parseLocalPuzzle("AA\n.."), { A: [0, 1] })).toThrow(/fill/);
    validateLocalSolution(parseLocalPuzzle("# fill: false\nAA\n.."), { A: [0, 1] });
  });
});
