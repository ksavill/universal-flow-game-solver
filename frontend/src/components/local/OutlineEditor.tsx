import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import { Box } from "@mui/material";
import type { Point } from "../../processing/localImage";

type OutlineEditorProps = {
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  corners: Point[];
  rows: number;
  cols: number;
  disabled?: boolean;
  maxHeight?: number | string;
  onChange: (corners: Point[]) => void;
};

const CORNER_NAMES = ["top left", "top right", "bottom right", "bottom left"];
const ZOOM = 3;

const lerp = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });

// A centered square covering most of the shorter side; a starting point to drag from.
export function defaultCorners(width: number, height: number): Point[] {
  const side = Math.min(width, height) * 0.7;
  const x0 = (width - side) / 2;
  const y0 = (height - side) / 2;
  return [
    { x: x0, y: y0 },
    { x: x0 + side, y: y0 },
    { x: x0 + side, y: y0 + side },
    { x: x0, y: y0 + side }
  ];
}

export function OutlineEditor({
  imageUrl,
  imageWidth,
  imageHeight,
  corners,
  rows,
  cols,
  disabled,
  maxHeight = 560,
  onChange
}: OutlineEditorProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const [draft, setDraft] = useState<Point[] | null>(null);
  // Image pixels per screen pixel, so handles keep a constant on-screen size.
  const [unit, setUnit] = useState(1);
  const clipId = `loupe${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const update = () => setUnit(imageWidth / Math.max(1, svg.getBoundingClientRect().width));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(svg);
    return () => observer.disconnect();
  }, [imageWidth]);

  const points = draft ?? corners;
  const toImage = (event: PointerEvent<SVGElement>): Point | null => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return {
      x: Math.min(imageWidth - 1, Math.max(0, point.x)),
      y: Math.min(imageHeight - 1, Math.max(0, point.y))
    };
  };

  // The drag lives in a ref: pointer events can arrive before React re-renders
  // (seen in WebKit), and a stale closure would drop the whole drag.
  const dragRef = useRef<{ index: number; points: Point[] } | null>(null);
  const startDrag = (index: number) => (event: PointerEvent<SVGElement>) => {
    if (disabled) return;
    event.preventDefault();
    svgRef.current?.setPointerCapture(event.pointerId);
    dragRef.current = { index, points: corners };
    setActive(index);
    setDraft(corners);
  };
  const moveDrag = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const point = toImage(event);
    if (!point) return;
    drag.points = drag.points.map((corner, index) => (index === drag.index ? point : corner));
    setDraft(drag.points);
  };
  const endDrag = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    // A cancelled pointer has no meaningful position; keep the last move.
    const point = event.type === "pointerup" ? toImage(event) : null;
    if (point) drag.points = drag.points.map((corner, index) => (index === drag.index ? point : corner));
    dragRef.current = null;
    if (svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId);
    onChange(drag.points);
    setActive(null);
    setDraft(null);
  };

  const handleRadius = 13 * unit;
  const hitRadius = 26 * unit;
  const stroke = 2.5 * unit;
  const [tl, tr, br, bl] = points;
  const gridLines =
    points.length === 4
      ? [
          ...Array.from({ length: Math.max(0, cols - 1) }, (_, i) => [
            lerp(tl, tr, (i + 1) / cols),
            lerp(bl, br, (i + 1) / cols)
          ]),
          ...Array.from({ length: Math.max(0, rows - 1) }, (_, i) => [
            lerp(tl, bl, (i + 1) / rows),
            lerp(tr, br, (i + 1) / rows)
          ])
        ]
      : [];

  const dragged = active !== null ? points[active] : null;
  const loupeRadius = 64 * unit;
  const loupeCenter = dragged
    ? {
        // Keep the magnifier away from the finger: opposite horizontal half.
        x: dragged.x > imageWidth / 2 ? loupeRadius + 12 * unit : imageWidth - loupeRadius - 12 * unit,
        y: loupeRadius + 12 * unit
      }
    : null;
  const aspect = imageWidth / imageHeight;
  const maxHeightCss = typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight;

  return (
    <Box sx={{ display: "flex", justifyContent: "center", width: "100%", p: 1.5, boxSizing: "border-box" }}>
      <svg
        ref={svgRef}
        role="group"
        aria-label="Board outline. Drag the four corner handles onto the board corners."
        viewBox={`0 0 ${imageWidth} ${imageHeight}`}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        style={{
          display: "block",
          width: `min(100%, calc(${maxHeightCss} * ${aspect}))`,
          aspectRatio: `${imageWidth} / ${imageHeight}`,
          borderRadius: 10,
          background: "#050507",
          touchAction: active !== null ? "none" : "pan-y",
          userSelect: "none",
          // Handles sit on the board corners, which are often at the image edge.
          overflow: "visible"
        }}
      >
        <image href={imageUrl} width={imageWidth} height={imageHeight} />
        {points.length === 4 && (
          <>
            <path
              d={`M0 0H${imageWidth}V${imageHeight}H0Z M${points.map((p) => `${p.x} ${p.y}`).join(" L")}Z`}
              fill="rgba(0,0,0,0.45)"
              fillRule="evenodd"
            />
            {gridLines.map(([a, b], index) => (
              <line
                key={index}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="rgba(255,223,50,0.45)"
                strokeWidth={stroke * 0.6}
              />
            ))}
            <polygon
              points={points.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="none"
              stroke="#ffdf32"
              strokeWidth={stroke}
              strokeLinejoin="round"
            />
          </>
        )}
        {points.map((point, index) => (
          <g
            key={index}
            role="slider"
            aria-label={`${CORNER_NAMES[index]} corner`}
            aria-valuetext={`${Math.round(point.x)}, ${Math.round(point.y)}`}
            tabIndex={disabled ? -1 : 0}
            onPointerDown={startDrag(index)}
            onKeyDown={(event) => {
              const step = (event.shiftKey ? 10 : 2) * unit;
              const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
              const delta = moves[event.key];
              if (!delta || disabled) return;
              event.preventDefault();
              onChange(corners.map((corner, i) => i !== index ? corner : {
                x: Math.min(imageWidth - 1, Math.max(0, corner.x + delta[0])),
                y: Math.min(imageHeight - 1, Math.max(0, corner.y + delta[1]))
              }));
            }}
            style={{ cursor: disabled ? "default" : active === index ? "grabbing" : "grab" }}
          >
            <circle cx={point.x} cy={point.y} r={hitRadius} fill="transparent" />
            <circle
              cx={point.x}
              cy={point.y}
              r={active === index ? handleRadius * 0.45 : handleRadius}
              fill={active === index ? "none" : "rgba(255,223,50,0.25)"}
              stroke="#ffdf32"
              strokeWidth={stroke}
            />
            <circle cx={point.x} cy={point.y} r={stroke} fill="#ffdf32" />
          </g>
        ))}
        {dragged && loupeCenter && (
          <g pointerEvents="none">
            <defs>
              <clipPath id={clipId}>
                <circle cx={loupeCenter.x} cy={loupeCenter.y} r={loupeRadius} />
              </clipPath>
            </defs>
            <g clipPath={`url(#${clipId})`}>
              <rect
                x={loupeCenter.x - loupeRadius}
                y={loupeCenter.y - loupeRadius}
                width={loupeRadius * 2}
                height={loupeRadius * 2}
                fill="#000"
              />
              <g transform={`translate(${loupeCenter.x - dragged.x * ZOOM} ${loupeCenter.y - dragged.y * ZOOM}) scale(${ZOOM})`}>
                <image href={imageUrl} width={imageWidth} height={imageHeight} />
                <polygon
                  points={points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke="#ffdf32"
                  strokeWidth={stroke / ZOOM}
                />
              </g>
            </g>
            <circle
              cx={loupeCenter.x}
              cy={loupeCenter.y}
              r={loupeRadius}
              fill="none"
              stroke="#fff"
              strokeWidth={stroke}
            />
            <path
              d={`M${loupeCenter.x - 10 * unit} ${loupeCenter.y}H${loupeCenter.x + 10 * unit}M${loupeCenter.x} ${loupeCenter.y - 10 * unit}V${loupeCenter.y + 10 * unit}`}
              stroke="#fff"
              strokeWidth={stroke * 0.6}
            />
          </g>
        )}
      </svg>
    </Box>
  );
}
