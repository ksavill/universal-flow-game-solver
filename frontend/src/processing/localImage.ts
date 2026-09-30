import type { ProgressReporter } from "./localProgress";
export type Point = { x: number; y: number };
export type Pixels = { width: number; height: number; data: Uint8ClampedArray };
export type DetectedBoard = { corners: Point[]; rows: number; cols: number };
export type LocalDetection = { text: string; colors: Record<string, string>; warnings: string[]; endpoints: number };

export function encodedImageSize(header: Uint8Array): { width: number; height: number } {
  const view = new DataView(header.buffer, header.byteOffset, header.byteLength);
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => header[index] === value);
  if (png && header.length >= 24) return { width: view.getUint32(16), height: view.getUint32(20) };
  if (header[0] === 0xff && header[1] === 0xd8) {
    let offset = 2;
    while (offset + 4 < header.length) {
      if (header[offset++] !== 0xff) break;
      while (header[offset] === 0xff) offset++;
      const marker = header[offset++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (offset + 2 > header.length) break;
      const length = view.getUint16(offset);
      if (length < 2 || offset + length > header.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 7) {
        return { width: view.getUint16(offset + 5), height: view.getUint16(offset + 3) };
      }
      offset += length;
    }
  }
  throw new Error("Choose a valid JPEG or PNG with a readable image header. Export HEIC photos as JPEG first.");
}

const lum = (pixels: Pixels, x: number, y: number) => {
  const index = (Math.max(0, Math.min(pixels.height - 1, y)) * pixels.width + Math.max(0, Math.min(pixels.width - 1, x))) * 4;
  return pixels.data[index] * .299 + pixels.data[index + 1] * .587 + pixels.data[index + 2] * .114;
};

function gridLines(pixels: Pixels, horizontal: boolean, low = 0, high?: number, report?: ProgressReporter): number[] {
  const length = horizontal ? pixels.height : pixels.width;
  const across = horizontal ? pixels.width : pixels.height;
  const stop = high ?? across;
  const scores: number[] = [];
  for (let position = 0; position < length; position++) {
    let hits = 0;
    let samples = 0;
    for (let offset = low; offset < stop; offset += 2) {
      const x = horizontal ? offset : position;
      const y = horizontal ? position : offset;
      const index = (y * pixels.width + x) * 4;
      const rgb = Array.from(pixels.data.subarray(index, index + 3));
      const value = lum(pixels, x, y);
      const surrounding = horizontal ? (lum(pixels, x, y - 3) + lum(pixels, x, y + 3)) / 2 : (lum(pixels, x - 3, y) + lum(pixels, x + 3, y)) / 2;
      if (value > 18 && Math.max(...rgb) < 190 && Math.max(...rgb) - Math.min(...rgb) < 100 && value - surrounding > 5) hits++;
      samples++;
    }
    scores[position] = hits / Math.max(1, samples);
    if (position % 32 === 0 || position === length - 1) report?.({ stage: "outline", completed: (horizontal ? 0 : pixels.height) + position + 1, total: pixels.height + pixels.width, unit: "lines" });
  }
  const lines: number[] = [];
  let cluster: number[] = [];
  for (let position = 0; position < length; position++) {
    if (scores[position] > .42) cluster.push(position);
    else if (cluster.length) {
      lines.push(cluster.reduce((a, b) => a + b, 0) / cluster.length); cluster = [];
    }
  }
  if (cluster.length) lines.push(cluster.reduce((a, b) => a + b, 0) / cluster.length);
  return lines;
}

function regularSequence(lines: number[]): number[] {
  let best: number[] = [];
  for (let a = 0; a < lines.length; a++) {
    for (let b = a + 1; b < lines.length; b++) {
      const pitch = lines[b] - lines[a];
      if (pitch < 12) continue;
      const sequence = [lines[a], lines[b]];
      for (let expected = lines[b] + pitch; expected <= lines[lines.length - 1] + pitch * .06; expected += pitch) {
        const nearest = lines.find(line => line > sequence[sequence.length - 1] && Math.abs(line - expected) <= Math.max(2, pitch * .06));
        if (nearest === undefined) break;
        sequence.push(nearest);
      }
      if (sequence.length >= 3 && sequence.length <= 21 && sequence.length > best.length) best = sequence;
    }
  }
  return best;
}

