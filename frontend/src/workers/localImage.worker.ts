import { detectSquareTerminals, findRegularBoard, rectifyBoard, type Pixels, type Point } from "../processing/localImage";
import { throttleProgress } from "../processing/localProgress";

self.onmessage = (event: MessageEvent<{ pixels: Pixels; corners?: Point[]; rows?: number; cols?: number }>) => {
  try {
    const { pixels, corners, rows, cols } = event.data;
    const report = throttleProgress(update => self.postMessage({ kind: "progress", update }));
    if (!corners) { self.postMessage({ kind: "outline", board: findRegularBoard(pixels, report) }); return; }
    const prepared = rectifyBoard(pixels, corners, rows!, cols!, report);
    const detection = detectSquareTerminals(prepared, rows!, cols!, report);
    self.postMessage({ kind: "detected", detection, prepared }, { transfer: [prepared.data.buffer] });
  } catch (error) {
    self.postMessage({ kind: "error", message: error instanceof Error ? error.message : String(error) });
  }
};
