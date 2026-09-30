import { describe, expect, it } from "vitest";
import { blankBoard, cellIndex, cellPoint, editorGraph, nextLetter, pairStatus, readBoard, resizeBoard, setCell, writeBoard } from "./localBoardEdit";
import { parseLocalPuzzle } from "./localPuzzle";

describe("board editing", () => {
  it("reads incomplete pairs that the solver parser rejects", () => {
    const text = "# type: square\n# fill: true\nA.B\n...\nB..\n";
    expect(() => parseLocalPuzzle(text)).toThrow();
    const board = readBoard(text)!;
    expect(pairStatus(board)).toEqual([{ letter: "A", count: 1 }, { letter: "B", count: 2 }]);
  });

  it("round-trips through text accepted by the solver parser", () => {
    const board = setCell(readBoard("A.B\n...\nB..")!, cellIndex(readBoard("A.B\n...\nB..")!, { x: 2, y: -2 }), "A");
    const text = writeBoard(board);
    expect(text).toBe("# type: square\n# fill: true\nA.B\n...\nB.A\n");
    expect(parseLocalPuzzle(text).terminals).toEqual({ A: [0, 8], B: [2, 6] });
  });

  it("keeps the fill rule and rejects unsupported text", () => {
    expect(readBoard("# fill: false\nAA\n..")!.fill).toBe(false);
    expect(readBoard("# type: hex\nAA\n..")).toBeNull();
    expect(readBoard("A+\n.A")).toBeNull();
    expect(readBoard("AA\n...")).toBeNull();
    expect(readBoard("# warps: 0,0 1,1\nAA\n..")).toBeNull();
  });

  it("maps cells to game coordinates and back", () => {
    const board = blankBoard(3, 4);
    expect(cellPoint(board, 6)).toEqual({ x: 2, y: -1 });
    expect(cellIndex(board, { x: 2, y: -1 })).toBe(6);
  });

  it("resizes from the top-left corner", () => {
    const board = resizeBoard(readBoard("AB\nBA")!, 3, 3);
    expect(writeBoard(board)).toContain("AB.\nBA.\n...");
    expect(writeBoard(resizeBoard(board, 2, 2))).toContain("AB\nBA");
  });

  it("picks the next free letter, skipping reserved ones", () => {
    const board = readBoard("AC\n..")!;
    expect(nextLetter(board)).toBe("B");
    expect(nextLetter(board, ["B"])).toBe("D");
  });

  it("draws a single endpoint in its color", () => {
    const graph = editorGraph(readBoard("A.\n.B\nB.")!, { A: "#ff0000" });
    expect(graph.terminals.A).toEqual(["0,0", "0,0"]);
    expect(graph.terminals.B).toEqual(["1,1", "0,2"]);
    expect(graph.terminal_colors).toEqual({ A: "#ff0000" });
  });
});