// Conservative automatic crop for straight screenshots. Photos use user corners.
export function findRegularBoard(pixels: Pixels, report?: ProgressReporter): DetectedBoard | null {
  report?.({ stage: "outline", completed: 0, total: pixels.width + pixels.height, unit: "lines", metrics: { imageWidth: pixels.width, imageHeight: pixels.height } });
  const ys = regularSequence(gridLines(pixels, true, 0, undefined, report));
  if (!ys.length) return null;
  const xs = regularSequence(gridLines(pixels, false, Math.ceil(ys[0]), Math.floor(ys[ys.length - 1]), report));
  if (!xs.length) return null;
  const x0 = xs[0], x1 = xs[xs.length - 1], y0 = ys[0], y1 = ys[ys.length - 1];
  const pitchX = (x1 - x0) / (xs.length - 1), pitchY = (y1 - y0) / (ys.length - 1);
  if (Math.abs(pitchX / pitchY - 1) > .15) return null;
  return { rows: ys.length - 1, cols: xs.length - 1, corners: [{ x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 }] };
}

function homography(corners: Point[]): number[] {
  if (corners.length !== 4 || corners.some(p => !Number.isFinite(p.x) || !Number.isFinite(p.y))) throw new Error("Select four board corners: top left, top right, bottom right, bottom left.");
  const crosses = corners.map((p, i) => {
    const q = corners[(i + 1) % 4], r = corners[(i + 2) % 4];
    return (q.x - p.x) * (r.y - q.y) - (q.y - p.y) * (r.x - q.x);
  });
  if (crosses.some(value => value <= 4)) throw new Error("Corners must form a convex board outline in clockwise order.");
  const uv = [[0, 0], [1, 0], [1, 1], [0, 1]];
  const matrix = uv.flatMap(([u, v], i) => {
    const { x, y } = corners[i];
    return [[u, v, 1, 0, 0, 0, -x * u, -x * v, x], [0, 0, 0, u, v, 1, -y * u, -y * v, y]];
  });
  for (let col = 0; col < 8; col++) {
    let pivot = col;
    for (let row = col + 1; row < 8; row++) if (Math.abs(matrix[row][col]) > Math.abs(matrix[pivot][col])) pivot = row;
    [matrix[col], matrix[pivot]] = [matrix[pivot], matrix[col]];
    if (Math.abs(matrix[col][col]) < 1e-8) throw new Error("Board corners are too close together.");
    const scale = matrix[col][col];
    matrix[col] = matrix[col].map(value => value / scale);
    for (let row = 0; row < 8; row++) {
      if (row === col) continue;
      const factor = matrix[row][col];
      matrix[row] = matrix[row].map((value, i) => value - factor * matrix[col][i]);
    }
  }
  return matrix.map(row => row[8]);
}

export function rectifyBoard(source: Pixels, corners: Point[], rows: number, cols: number, report?: ProgressReporter): Pixels {
  if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 2 || cols < 2 || rows > 20 || cols > 20) throw new Error("Rows and columns must be integers between 2 and 20.");
  if (corners.some(p => p.x < 0 || p.y < 0 || p.x >= source.width || p.y >= source.height)) throw new Error("Keep all four corners inside the image.");
  const h = homography(corners);
  const width = cols * 48, height = rows * 48;
  const data = new Uint8ClampedArray(width * height * 4);
  report?.({ stage: "rectifying", completed: 0, total: height, unit: "rows", metrics: { rows, cols } });
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const u = (x + .5) / width, v = (y + .5) / height;
    const denominator = h[6] * u + h[7] * v + 1;
    const sx = Math.max(0, Math.min(source.width - 1, (h[0] * u + h[1] * v + h[2]) / denominator));
    const sy = Math.max(0, Math.min(source.height - 1, (h[3] * u + h[4] * v + h[5]) / denominator));
    const x0 = Math.floor(sx), y0 = Math.floor(sy), x1 = Math.min(source.width - 1, x0 + 1), y1 = Math.min(source.height - 1, y0 + 1);
    const fx = sx - x0, fy = sy - y0, target = (y * width + x) * 4;
    for (let c = 0; c < 3; c++) {
      const top = source.data[(y0 * source.width + x0) * 4 + c] * (1 - fx) + source.data[(y0 * source.width + x1) * 4 + c] * fx;
      const bottom = source.data[(y1 * source.width + x0) * 4 + c] * (1 - fx) + source.data[(y1 * source.width + x1) * 4 + c] * fx;
      data[target + c] = top * (1 - fy) + bottom * fy;
    }
    data[target + 3] = 255;
    if (x === width - 1 && (y % 16 === 0 || y === height - 1)) report?.({ stage: "rectifying", completed: y + 1, total: height, unit: "rows" });
  }
  return { width, height, data };
}

