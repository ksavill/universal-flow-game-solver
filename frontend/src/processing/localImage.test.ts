import { describe, expect, it } from "vitest";
import { detectSquareTerminals, encodedImageSize, findRegularBoard, rectifyBoard, type Pixels } from "./localImage";
import type { ProgressUpdate } from "./localProgress";

function boardImage(): Pixels {
  const width = 252, height = 320;
  const data = new Uint8ClampedArray(width * height * 4);
  for (let index = 0; index < data.length; index += 4) { data[index] = data[index + 1] = data[index + 2] = 5; data[index + 3] = 255; }
  const set = (x: number, y: number, rgb: number[]) => rgb.forEach((value, c) => data[(y * width + x) * 4 + c] = value);
  for (let i = 0; i <= 5; i++) {
    for (let y = 40; y <= 290; y++) set(1 + i * 50, y, [60, 100, 60]);
    for (let x = 1; x <= 251; x++) set(x, 40 + i * 50, [60, 100, 60]);
  }
  const colors = [[255, 0, 0], [0, 220, 0], [0, 0, 255], [255, 255, 0], [255, 130, 0]];
  colors.forEach((rgb, row) => [0, 4].forEach(col => {
    const cx = 26 + col * 50, cy = 65 + row * 50;
    for (let y = cy - 17; y <= cy + 17; y++) for (let x = cx - 17; x <= cx + 17; x++) if ((x - cx) ** 2 + (y - cy) ** 2 < 17 ** 2) set(x, y, rgb);
  }));
  return { width, height, data };
}

describe("local image detector", () => {
  it("reads compressed dimensions before decoding and rejects broken headers", () => {
    const header = new Uint8Array(24);
    header.set([137, 80, 78, 71, 13, 10, 26, 10]);
    const view = new DataView(header.buffer); view.setUint32(16, 1242); view.setUint32(20, 2688);
    expect(encodedImageSize(header)).toEqual({ width: 1242, height: 2688 });
    const jpeg = new Uint8Array([255, 216, 255, 192, 0, 8, 8, 0, 100, 0, 200, 1]);
    expect(encodedImageSize(jpeg)).toEqual({ width: 200, height: 100 });
    expect(() => encodedImageSize(new Uint8Array([255, 216, 255, 192, 0]))).toThrow(/header/);
  });
  it("finds edge-adjacent grid lines and all five endpoint pairs", () => {
    const source = boardImage();
    const events: ProgressUpdate[] = [];
    const report = (update: ProgressUpdate) => events.push(update);
    const outline = findRegularBoard(source, report);
    expect(outline?.rows).toBe(5); expect(outline?.cols).toBe(5);
    const corrected = rectifyBoard(source, outline!.corners, 5, 5, report);
    const detected = detectSquareTerminals(corrected, 5, 5, report);
    expect(detected.text).toContain("A...A\nB...B\nC...C\nD...D\nE...E");
    expect(detected.endpoints).toBe(10);
    for (const stage of ["outline", "rectifying", "detecting"]) {
      const updates = events.filter(event => event.stage === stage);
      expect(updates[updates.length - 1].completed).toBe(updates[updates.length - 1].total);
      expect(updates.map(event => event.completed)).toEqual(updates.map(event => event.completed).sort((a, b) => a! - b!));
    }
    expect(events[events.length - 1].metrics).toMatchObject({ endpoints: 10, pairs: 5, cellsExamined: 25 });
  });
  it("does not claim an unrelated blank image is a board", () => {
    const image = { width: 100, height: 100, data: new Uint8ClampedArray(100 * 100 * 4) };
    expect(findRegularBoard(image)).toBeNull();
    expect(detectSquareTerminals(image, 5, 5).endpoints).toBe(0);
  });
  it("rejects crossed corners and excessive grid sizes", () => {
    const source = boardImage();
    expect(() => rectifyBoard(source, [{ x: 1, y: 1 }, { x: 200, y: 200 }, { x: 200, y: 1 }, { x: 1, y: 200 }], 5, 5)).toThrow(/convex/);
    expect(() => rectifyBoard(source, [], 21, 5)).toThrow(/between/);
  });
});