export function detectSquareTerminals(board: Pixels, rows: number, cols: number, report?: ProgressReporter): LocalDetection {
  const cells = Array(rows * cols).fill(".") as string[];
  const groups: { color: number[]; cells: number[] }[] = [];
  report?.({ stage: "detecting", completed: 0, total: rows * cols, unit: "cells", metrics: { rows, cols } });
  for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {
    report?.({ stage: "detecting", completed: row * cols + col, total: rows * cols, unit: "cells", metrics: { cellsExamined: row * cols + col, endpoints: groups.reduce((sum, group) => sum + group.cells.length, 0), pairs: groups.filter(group => group.cells.length === 2).length } });
    const rgb = [0, 0, 0];
    let hits = 0, total = 0;
    for (let y = Math.floor((row + .35) * board.height / rows); y < (row + .65) * board.height / rows; y++) {
      for (let x = Math.floor((col + .35) * board.width / cols); x < (col + .65) * board.width / cols; x++) {
        const index = (y * board.width + x) * 4;
        const sample = Array.from(board.data.subarray(index, index + 3));
        const max = Math.max(...sample), min = Math.min(...sample);
        if (max > 110 && (max - min > 55 || min > 100)) { sample.forEach((value, c) => rgb[c] += value); hits++; }
        total++;
      }
    }
    if (hits / total < .65) continue;
    const mean = rgb.map(value => value / hits);
    const normalized = mean.map(value => value / Math.max(...mean));
    let group = groups.find(g => {
      const other = g.color.map(value => value / Math.max(...g.color));
      return Math.sqrt(normalized.reduce((sum, value, c) => sum + (value - other[c]) ** 2, 0)) < .23;
    });
    if (!group) { group = { color: mean, cells: [] }; groups.push(group); }
    group.cells.push(row * cols + col);
  }
  if (groups.length > 26) throw new Error("Too many candidate colors. Adjust the corners or use an unplayed board.");
  const warnings = ["Review all endpoints before solving. This prototype cannot verify whether an entire color pair was missed."];
  const colors: Record<string, string> = {};
  groups.forEach((group, i) => {
    const letter = String.fromCharCode(65 + i);
    group.cells.forEach(index => cells[index] = letter);
    colors[letter] = "#" + group.color.map(value => Math.round(value).toString(16).padStart(2, "0")).join("");
    if (group.cells.length !== 2) warnings.push(`${letter}: found ${group.cells.length} endpoints; fix this pair on the board.`);
  });
  if (!groups.length) warnings.push("No endpoints found. Adjust the board outline or enter the puzzle manually.");
  report?.({ stage: "detecting", completed: rows * cols, total: rows * cols, unit: "cells", metrics: { cellsExamined: rows * cols, endpoints: groups.reduce((sum, group) => sum + group.cells.length, 0), pairs: groups.filter(group => group.cells.length === 2).length } });
  return { text: "# type: square\n# fill: true\n" + Array.from({ length: rows }, (_, row) => cells.slice(row * cols, (row + 1) * cols).join("")).join("\n") + "\n", colors, warnings, endpoints: groups.reduce((sum, group) => sum + group.cells.length, 0) };
}
