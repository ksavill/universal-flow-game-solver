from __future__ import annotations

import io
from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageStat


@dataclass(frozen=True)
class CropBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class GridDetection:
    rows: int
    cols: int
    vertical_lines: int
    horizontal_lines: int
    width: int
    height: int


@dataclass(frozen=True)
class CircleGridDetection:
    rings: int
    sectors: int
    center_x: float
    center_y: float
    outer_radius: float
    inner_radius: float
    ring_boundaries: Tuple[float, ...]
    spoke_angles: Tuple[float, ...]


@dataclass(frozen=True)
class TerminalCandidate:
    row: int
    col: int
    color: Tuple[float, float, float]
    saturation: float
    brightness: float


@dataclass(frozen=True)
class TerminalPlacement:
    row: int
    col: int
    letter: str
    color: Tuple[float, float, float]


@dataclass(frozen=True)
class TerminalNodePlacement:
    node_id: str
    letter: str
    color: Tuple[float, float, float]


@dataclass(frozen=True)
class LevelTypeCandidate:
    geometry: str
    modifiers: Tuple[str, ...]
    confidence: float
    reason: str = ""


@dataclass(frozen=True)
class LevelTypeDetection:
    geometry: str
    modifiers: Tuple[str, ...]
    confidence: float
    candidates: List[LevelTypeCandidate]
    signals: Dict[str, Any]
    warnings: List[str]


def _try_import_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        return cv2, np
    except Exception:
        return None, None


def _order_points(pts):
    # pts shape: (4, 2)
    import numpy as np  # type: ignore

    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[s.argmin()]
    rect[2] = pts[s.argmax()]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[diff.argmin()]
    rect[3] = pts[diff.argmax()]
    return rect


def auto_perspective(
    image: Image.Image,
    *,
    canny_low: int = 50,
    canny_high: int = 150,
    min_area_ratio: float = 0.1,
) -> Tuple[Image.Image, Optional[Dict[str, Any]]]:
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return image, None

    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, canny_low, canny_high)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image, None

    h, w = gray.shape[:2]
    min_area = float(w * h) * min_area_ratio
    best = None
    best_area = 0.0
    for cnt in contours:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        area = cv2.contourArea(approx)
        if area > best_area and area >= min_area and len(approx) == 4:
            best = approx
            best_area = area

    if best is None:
        return image, None

    pts = best.reshape(4, 2).astype("float32")
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect

    width_a = ((br[0] - bl[0]) ** 2 + (br[1] - bl[1]) ** 2) ** 0.5
    width_b = ((tr[0] - tl[0]) ** 2 + (tr[1] - tl[1]) ** 2) ** 0.5
    height_a = ((tr[0] - br[0]) ** 2 + (tr[1] - br[1]) ** 2) ** 0.5
    height_b = ((tl[0] - bl[0]) ** 2 + (tl[1] - bl[1]) ** 2) ** 0.5

    max_w = int(max(width_a, width_b))
    max_h = int(max(height_a, height_b))
    if max_w <= 0 or max_h <= 0:
        return image, None

    dst = np.array(
        [
            [0, 0],
            [max_w - 1, 0],
            [max_w - 1, max_h - 1],
            [0, max_h - 1],
        ],
        dtype="float32",
    )
    m = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img, m, (max_w, max_h))
    warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
    out = Image.fromarray(warped_rgb)

    info = {
        "corners": [{"x": float(p[0]), "y": float(p[1])} for p in rect],
        "width": max_w,
        "height": max_h,
        "area": best_area,
    }
    return out, info


def load_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    return image.convert("RGB")


def _ink_mask(gray: Image.Image, *, threshold: int, invert: bool) -> Image.Image:
    if invert:
        return gray.point(lambda p: 255 if p > threshold else 0)
    return gray.point(lambda p: 255 if p < threshold else 0)


def _finalize_crop_box(
    *,
    x0: int,
    y0: int,
    width: int,
    height: int,
    image_width: int,
    image_height: int,
    padding: int,
    area_limit: float,
) -> Optional[CropBox]:
    if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
        return None
    pad = max(int(padding), int(min(image_width, image_height) * 0.01))
    left = max(0, int(x0) - pad)
    top = max(0, int(y0) - pad)
    right = min(image_width, int(x0) + int(width) + pad)
    bottom = min(image_height, int(y0) + int(height) + pad)
    if right <= left or bottom <= top:
        return None
    crop_area = float((right - left) * (bottom - top))
    img_area = float(max(1, image_width * image_height))
    if crop_area >= img_area * area_limit:
        return None
    return CropBox(left, top, right - left, bottom - top)


def _auto_crop_by_edges_cv2(
    image: Image.Image,
    *,
    padding: int,
) -> Optional[CropBox]:
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return None

    width, height = image.size
    if width <= 0 or height <= 0:
        return None

    rgb = np.array(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 160)

    kernel_n = max(3, int(min(width, height) * 0.006))
    if kernel_n % 2 == 0:
        kernel_n += 1
    kernel = np.ones((kernel_n, kernel_n), dtype=np.uint8)
    mask = cv2.dilate(edges, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    img_area = float(max(1, width * height))
    best_box: Optional[Tuple[int, int, int, int]] = None
    best_score = -1.0

    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < img_area * 0.04:
            continue
        x0, y0, ww, hh = cv2.boundingRect(contour)
        if ww <= 0 or hh <= 0:
            continue
        rect_area = float(ww * hh)
        box_ratio = rect_area / img_area
        if box_ratio >= 0.94:
            continue
        region = mask[y0 : y0 + hh, x0 : x0 + ww]
        edge_density = float(np.count_nonzero(region)) / max(1.0, rect_area)
        if edge_density < 0.02:
            continue
        cx = x0 + ww / 2.0
        cy = y0 + hh / 2.0
        dx = abs(cx - width * 0.5) / max(1.0, width * 0.5)
        dy = abs(cy - height * 0.53) / max(1.0, height * 0.53)

        score = area * (0.55 + min(0.7, edge_density * 8.0))
        score *= max(0.20, 1.0 - dx * 0.45)
        score *= max(0.25, 1.0 - dy * 0.20)
        if y0 < height * 0.12:
            score *= 0.70
        if (y0 + hh) > height * 0.94:
            score *= 0.78

        if score > best_score:
            best_score = score
            best_box = (x0, y0, ww, hh)

    if best_box is None:
        return None

    x0, y0, ww, hh = best_box
    return _finalize_crop_box(
        x0=x0,
        y0=y0,
        width=ww,
        height=hh,
        image_width=width,
        image_height=height,
        padding=padding,
        area_limit=0.92,
    )


def _auto_crop_by_color_cv2(
    image: Image.Image,
    *,
    padding: int,
) -> Optional[CropBox]:
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return None

    width, height = image.size
    if width <= 0 or height <= 0:
        return None

    rgb = np.array(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)

    color_mask = cv2.inRange(hsv, (0, 25, 45), (180, 255, 255))
    bright_mask = cv2.inRange(hsv, (0, 0, 145), (180, 90, 255))
    mask = cv2.bitwise_or(color_mask, bright_mask)

    kernel_n = max(3, int(min(width, height) * 0.012))
    if kernel_n % 2 == 0:
        kernel_n += 1
    kernel = np.ones((kernel_n, kernel_n), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.dilate(mask, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    img_area = float(max(1, width * height))
    best_box: Optional[Tuple[int, int, int, int]] = None
    best_score = -1.0
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < img_area * 0.015:
            continue
        x0, y0, ww, hh = cv2.boundingRect(contour)
        if ww <= 0 or hh <= 0:
            continue
        rect_area = float(ww * hh)
        box_ratio = rect_area / img_area
        if box_ratio >= 0.95:
            continue
        fill = area / max(1.0, rect_area)
        cx = x0 + ww / 2.0
        cy = y0 + hh / 2.0
        dx = abs(cx - width / 2.0) / max(1.0, width / 2.0)
        dy = abs(cy - height * 0.56) / max(1.0, height * 0.56)
        score = area * (0.9 + min(0.3, fill))
        score *= max(0.15, 1.0 - dx * 0.40)
        score *= max(0.20, 1.0 - dy * 0.18)
        if y0 < height * 0.10:
            score *= 0.72
        if (y0 + hh) > height * 0.97:
            score *= 0.85
        if score > best_score:
            best_score = score
            best_box = (x0, y0, ww, hh)

    if best_box is None:
        return None
    x0, y0, ww, hh = best_box
    return _finalize_crop_box(
        x0=x0,
        y0=y0,
        width=ww,
        height=hh,
        image_width=width,
        image_height=height,
        padding=padding,
        area_limit=0.97,
    )


def auto_crop(
    image: Image.Image,
    *,
    threshold: int,
    invert: bool,
    padding: int,
) -> Optional[CropBox]:
    edge_crop = _auto_crop_by_edges_cv2(image, padding=padding)
    if edge_crop is not None:
        return edge_crop

    color_crop = _auto_crop_by_color_cv2(image, padding=padding)
    if color_crop is not None:
        return color_crop

    gray = image.convert("L")
    mask = _ink_mask(gray, threshold=threshold, invert=invert)
    bbox = mask.getbbox()
    if not bbox:
        return None

    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    if right <= left or bottom <= top:
        return None

    crop_area = float((right - left) * (bottom - top))
    img_area = float(max(1, image.width * image.height))
    if crop_area >= img_area * 0.98:
        return None

    return CropBox(left, top, right - left, bottom - top)


def apply_crop(image: Image.Image, crop: Optional[CropBox]) -> Image.Image:
    if crop is None:
        return image
    return image.crop((crop.x, crop.y, crop.x + crop.width, crop.y + crop.height))


def _count_runs(flags: Iterable[bool]) -> int:
    runs = 0
    in_run = False
    for flag in flags:
        if flag and not in_run:
            runs += 1
            in_run = True
        elif not flag:
            in_run = False
    return runs


def _cluster_positions(values: List[float], tol: float) -> List[float]:
    if not values:
        return []
    values.sort()
    clusters = [[values[0]]]
    for v in values[1:]:
        if abs(v - clusters[-1][-1]) <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _detect_grid_hough(
    image: Image.Image,
    *,
    line_threshold: float,
    max_dim: int = 800,
) -> Optional[GridDetection]:
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return None

    width, height = image.size
    if width == 0 or height == 0:
        return None

    scale = min(1.0, float(max_dim) / float(max(width, height)))
    img = image
    if scale < 1.0:
        img = image.resize((int(width * scale), int(height * scale)), Image.BILINEAR)
        width, height = img.size

    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    min_len = max(30, int(min(width, height) * max(0.2, min(0.9, line_threshold))))
    max_gap = max(6, int(min(width, height) * 0.02))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=min_len, maxLineGap=max_gap)
    if lines is None:
        return None

    verticals: List[float] = []
    horizontals: List[float] = []
    angle_tol = 10.0
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        dx = x2 - x1
        dy = y2 - y1
        angle = abs(math.degrees(math.atan2(dy, dx)))
        if angle < angle_tol or abs(angle - 180) < angle_tol:
            horizontals.append((y1 + y2) / 2.0)
        elif abs(angle - 90) < angle_tol:
            verticals.append((x1 + x2) / 2.0)

    tol = max(6, int(min(width, height) * 0.01))
    v_clusters = _cluster_positions(verticals, tol)
    h_clusters = _cluster_positions(horizontals, tol)

    if len(v_clusters) < 2 or len(h_clusters) < 2:
        return None

    return GridDetection(
        rows=len(h_clusters) - 1,
        cols=len(v_clusters) - 1,
        vertical_lines=len(v_clusters),
        horizontal_lines=len(h_clusters),
        width=width,
        height=height,
    )


def detect_grid(
    image: Image.Image,
    *,
    threshold: int,
    line_threshold: float,
    invert: bool,
    max_dim: int = 800,
) -> Optional[GridDetection]:
    # Prefer Hough-based line detection when OpenCV is available.
    hough = _detect_grid_hough(image, line_threshold=line_threshold, max_dim=max_dim)
    if hough is not None:
        return hough
    gray = image.convert("L")
    width, height = gray.size
    if width == 0 or height == 0:
        return None

    scale = min(1.0, float(max_dim) / float(max(width, height)))
    if scale < 1.0:
        gray = gray.resize((int(width * scale), int(height * scale)), Image.BILINEAR)
        width, height = gray.size

    pixels = gray.load()
    col_counts = [0] * width
    row_counts = [0] * height

    for y in range(height):
        for x in range(width):
            val = pixels[x, y]
            is_ink = val < threshold if not invert else val > threshold
            if is_ink:
                col_counts[x] += 1
                row_counts[y] += 1

    col_flags = [count / height >= line_threshold for count in col_counts]
    row_flags = [count / width >= line_threshold for count in row_counts]

    vertical_lines = _count_runs(col_flags)
    horizontal_lines = _count_runs(row_flags)
    if vertical_lines < 2 or horizontal_lines < 2:
        return None

    return GridDetection(
        rows=horizontal_lines - 1,
        cols=vertical_lines - 1,
        vertical_lines=vertical_lines,
        horizontal_lines=horizontal_lines,
        width=width,
        height=height,
    )


def _angle_distance_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _cluster_angles_deg(values: List[float], *, tol: float) -> List[float]:
    out: List[float] = []
    for value in values:
        angle = value % 360.0
        assigned = False
        for idx, current in enumerate(out):
            if _angle_distance_deg(angle, current) <= tol:
                cand = [current, angle, current + 360.0, angle + 360.0]
                merged = sum(cand[:2]) / 2.0
                if abs(merged - current) > 180.0:
                    merged = (merged + 180.0) % 360.0
                out[idx] = merged % 360.0
                assigned = True
                break
        if not assigned:
            out.append(angle)
    return sorted(out)


def _sample_circle_edge_strength(
    edges: Any,
    *,
    cx: float,
    cy: float,
    radius: float,
    samples: int,
) -> float:
    h, w = edges.shape[:2]
    if radius <= 0.0 or samples <= 0:
        return 0.0
    hits = 0
    valid = 0
    for i in range(samples):
        theta = 2.0 * math.pi * float(i) / float(samples)
        x = int(round(cx + radius * math.cos(theta)))
        y = int(round(cy + radius * math.sin(theta)))
        if 0 <= x < w and 0 <= y < h:
            valid += 1
            if edges[y, x] > 0:
                hits += 1
    if valid <= 0:
        return 0.0
    return float(hits) / float(valid)


def detect_circle_grid(
    image: Image.Image,
    *,
    min_sectors: int = 3,
    max_sectors: int = 24,
) -> Tuple[Optional[CircleGridDetection], Dict[str, Any]]:
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return None, {"warnings": ["OpenCV is unavailable for circle-grid detection."]}

    width, height = image.size
    if width <= 0 or height <= 0:
        return None, {"warnings": ["Invalid image dimensions for circle-grid detection."]}

    rgb = np.array(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 45, 140)

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, {"warnings": ["No contours detected for circle-grid inference."]}

    img_area = float(max(1, width * height))
    center_ref_x = float(width) * 0.5
    center_ref_y = float(height) * 0.52
    max_reasonable_radius = float(min(width, height)) * 0.72
    best = None
    best_score = -1.0
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < img_area * 0.05:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 1.0:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        if radius < float(min(width, height)) * 0.18:
            continue
        if radius > max_reasonable_radius:
            continue
        edge_cap = min(float(cx), float(cy), float(width) - float(cx), float(height) - float(cy))
        if edge_cap <= 0.0:
            continue
        if radius > edge_cap * 1.12:
            continue
        circularity = (4.0 * math.pi * area) / max(1.0, perimeter * perimeter)
        center_penalty = (
            ((cx - center_ref_x) / max(1.0, width * 0.5)) ** 2
            + ((cy - center_ref_y) / max(1.0, height * 0.6)) ** 2
        )
        score = area * (0.55 + min(1.0, circularity) * 0.75) / (1.0 + center_penalty * 1.3)
        if score > best_score:
            best = (cx, cy, radius, circularity)
            best_score = score

    if best is None:
        return None, {"warnings": ["No circular contour candidates found for circle-grid inference."]}

    cx, cy, outer_radius, circularity = best
    max_r = int(max(8.0, outer_radius * 0.985))
    min_r = int(max(4.0, outer_radius * 0.06))
    step_r = max(1, int(max_r / 260))
    radii: List[int] = list(range(min_r, max_r + 1, step_r))
    if len(radii) < 8:
        return None, {"warnings": ["Not enough radial samples for circle-grid inference."]}

    strengths: List[float] = []
    for r in radii:
        samples = int(max(140.0, min(720.0, 2.0 * math.pi * float(r) * 0.82)))
        strengths.append(_sample_circle_edge_strength(edges, cx=cx, cy=cy, radius=float(r), samples=samples))

    top_strength = max(strengths) if strengths else 0.0
    peak_floor = max(0.028, top_strength * 0.24)
    peaks: List[Tuple[float, float]] = []
    for idx in range(1, len(radii) - 1):
        prev_v = strengths[idx - 1]
        cur_v = strengths[idx]
        next_v = strengths[idx + 1]
        if cur_v < peak_floor:
            continue
        if cur_v >= prev_v and cur_v >= next_v:
            peaks.append((float(radii[idx]), float(cur_v)))

    if not peaks:
        peaks = [(float(max_r), float(top_strength))]

    merge_tol = max(5.0, outer_radius * 0.035)
    merged: List[Tuple[float, float]] = []
    for radius, score in sorted(peaks, key=lambda item: item[0]):
        if not merged:
            merged.append((radius, score))
            continue
        prev_radius, prev_score = merged[-1]
        if abs(radius - prev_radius) <= merge_tol:
            if score > prev_score:
                merged[-1] = (radius, score)
        else:
            merged.append((radius, score))

    ring_candidates = [radius for radius, _score in merged if radius <= outer_radius * 0.98]
    if not ring_candidates:
        ring_candidates = [outer_radius]
    outer_peak = max(ring_candidates)
    outer_radius = max(outer_radius * 0.88, outer_peak)
    edge_outer_cap = min(float(cx), float(cy), float(width) - float(cx), float(height) - float(cy))
    if edge_outer_cap > 0.0:
        outer_radius = min(outer_radius, edge_outer_cap * 1.02)

    inner_peaks = [
        radius
        for radius in ring_candidates
        if radius < outer_radius * 0.93 and radius > outer_radius * 0.07
    ]
    ring_boundaries: List[float]
    if not inner_peaks:
        ring_boundaries = [0.0, outer_radius]
    else:
        ring_boundaries = sorted(inner_peaks) + [outer_radius]
        if ring_boundaries[0] > outer_radius * 0.28:
            ring_boundaries = [0.0] + ring_boundaries

    rings = max(1, len(ring_boundaries) - 1)

    spokes: List[float] = []
    line_min = int(max(16.0, outer_radius * 0.22))
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180.0,
        threshold=max(30, int(outer_radius * 0.12)),
        minLineLength=line_min,
        maxLineGap=max(5, int(outer_radius * 0.05)),
    )
    if lines is not None:
        center_tol = max(8.0, outer_radius * 0.10)
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            dx = float(x2 - x1)
            dy = float(y2 - y1)
            denom = max(1.0, math.hypot(dx, dy))
            dist_center = abs(dy * cx - dx * cy + float(x2 * y1 - y2 * x1)) / denom
            if dist_center > center_tol:
                continue
            for px, py in ((float(x1), float(y1)), (float(x2), float(y2))):
                dist = math.hypot(px - cx, py - cy)
                if dist < outer_radius * 0.42 or dist > outer_radius * 1.05:
                    continue
                angle = (math.degrees(math.atan2(py - cy, px - cx)) + 90.0) % 360.0
                spokes.append(angle)

    if len(spokes) < min_sectors:
        ang_scores: List[Tuple[float, float]] = []
        ann_inner = max(outer_radius * 0.32, ring_boundaries[0] + 2.0)
        ann_outer = outer_radius * 0.95
        radial_samples = 26
        for deg in range(360):
            theta = math.radians(float(deg) - 90.0)
            hits = 0
            seen = 0
            for i in range(radial_samples):
                t = float(i) / float(max(1, radial_samples - 1))
                radius = ann_inner + (ann_outer - ann_inner) * t
                x = int(round(cx + radius * math.cos(theta)))
                y = int(round(cy + radius * math.sin(theta)))
                if 0 <= x < width and 0 <= y < height:
                    seen += 1
                    if edges[y, x] > 0:
                        hits += 1
            score = float(hits) / float(max(1, seen))
            ang_scores.append((float(deg), score))
        top_ang = max((score for _, score in ang_scores), default=0.0)
        ang_floor = max(0.045, top_ang * 0.52)
        for idx in range(360):
            prev_score = ang_scores[(idx - 1) % 360][1]
            cur_score = ang_scores[idx][1]
            next_score = ang_scores[(idx + 1) % 360][1]
            if cur_score >= ang_floor and cur_score >= prev_score and cur_score >= next_score:
                spokes.append(ang_scores[idx][0])

    spoke_tol = max(4.0, min(12.0, 360.0 / float(max(8, max_sectors + 2))))
    spoke_angles = _cluster_angles_deg(spokes, tol=spoke_tol)
    if len(spoke_angles) >= 3:
        sorted_angles = sorted(spoke_angles)
        gaps: List[float] = []
        for idx, angle in enumerate(sorted_angles):
            nxt = sorted_angles[(idx + 1) % len(sorted_angles)]
            gap = (nxt - angle) % 360.0
            if gap > 1e-3:
                gaps.append(gap)
        if gaps:
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
            if median_gap > 1.0:
                est = int(round(360.0 / median_gap))
                if min_sectors <= est <= max_sectors and abs(est - len(spoke_angles)) <= 2:
                    sectors_guess = est
                else:
                    sectors_guess = len(spoke_angles)
            else:
                sectors_guess = len(spoke_angles)
        else:
            sectors_guess = len(spoke_angles)
    else:
        sectors_guess = len(spoke_angles)
    if len(spoke_angles) > max_sectors:
        step = max(1, int(round(float(len(spoke_angles)) / float(max_sectors))))
        spoke_angles = spoke_angles[::step][:max_sectors]
    sectors = max(len(spoke_angles), sectors_guess)
    sectors = max(min_sectors, min(max_sectors, sectors))

    warnings: List[str] = []
    if sectors < min_sectors:
        warnings.append("Insufficient radial spoke signal for circle sectors.")
        return None, {
            "warnings": warnings,
            "contour_circularity": round(float(circularity), 4),
            "ring_candidates": [round(float(r), 2) for r in ring_boundaries],
        }

    inner_radius = float(ring_boundaries[0]) if rings > 1 else 0.0
    detection = CircleGridDetection(
        rings=rings,
        sectors=sectors,
        center_x=float(cx),
        center_y=float(cy),
        outer_radius=float(outer_radius),
        inner_radius=float(inner_radius),
        ring_boundaries=tuple(float(r) for r in ring_boundaries),
        spoke_angles=tuple(float(a) for a in spoke_angles),
    )
    info: Dict[str, Any] = {
        "rings": rings,
        "sectors": sectors,
        "center": {"x": round(float(cx), 2), "y": round(float(cy), 2)},
        "outer_radius": round(float(outer_radius), 2),
        "inner_radius": round(float(inner_radius), 2),
        "ring_boundaries": [round(float(r), 2) for r in ring_boundaries],
        "spoke_angles": [round(float(a), 2) for a in spoke_angles],
        "contour_circularity": round(float(circularity), 4),
        "warnings": warnings,
    }
    return detection, info


def _saturation(color: Tuple[float, float, float]) -> float:
    r, g, b = color
    return max(r, g, b) - min(r, g, b)


def _brightness(color: Tuple[float, float, float]) -> float:
    r, g, b = color
    return (r + g + b) / 3.0


def _color_distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _mean_color(pixels: List[Tuple[int, int, int]]) -> Tuple[float, float, float]:
    if not pixels:
        return (0.0, 0.0, 0.0)
    r = sum(p[0] for p in pixels) / len(pixels)
    g = sum(p[1] for p in pixels) / len(pixels)
    b = sum(p[2] for p in pixels) / len(pixels)
    return (r, g, b)


def _sample_region_color(
    region: Image.Image,
    *,
    sample_max_dim: int = 24,
) -> Tuple[Tuple[float, float, float], float, float]:
    sample = region
    if sample.width > sample_max_dim or sample.height > sample_max_dim:
        scale = min(sample_max_dim / sample.width, sample_max_dim / sample.height)
        sample = sample.resize(
            (max(1, int(sample.width * scale)), max(1, int(sample.height * scale))),
            Image.BILINEAR,
        )
    pixels = list(sample.getdata())
    if not pixels:
        return (0.0, 0.0, 0.0), 0.0, 0.0

    def score(pixel: Tuple[int, int, int]) -> float:
        return _saturation(pixel) + _brightness(pixel) * 0.2

    pixels.sort(key=score, reverse=True)
    top_n = max(6, int(len(pixels) * 0.2))
    selected = pixels[:top_n]
    color = _mean_color(selected)
    return color, _saturation(color), _brightness(color)


def _cluster_candidates(candidates: List[TerminalCandidate], threshold: float) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for cand in candidates:
        assigned = False
        for cluster in clusters:
            if _color_distance(cand.color, cluster["color"]) <= threshold:
                members = cluster["members"]
                members.append(cand)
                count = len(members)
                cluster["color"] = (
                    (cluster["color"][0] * (count - 1) + cand.color[0]) / count,
                    (cluster["color"][1] * (count - 1) + cand.color[1]) / count,
                    (cluster["color"][2] * (count - 1) + cand.color[2]) / count,
                )
                assigned = True
                break
        if not assigned:
            clusters.append({"color": cand.color, "members": [cand]})
    return clusters


def detect_terminals(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
    sat_threshold: float,
    brightness_min: float,
    brightness_max: float,
    margin_ratio: float,
    cluster_threshold: float,
    bg_threshold: float = 40.0,
) -> Tuple[List[TerminalPlacement], Dict[str, Any]]:
    width, height = image.size
    if rows <= 0 or cols <= 0 or width == 0 or height == 0:
        return [], {"warnings": ["Invalid grid size for terminal detection."]}

    cell_w = width / cols
    cell_h = height / rows
    margin_x = cell_w * margin_ratio
    margin_y = cell_h * margin_ratio

    # Estimate background from border pixels.
    border = image.crop((0, 0, width, max(1, int(height * 0.05))))
    border2 = image.crop((0, height - max(1, int(height * 0.05)), width, height))
    border3 = image.crop((0, 0, max(1, int(width * 0.05)), height))
    border4 = image.crop((width - max(1, int(width * 0.05)), 0, width, height))
    border_stat = ImageStat.Stat(border)
    border_stat2 = ImageStat.Stat(border2)
    border_stat3 = ImageStat.Stat(border3)
    border_stat4 = ImageStat.Stat(border4)
    bg_color = (
        (border_stat.mean[0] + border_stat2.mean[0] + border_stat3.mean[0] + border_stat4.mean[0]) / 4.0,
        (border_stat.mean[1] + border_stat2.mean[1] + border_stat3.mean[1] + border_stat4.mean[1]) / 4.0,
        (border_stat.mean[2] + border_stat2.mean[2] + border_stat3.mean[2] + border_stat4.mean[2]) / 4.0,
    )
    bg_brightness = _brightness(bg_color)
    neutral_brightness_min = max(brightness_min, bg_brightness + 25.0, 140.0)
    neutral_brightness_max = max(brightness_max, 250.0)
    neutral_dist = max(bg_threshold * 1.5, bg_threshold + 20.0)

    candidates: List[TerminalCandidate] = []
    for row in range(rows):
        for col in range(cols):
            x0 = int(col * cell_w + margin_x)
            y0 = int(row * cell_h + margin_y)
            x1 = int((col + 1) * cell_w - margin_x)
            y1 = int((row + 1) * cell_h - margin_y)
            if x1 <= x0 or y1 <= y0:
                continue

            region = image.crop((x0, y0, x1, y1))
            color, sat, bright = _sample_region_color(region)
            dist_bg = _color_distance(color, bg_color)
            is_colorful = sat >= sat_threshold and brightness_min <= bright <= brightness_max and dist_bg >= bg_threshold
            is_neutral = (
                sat < sat_threshold
                and bright >= neutral_brightness_min
                and bright <= neutral_brightness_max
                and bright >= bg_brightness + 35.0
                and dist_bg >= neutral_dist
            )
            if is_colorful or is_neutral:
                candidates.append(
                    TerminalCandidate(row=row, col=col, color=color, saturation=sat, brightness=bright)
                )

    clusters = _cluster_candidates(candidates, cluster_threshold)
    if clusters:
        refined: List[Dict[str, Any]] = []
        split_threshold = max(12.0, cluster_threshold * 0.6)
        for cluster in clusters:
            if len(cluster["members"]) <= 2:
                refined.append(cluster)
                continue
            subclusters = _cluster_candidates(cluster["members"], split_threshold)
            if len(subclusters) == 1:
                refined.append(cluster)
            else:
                refined.extend(subclusters)
        clusters = refined

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    placements: List[TerminalPlacement] = []
    warnings: List[str] = []

    clusters_sorted = sorted(clusters, key=lambda c: len(c["members"]), reverse=True)
    for idx, cluster in enumerate(clusters_sorted):
        if idx >= len(letters):
            warnings.append("Too many terminal colors detected; truncating.")
            break
        members = sorted(cluster["members"], key=lambda c: c.saturation, reverse=True)
        if len(members) < 2:
            warnings.append("Detected a color with fewer than 2 terminals; ignoring.")
            continue
        for cand in members[:2]:
            placements.append(
                TerminalPlacement(
                    row=cand.row,
                    col=cand.col,
                    letter=letters[idx],
                    color=cand.color,
                )
            )
        if len(members) > 2:
            warnings.append("Detected more than 2 terminals for a color; using strongest 2.")

    info = {
        "clusters": [
            {
                "color": [round(c, 2) for c in cluster["color"]],
                "count": len(cluster["members"]),
            }
            for cluster in clusters_sorted
        ],
        "candidates": len(candidates),
        "warnings": warnings,
        "background_color": [round(c, 2) for c in bg_color],
    }
    return placements, info


def detect_circle_terminals(
    image: Image.Image,
    *,
    rings: int,
    sectors: int,
    sat_threshold: float,
    brightness_min: float,
    brightness_max: float,
    margin_ratio: float,
    cluster_threshold: float,
    bg_threshold: float = 40.0,
    circle_grid: Optional[CircleGridDetection] = None,
) -> Tuple[List[TerminalPlacement], Dict[str, Any]]:
    width, height = image.size
    if rings <= 0 or sectors <= 0 or width <= 0 or height <= 0:
        return [], {"warnings": ["Invalid circle dimensions for terminal detection."]}

    circle_info: Dict[str, Any] = {}
    if circle_grid is None:
        detected, info = detect_circle_grid(
            image,
            min_sectors=max(3, min(sectors, 12)),
            max_sectors=max(sectors + 6, 24),
        )
        circle_grid = detected
        circle_info = info
    else:
        circle_info = {
            "rings": int(circle_grid.rings),
            "sectors": int(circle_grid.sectors),
            "center": {"x": round(float(circle_grid.center_x), 2), "y": round(float(circle_grid.center_y), 2)},
            "outer_radius": round(float(circle_grid.outer_radius), 2),
            "inner_radius": round(float(circle_grid.inner_radius), 2),
            "ring_boundaries": [round(float(r), 2) for r in circle_grid.ring_boundaries],
            "spoke_angles": [round(float(a), 2) for a in circle_grid.spoke_angles],
            "warnings": [],
        }

    if circle_grid is None:
        return [], {"warnings": ["Circle grid geometry was not detected for terminal sampling."], "circle": circle_info}

    cx = float(circle_grid.center_x)
    cy = float(circle_grid.center_y)
    outer_radius = float(circle_grid.outer_radius)

    detected_bounds = [float(r) for r in circle_grid.ring_boundaries]
    if len(detected_bounds) < 2:
        detected_bounds = [0.0, outer_radius]
    detected_inner = detected_bounds[0] if detected_bounds[0] > outer_radius * 0.12 else 0.0
    detected_rings = max(1, len(detected_bounds) - 1)
    if detected_rings == rings:
        ring_bounds = detected_bounds
    else:
        ring_bounds = [
            detected_inner + (outer_radius - detected_inner) * float(i) / float(max(1, rings))
            for i in range(rings + 1)
        ]

    phase_deg = 0.0
    if circle_grid.spoke_angles:
        step = 360.0 / float(max(1, sectors))
        phase_samples = sorted((float(angle) % step) for angle in circle_grid.spoke_angles)
        phase_deg = phase_samples[len(phase_samples) // 2] if phase_samples else 0.0

    # Estimate background from border strips.
    border = image.crop((0, 0, width, max(1, int(height * 0.05))))
    border2 = image.crop((0, height - max(1, int(height * 0.05)), width, height))
    border3 = image.crop((0, 0, max(1, int(width * 0.05)), height))
    border4 = image.crop((width - max(1, int(width * 0.05)), 0, width, height))
    border_stat = ImageStat.Stat(border)
    border_stat2 = ImageStat.Stat(border2)
    border_stat3 = ImageStat.Stat(border3)
    border_stat4 = ImageStat.Stat(border4)
    bg_color = (
        (border_stat.mean[0] + border_stat2.mean[0] + border_stat3.mean[0] + border_stat4.mean[0]) / 4.0,
        (border_stat.mean[1] + border_stat2.mean[1] + border_stat3.mean[1] + border_stat4.mean[1]) / 4.0,
        (border_stat.mean[2] + border_stat2.mean[2] + border_stat3.mean[2] + border_stat4.mean[2]) / 4.0,
    )
    bg_brightness = _brightness(bg_color)
    neutral_brightness_min = max(brightness_min, bg_brightness + 25.0, 140.0)
    neutral_brightness_max = max(brightness_max, 250.0)
    neutral_dist = max(bg_threshold * 1.5, bg_threshold + 20.0)

    candidates: List[TerminalCandidate] = []
    sector_step = 360.0 / float(max(1, sectors))
    for row in range(rings):
        r0 = float(ring_bounds[row])
        r1 = float(ring_bounds[row + 1])
        if r1 <= r0 + 1.0:
            continue
        radial_margin = max(1.0, (r1 - r0) * max(0.02, min(0.34, margin_ratio * 0.62)))
        cell_radius = max(r0 + radial_margin, min(r1 - radial_margin, (r0 + r1) * 0.5))
        arc_len = max(4.0, 2.0 * math.pi * max(1.0, cell_radius) / float(max(1, sectors)))
        sample_radius = int(max(3.0, min(20.0, min((r1 - r0), arc_len) * 0.35)))
        for col in range(sectors):
            ang = (phase_deg + (float(col) + 0.5) * sector_step) % 360.0
            theta = math.radians(ang - 90.0)
            px = cx + cell_radius * math.cos(theta)
            py = cy + cell_radius * math.sin(theta)
            x0 = max(0, int(round(px)) - sample_radius)
            y0 = max(0, int(round(py)) - sample_radius)
            x1 = min(width, int(round(px)) + sample_radius + 1)
            y1 = min(height, int(round(py)) + sample_radius + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            region = image.crop((x0, y0, x1, y1))
            color, sat, bright = _sample_region_color(region, sample_max_dim=max(12, sample_radius * 2))
            dist_bg = _color_distance(color, bg_color)
            is_colorful = sat >= sat_threshold and brightness_min <= bright <= brightness_max and dist_bg >= bg_threshold
            is_neutral = (
                sat < sat_threshold
                and bright >= neutral_brightness_min
                and bright <= neutral_brightness_max
                and bright >= bg_brightness + 35.0
                and dist_bg >= neutral_dist
            )
            if is_colorful or is_neutral:
                candidates.append(
                    TerminalCandidate(
                        row=row,
                        col=col,
                        color=color,
                        saturation=sat,
                        brightness=bright,
                    )
                )

    clusters = _cluster_candidates(candidates, cluster_threshold)
    if clusters:
        refined: List[Dict[str, Any]] = []
        split_threshold = max(12.0, cluster_threshold * 0.6)
        for cluster in clusters:
            members = cluster.get("members", [])
            if len(members) <= 2:
                refined.append(cluster)
                continue
            subclusters = _cluster_candidates(members, split_threshold)
            if len(subclusters) == 1:
                refined.append(cluster)
            else:
                refined.extend(subclusters)
        clusters = refined

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    placements: List[TerminalPlacement] = []
    warnings: List[str] = []
    clusters_sorted = sorted(clusters, key=lambda c: len(c["members"]), reverse=True)
    for idx, cluster in enumerate(clusters_sorted):
        if idx >= len(letters):
            warnings.append("Too many terminal colors detected; truncating.")
            break
        members = sorted(cluster["members"], key=lambda c: c.saturation, reverse=True)
        if len(members) < 2:
            warnings.append("Detected a color with fewer than 2 terminals; ignoring.")
            continue
        for cand in members[:2]:
            placements.append(
                TerminalPlacement(
                    row=cand.row,
                    col=cand.col,
                    letter=letters[idx],
                    color=cand.color,
                )
            )
        if len(members) > 2:
            warnings.append("Detected more than 2 terminals for a color; using strongest 2.")

    info: Dict[str, Any] = {
        "mode": "circle",
        "rings": rings,
        "sectors": sectors,
        "phase_deg": round(float(phase_deg), 2),
        "circle": {
            "center": {"x": round(cx, 2), "y": round(cy, 2)},
            "outer_radius": round(outer_radius, 2),
            "ring_boundaries": [round(float(r), 2) for r in ring_bounds],
        },
        "clusters": [
            {
                "color": [round(c, 2) for c in cluster["color"]],
                "count": len(cluster["members"]),
            }
            for cluster in clusters_sorted
        ],
        "candidates": len(candidates),
        "warnings": warnings,
        "background_color": [round(c, 2) for c in bg_color],
        "circle_detection": circle_info,
    }
    return placements, info


def _cluster_node_candidates(
    candidates: List[Dict[str, Any]],
    threshold: float,
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for cand in candidates:
        assigned = False
        for cluster in clusters:
            if _color_distance(cand["color"], cluster["color"]) <= threshold:
                members = cluster["members"]
                members.append(cand)
                count = len(members)
                cluster["color"] = (
                    (cluster["color"][0] * (count - 1) + cand["color"][0]) / count,
                    (cluster["color"][1] * (count - 1) + cand["color"][1]) / count,
                    (cluster["color"][2] * (count - 1) + cand["color"][2]) / count,
                )
                assigned = True
                break
        if not assigned:
            clusters.append({"color": cand["color"], "members": [cand]})
    return clusters


def _project_nodes_to_pixels(
    image: Image.Image,
    *,
    nodes: Dict[str, Dict[str, Any]],
    margin_ratio: float,
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Any]]:
    width, height = image.size
    if width <= 0 or height <= 0 or not nodes:
        return {}, {"warnings": ["Invalid dimensions or empty graph nodes."]}

    node_positions: List[Tuple[str, float, float]] = []
    for node_id, node in nodes.items():
        pos = node.get("pos", [0.0, 0.0, 0.0]) if isinstance(node, dict) else [0.0, 0.0, 0.0]
        if not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        try:
            x = float(pos[0])
            y = float(pos[1])
        except Exception:
            continue
        node_positions.append((str(node_id), x, y))

    if len(node_positions) < 2:
        return {}, {"warnings": ["Not enough nodes for topology terminal detection."]}

    xs = [item[1] for item in node_positions]
    ys = [item[2] for item in node_positions]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    span_x = max(1e-6, max_x - min_x)
    span_y = max(1e-6, max_y - min_y)

    margin_x = max(4.0, float(width) * max(0.04, min(0.24, margin_ratio * 1.1)))
    margin_y = max(4.0, float(height) * max(0.04, min(0.24, margin_ratio * 1.1)))
    usable_w = max(1.0, float(width) - margin_x * 2.0)
    usable_h = max(1.0, float(height) - margin_y * 2.0)

    projected: Dict[str, Tuple[float, float]] = {}
    for node_id, x, y in node_positions:
        nx = 0.5 if span_x <= 1e-6 else (x - min_x) / span_x
        ny = 0.5 if span_y <= 1e-6 else (max_y - y) / span_y
        px = margin_x + nx * usable_w
        py = margin_y + ny * usable_h
        projected[node_id] = (px, py)

    info = {
        "bounds": {
            "min_x": round(min_x, 4),
            "max_x": round(max_x, 4),
            "min_y": round(min_y, 4),
            "max_y": round(max_y, 4),
        },
        "margin": {"x": round(margin_x, 2), "y": round(margin_y, 2)},
        "projected_nodes": len(projected),
    }
    return projected, info


def detect_terminals_on_nodes(
    image: Image.Image,
    *,
    nodes: Dict[str, Dict[str, Any]],
    sat_threshold: float,
    brightness_min: float,
    brightness_max: float,
    margin_ratio: float,
    cluster_threshold: float,
    bg_threshold: float = 40.0,
) -> Tuple[List[TerminalNodePlacement], Dict[str, Any]]:
    width, height = image.size
    projected, proj_info = _project_nodes_to_pixels(image, nodes=nodes, margin_ratio=margin_ratio)
    if not projected:
        return [], {
            "warnings": list(proj_info.get("warnings", ["No projected graph nodes found."])),
            "projected_nodes": 0,
        }

    border = image.crop((0, 0, width, max(1, int(height * 0.05))))
    border2 = image.crop((0, height - max(1, int(height * 0.05)), width, height))
    border3 = image.crop((0, 0, max(1, int(width * 0.05)), height))
    border4 = image.crop((width - max(1, int(width * 0.05)), 0, width, height))
    border_stat = ImageStat.Stat(border)
    border_stat2 = ImageStat.Stat(border2)
    border_stat3 = ImageStat.Stat(border3)
    border_stat4 = ImageStat.Stat(border4)
    bg_color = (
        (border_stat.mean[0] + border_stat2.mean[0] + border_stat3.mean[0] + border_stat4.mean[0]) / 4.0,
        (border_stat.mean[1] + border_stat2.mean[1] + border_stat3.mean[1] + border_stat4.mean[1]) / 4.0,
        (border_stat.mean[2] + border_stat2.mean[2] + border_stat3.mean[2] + border_stat4.mean[2]) / 4.0,
    )
    bg_brightness = _brightness(bg_color)
    neutral_brightness_min = max(brightness_min, bg_brightness + 25.0, 140.0)
    neutral_brightness_max = max(brightness_max, 250.0)
    neutral_dist = max(bg_threshold * 1.5, bg_threshold + 20.0)

    points = list(projected.values())
    nn_dists: List[float] = []
    for idx, (px, py) in enumerate(points):
        best = None
        for jdx, (qx, qy) in enumerate(points):
            if idx == jdx:
                continue
            dist = math.hypot(px - qx, py - qy)
            if best is None or dist < best:
                best = dist
        if best is not None:
            nn_dists.append(best)
    median_nn = 0.0
    if nn_dists:
        sorted_nn = sorted(nn_dists)
        median_nn = sorted_nn[len(sorted_nn) // 2]
    sample_radius = int(
        max(
            4.0,
            min(
                24.0,
                median_nn * 0.24 if median_nn > 0.0 else float(min(width, height)) * 0.03,
            ),
        )
    )

    candidates: List[Dict[str, Any]] = []
    for node_id, (px, py) in projected.items():
        x0 = max(0, int(px) - sample_radius)
        y0 = max(0, int(py) - sample_radius)
        x1 = min(width, int(px) + sample_radius + 1)
        y1 = min(height, int(py) + sample_radius + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        region = image.crop((x0, y0, x1, y1))
        color, sat, bright = _sample_region_color(region, sample_max_dim=max(12, sample_radius * 2))
        dist_bg = _color_distance(color, bg_color)
        is_colorful = sat >= sat_threshold and brightness_min <= bright <= brightness_max and dist_bg >= bg_threshold
        is_neutral = (
            sat < sat_threshold
            and bright >= neutral_brightness_min
            and bright <= neutral_brightness_max
            and bright >= bg_brightness + 35.0
            and dist_bg >= neutral_dist
        )
        if not (is_colorful or is_neutral):
            continue
        score = sat * 1.1 + dist_bg * 0.75 + max(0.0, bright - bg_brightness) * 0.2
        candidates.append(
            {
                "node_id": node_id,
                "color": color,
                "saturation": sat,
                "brightness": bright,
                "distance_bg": dist_bg,
                "score": score,
            }
        )

    clusters = _cluster_node_candidates(candidates, cluster_threshold)
    if clusters:
        refined: List[Dict[str, Any]] = []
        split_threshold = max(12.0, cluster_threshold * 0.6)
        for cluster in clusters:
            members = cluster.get("members", [])
            if len(members) <= 2:
                refined.append(cluster)
                continue
            subclusters = _cluster_node_candidates(members, split_threshold)
            if len(subclusters) == 1:
                refined.append(cluster)
            else:
                refined.extend(subclusters)
        clusters = refined

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    placements: List[TerminalNodePlacement] = []
    warnings: List[str] = []
    clusters_sorted = sorted(clusters, key=lambda c: len(c.get("members", [])), reverse=True)
    for idx, cluster in enumerate(clusters_sorted):
        if idx >= len(letters):
            warnings.append("Too many terminal colors detected; truncating.")
            break
        members = sorted(cluster.get("members", []), key=lambda item: float(item.get("score", 0.0)), reverse=True)
        unique: List[Dict[str, Any]] = []
        seen_nodes: set[str] = set()
        for member in members:
            node_id = str(member.get("node_id", ""))
            if not node_id or node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            unique.append(member)
        if len(unique) < 2:
            warnings.append("Detected a color with fewer than 2 terminals; ignoring.")
            continue
        for member in unique[:2]:
            placements.append(
                TerminalNodePlacement(
                    node_id=str(member["node_id"]),
                    letter=letters[idx],
                    color=tuple(member["color"]),
                )
            )
        if len(unique) > 2:
            warnings.append("Detected more than 2 terminals for a color; using strongest 2.")

    info: Dict[str, Any] = {
        "projected_nodes": len(projected),
        "sample_radius": sample_radius,
        "candidates": len(candidates),
        "clusters": [
            {"color": [round(c, 2) for c in cluster["color"]], "count": len(cluster.get("members", []))}
            for cluster in clusters_sorted
        ],
        "warnings": warnings,
        "background_color": [round(c, 2) for c in bg_color],
        "projection": proj_info,
    }
    return placements, info


def build_graph_terminals_from_node_placements(
    placements: List[TerminalNodePlacement],
) -> Dict[str, List[str]]:
    by_letter: Dict[str, List[str]] = {}
    for placement in placements:
        by_letter.setdefault(placement.letter, []).append(placement.node_id)
    out: Dict[str, List[str]] = {}
    for letter, node_ids in by_letter.items():
        if len(node_ids) >= 2:
            out[letter] = node_ids[:2]
    return out


def _hint_tokens(text: str) -> List[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [tok for tok in cleaned.split() if tok]


def classify_level_type(
    image: Image.Image,
    *,
    threshold: int = 230,
    line_threshold: float = 0.6,
    invert: bool = False,
    file_hint: Optional[str] = None,
) -> LevelTypeDetection:
    """Classify board geometry + mode modifiers from a screenshot crop.

    This is intentionally heuristic-first. It returns ranked candidates so the UI
    can keep a manual override when confidence is low.
    """

    scores: Dict[str, float] = {
        "square": 0.30,
        "hex": 0.24,
        "circle": 0.22,
        "graph": 0.18,
        "cube": 0.14,
        "star": 0.14,
        "figure8": 0.14,
    }
    modifier_scores: Dict[str, float] = {
        "bridges": 0.0,
        "warps": 0.0,
        "walls": 0.0,
    }
    warnings: List[str] = []
    signals: Dict[str, Any] = {}

    hint_tokens: List[str] = []
    if file_hint:
        hint_tokens = _hint_tokens(file_hint)
        signals["hint_tokens"] = hint_tokens
        if any(tok.startswith("hex") for tok in hint_tokens):
            scores["hex"] += 0.45
        if any(tok in {"circle", "ring", "rings", "radial"} for tok in hint_tokens):
            scores["circle"] += 0.45
        if any(tok.startswith("cube") for tok in hint_tokens):
            scores["cube"] += 0.55
        if any(tok.startswith("star") for tok in hint_tokens):
            scores["star"] += 0.55
        if any(tok in {"figure8", "figure", "lemniscate", "infinity"} for tok in hint_tokens):
            scores["figure8"] += 0.60
        if any(tok in {"graph", "freeform", "free", "custom"} for tok in hint_tokens):
            scores["graph"] += 0.40
        if any(tok.startswith("bridge") for tok in hint_tokens):
            modifier_scores["bridges"] += 0.70
        if any(tok.startswith("warp") or tok == "portal" for tok in hint_tokens):
            modifier_scores["warps"] += 0.75
        if any(tok.startswith("wall") or tok in {"blocked", "blockers"} for tok in hint_tokens):
            modifier_scores["walls"] += 0.60

    grid = detect_grid(image, threshold=threshold, line_threshold=line_threshold, invert=invert)
    if grid is not None:
        signals["grid"] = {
            "rows": grid.rows,
            "cols": grid.cols,
            "vertical_lines": grid.vertical_lines,
            "horizontal_lines": grid.horizontal_lines,
        }
        scores["square"] += 0.40
        if abs(grid.vertical_lines - grid.horizontal_lines) <= 2:
            scores["square"] += 0.12
        if grid.rows > 0 and grid.cols > 0:
            ratio = float(grid.cols) / float(max(1, grid.rows))
            signals["grid_ratio"] = round(ratio, 3)
            if 0.8 <= ratio <= 1.25:
                scores["square"] += 0.06
    else:
        warnings.append("Grid lines were not strongly detected.")

    cv2, np = _try_import_cv2()
    if cv2 is not None and np is not None:
        gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        h, w = gray.shape[:2]

        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=max(60, int(min(h, w) * 0.35)))
        if lines is not None:
            angles: List[float] = []
            for item in lines[:240]:
                theta = float(item[0][1])
                deg = (math.degrees(theta) + 180.0) % 180.0
                if deg > 90.0:
                    deg = 180.0 - deg
                angles.append(deg)

            total = len(angles)
            if total > 0:
                tol = 12.0

                def _near(target: float) -> int:
                    return sum(1 for a in angles if abs(a - target) <= tol)

                hv = _near(0.0) + _near(90.0)
                d30 = _near(30.0)
                d60 = _near(60.0)
                hv_ratio = hv / total
                d30_ratio = d30 / total
                d60_ratio = d60 / total
                diag_ratio = d30_ratio + d60_ratio
                signals["line_orientation"] = {
                    "count": total,
                    "hv_ratio": round(hv_ratio, 3),
                    "diag30_ratio": round(d30_ratio, 3),
                    "diag60_ratio": round(d60_ratio, 3),
                }
                if hv_ratio >= 0.52 and diag_ratio <= 0.35:
                    scores["square"] += 0.25
                if diag_ratio >= 0.46:
                    scores["hex"] += 0.27
                elif diag_ratio >= 0.28:
                    scores["hex"] += 0.16
                if 0.22 <= hv_ratio <= 0.62 and 0.22 <= diag_ratio <= 0.66:
                    scores["cube"] += 0.18
                if diag_ratio >= 0.42 and hv_ratio <= 0.34:
                    scores["star"] += 0.10
        else:
            warnings.append("Hough line orientation signal unavailable.")

        # Boundary-shape heuristics (robust to thick puzzle borders).
        # Use RETR_LIST so we can avoid selecting the full-screen frame contour.
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            frame_pad = 2
            img_area = float(max(1, h * w))
            chosen = None
            chosen_meta: Dict[str, float] = {}
            chosen_score = -1.0

            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area <= 0.0:
                    continue
                x0, y0, ww, hh = cv2.boundingRect(contour)
                if ww <= 0 or hh <= 0:
                    continue
                area_ratio = area / img_area
                if area_ratio < 0.02:
                    continue
                touches_frame = (
                    x0 <= frame_pad
                    or y0 <= frame_pad
                    or (x0 + ww) >= (w - frame_pad)
                    or (y0 + hh) >= (h - frame_pad)
                )
                # The full-screen frame contour is rarely the puzzle boundary.
                if touches_frame and area_ratio >= 0.94:
                    continue
                extent = area / max(1.0, float(ww * hh))
                # Bias toward large, compact board outlines while preferring non-frame shapes.
                score = area * (0.75 + min(0.35, extent))
                if touches_frame:
                    score *= 0.78
                if score > chosen_score:
                    chosen = contour
                    chosen_score = score
                    chosen_meta = {
                        "area_ratio": area_ratio,
                        "extent": extent,
                        "touches_frame": 1.0 if touches_frame else 0.0,
                    }

            if chosen is None:
                chosen = max(contours, key=cv2.contourArea)
                area = float(cv2.contourArea(chosen))
                x0, y0, ww, hh = cv2.boundingRect(chosen)
                chosen_meta = {
                    "area_ratio": area / img_area if img_area > 0 else 0.0,
                    "extent": area / max(1.0, float(ww * hh)),
                    "touches_frame": 1.0,
                }

            area = float(cv2.contourArea(chosen))
            perimeter = float(cv2.arcLength(chosen, True))
            if area > 0.0 and perimeter > 0.0:
                epsilon = 0.02 * perimeter
                approx = cv2.approxPolyDP(chosen, epsilon, True)
                hull = cv2.convexHull(chosen)
                hull_area = max(1.0, float(cv2.contourArea(hull)))
                x0, y0, ww, hh = cv2.boundingRect(chosen)
                extent = area / max(1.0, float(ww * hh))
                aspect_ratio = float(hh) / max(1.0, float(ww))
                solidity = area / hull_area
                circularity = (4.0 * math.pi * area) / (perimeter * perimeter)
                moments = cv2.moments(chosen)
                radial_spread = 0.0
                if moments.get("m00", 0.0):
                    cx = moments["m10"] / moments["m00"]
                    cy = moments["m01"] / moments["m00"]
                    pts = chosen.reshape(-1, 2)
                    dists = [math.hypot(float(px) - cx, float(py) - cy) for px, py in pts]
                    if dists:
                        mean_dist = max(1e-3, sum(dists) / len(dists))
                        var = sum((d - mean_dist) ** 2 for d in dists) / len(dists)
                        radial_spread = math.sqrt(var) / mean_dist

                signals["shape_contour"] = {
                    "area_ratio": round(area / float(max(1, h * w)), 4),
                    "vertices": int(len(approx)),
                    "extent": round(extent, 4),
                    "aspect_ratio": round(aspect_ratio, 4),
                    "solidity": round(solidity, 4),
                    "circularity": round(circularity, 4),
                    "radial_spread": round(radial_spread, 4),
                    "touches_frame": bool(chosen_meta.get("touches_frame", 0.0)),
                }

                if len(approx) >= 8 and solidity < 0.82 and radial_spread >= 0.18:
                    scores["star"] += 0.42
                if 5 <= len(approx) <= 7 and 0.55 <= extent <= 0.9 and 0.72 <= solidity <= 0.95:
                    scores["cube"] += 0.30
                if len(approx) >= 9 and 0.42 <= extent <= 0.72 and 0.72 <= solidity <= 0.95:
                    scores["cube"] += 0.20
                if len(approx) >= 9 and extent <= 0.70 and radial_spread >= 0.15:
                    scores["star"] += 0.22
                # Distinguish deeply concave stars from hex-like diagonal grids.
                if len(approx) >= 10 and solidity <= 0.74 and extent <= 0.62 and radial_spread >= 0.13:
                    scores["star"] += 0.40
                    scores["hex"] -= 0.08
                # Figure-8 boards can look star/circle-like if only contour signal is available.
                if (
                    radial_spread >= 0.17
                    and (
                        (4 <= len(approx) <= 6 and solidity >= 0.90 and 0.60 <= extent <= 0.76)
                        or (
                            len(approx) >= 9
                            and 0.82 <= solidity <= 0.94
                            and 0.60 <= extent <= 0.75
                            and aspect_ratio >= 1.40
                        )
                    )
                ):
                    scores["figure8"] += 0.52
                    scores["square"] -= 0.07
                    scores["circle"] -= 0.05
                if circularity >= 0.72 and 0.68 <= extent <= 0.95:
                    scores["circle"] += 0.12
                # Non-rectangular thick-border shapes should lean away from square.
                if len(approx) > 6 and radial_spread >= 0.14:
                    scores["square"] -= 0.05

        circles = cv2.HoughCircles(
            blur,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(8.0, float(min(h, w)) * 0.1),
            param1=120,
            param2=24,
            minRadius=max(6, int(min(h, w) * 0.08)),
            maxRadius=int(min(h, w) * 0.48),
        )
        if circles is not None and len(circles) > 0:
            det = circles[0]
            centers = [(float(c[0]), float(c[1])) for c in det]
            tol = float(min(h, w)) * 0.08
            best_cluster = 0
            for cx, cy in centers:
                n = sum(
                    1
                    for ox, oy in centers
                    if ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5 <= tol
                )
                best_cluster = max(best_cluster, n)
            signals["circles_detected"] = int(len(det))
            signals["circle_center_cluster"] = best_cluster
            if best_cluster >= 2:
                scores["circle"] += 0.50
            elif len(det) > 24:
                # Many terminal dots can trigger dozens of weak circles; don't over-bias circle geometry.
                scores["circle"] += 0.04
            else:
                scores["circle"] += 0.18

            # Cluster circle centers into coarse groups to detect figure-8 layouts.
            groups: List[Tuple[float, float, int]] = []
            for cx, cy in centers:
                assigned = False
                for idx, (gx, gy, cnt) in enumerate(groups):
                    if math.hypot(cx - gx, cy - gy) <= tol:
                        new_cnt = cnt + 1
                        groups[idx] = ((gx * cnt + cx) / new_cnt, (gy * cnt + cy) / new_cnt, new_cnt)
                        assigned = True
                        break
                if not assigned:
                    groups.append((cx, cy, 1))
            strong_groups = [g for g in groups if g[2] >= 2]
            if len(strong_groups) >= 2:
                strong_groups.sort(key=lambda g: g[2], reverse=True)
                g1 = strong_groups[0]
                g2 = strong_groups[1]
                center_dist = math.hypot(g1[0] - g2[0], g1[1] - g2[1])
                signals["circle_groups"] = [
                    {"x": round(g[0], 2), "y": round(g[1], 2), "count": g[2]} for g in strong_groups[:4]
                ]
                if center_dist >= float(min(h, w)) * 0.22:
                    scores["figure8"] += 0.48
                else:
                    scores["figure8"] += 0.16
    else:
        warnings.append("OpenCV is unavailable; classifier is using lightweight fallback signals.")

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    geometry = ordered[0][0]
    top_score = ordered[0][1]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    margin = max(0.0, top_score - second_score)

    selected_modifiers = tuple(
        sorted(mod for mod, score in modifier_scores.items() if score >= 0.60)
    )
    weak_modifier_hints = {mod: score for mod, score in modifier_scores.items() if 0.40 <= score < 0.60}
    if weak_modifier_hints:
        signals["modifier_hints"] = {k: round(v, 3) for k, v in weak_modifier_hints.items()}
        warnings.append("Modifier hints were weak; modifiers were left unset.")

    if geometry != "square" and "bridges" in selected_modifiers:
        selected_modifiers = tuple(mod for mod in selected_modifiers if mod != "bridges")
        warnings.append("Bridge modifier dropped because detected geometry is not square.")

    total_score = sum(max(0.001, score) for _kind, score in ordered)
    candidates: List[LevelTypeCandidate] = []
    for idx, (kind, score) in enumerate(ordered):
        confidence = max(0.01, min(0.99, score / total_score))
        mods = selected_modifiers if idx == 0 else tuple()
        reason = "highest score" if idx == 0 else "alternate geometry candidate"
        candidates.append(
            LevelTypeCandidate(
                geometry=kind,
                modifiers=mods,
                confidence=confidence,
                reason=reason,
            )
        )

    confidence = max(0.35, min(0.99, (top_score / total_score) + margin * 0.30))
    return LevelTypeDetection(
        geometry=geometry,
        modifiers=selected_modifiers,
        confidence=confidence,
        candidates=candidates,
        signals=signals,
        warnings=warnings,
    )


def build_grid(
    *,
    rows: int,
    cols: int,
    terminals: List[TerminalPlacement],
    fallback: bool = True,
) -> Tuple[List[List[str]], List[str]]:
    grid = [["." for _ in range(cols)] for _ in range(rows)]
    warnings: List[str] = []

    by_letter: Dict[str, List[TerminalPlacement]] = {}
    for t in terminals:
        by_letter.setdefault(t.letter, []).append(t)

    placements: List[TerminalPlacement] = []
    for letter, items in by_letter.items():
        if len(items) >= 2:
            placements.extend(items[:2])
        else:
            warnings.append(f"Terminal {letter} detected only once; skipped.")

    if placements:
        for t in placements:
            if 0 <= t.row < rows and 0 <= t.col < cols:
                grid[t.row][t.col] = t.letter
    elif fallback and rows * cols >= 2:
        grid[0][0] = "A"
        grid[rows - 1][cols - 1] = "A"
        warnings.append("No terminals detected; placed default A pair.")

    return grid, warnings


def build_flow_text(board_type: str, grid: List[List[str]], meta: Dict[str, str]) -> str:
    lines = [f"# type: {board_type}", "# fill: true"]
    for key, value in meta.items():
        if value:
            lines.append(f"# {key}: {value}")
    lines.extend("".join(row) for row in grid)
    return "\n".join(lines).rstrip() + "\n"


def _normalize_edge_pair(u: str, v: str) -> Optional[Tuple[str, str]]:
    u_id = str(u)
    v_id = str(v)
    if not u_id or not v_id or u_id == v_id:
        return None
    return (u_id, v_id) if u_id < v_id else (v_id, u_id)


def _dedupe_edge_pairs(edges: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen: set[Tuple[str, str]] = set()
    out: List[Tuple[str, str]] = []
    for u, v in edges:
        pair = _normalize_edge_pair(u, v)
        if pair is None or pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


def detect_wall_edges(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
    sample_span_ratio: float = 0.55,
    sample_thickness_ratio: float = 0.12,
    min_darkness: float = 0.45,
    darkness_margin: float = 0.18,
    max_wall_fraction: float = 0.65,
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """Heuristically detect blocked adjacencies (walls) on a square grid image.

    Returns undirected node-id pairs matching `build_graph_json(layout="grid")`
    (`"x,y"` ids), plus diagnostics.
    """

    width, height = image.size
    if rows <= 0 or cols <= 0 or width <= 0 or height <= 0:
        return [], {"warnings": ["Invalid dimensions for wall detection."]}

    gray = image.convert("L")
    cell_w = width / float(cols)
    cell_h = height / float(rows)
    base_cell = min(cell_w, cell_h)
    span = max(3, int(base_cell * sample_span_ratio))
    thickness = max(1, int(base_cell * sample_thickness_ratio))

    samples: List[float] = []
    candidates: List[Tuple[float, str, str]] = []

    def clamp_region(x0: int, y0: int, x1: int, y1: int) -> Optional[Tuple[int, int, int, int]]:
        cx0 = max(0, min(width, x0))
        cy0 = max(0, min(height, y0))
        cx1 = max(0, min(width, x1))
        cy1 = max(0, min(height, y1))
        if cx1 <= cx0 or cy1 <= cy0:
            return None
        return cx0, cy0, cx1, cy1

    def sample_darkness(region: Tuple[int, int, int, int]) -> float:
        stat = ImageStat.Stat(gray.crop(region))
        bright = float(stat.mean[0]) if stat.mean else 255.0
        return max(0.0, min(1.0, (255.0 - bright) / 255.0))

    # Vertical boundaries (between (x,y) and (x+1,y))
    for y in range(rows):
        y_mid = int(round((y + 0.5) * cell_h))
        for x in range(cols - 1):
            x_mid = int(round((x + 1) * cell_w))
            region = clamp_region(
                x_mid - thickness // 2,
                y_mid - span // 2,
                x_mid + (thickness + 1) // 2,
                y_mid + (span + 1) // 2,
            )
            if region is None:
                continue
            darkness = sample_darkness(region)
            u = f"{x},{y}"
            v = f"{x + 1},{y}"
            samples.append(darkness)
            candidates.append((darkness, u, v))

    # Horizontal boundaries (between (x,y) and (x,y+1))
    for y in range(rows - 1):
        y_mid = int(round((y + 1) * cell_h))
        for x in range(cols):
            x_mid = int(round((x + 0.5) * cell_w))
            region = clamp_region(
                x_mid - span // 2,
                y_mid - thickness // 2,
                x_mid + (span + 1) // 2,
                y_mid + (thickness + 1) // 2,
            )
            if region is None:
                continue
            darkness = sample_darkness(region)
            u = f"{x},{y}"
            v = f"{x},{y + 1}"
            samples.append(darkness)
            candidates.append((darkness, u, v))

    if not candidates:
        return [], {"warnings": ["No wall boundary samples were collected."]}

    sorted_dark = sorted(samples)
    median = sorted_dark[len(sorted_dark) // 2]
    threshold = max(min_darkness, median + darkness_margin)
    wall_edges = [(u, v) for darkness, u, v in candidates if darkness >= threshold]
    wall_edges = _dedupe_edge_pairs(wall_edges)

    warnings: List[str] = []
    if wall_edges and len(wall_edges) > int(len(candidates) * max_wall_fraction):
        warnings.append("Wall detection was too dense; wall edges were discarded.")
        wall_edges = []
    if not wall_edges:
        warnings.append("No wall edges exceeded confidence threshold.")

    info: Dict[str, Any] = {
        "samples": len(candidates),
        "detected_walls": len(wall_edges),
        "median_darkness": round(float(median), 4),
        "threshold_darkness": round(float(threshold), 4),
        "warnings": warnings,
    }
    return wall_edges, info


def _edge_subdivide(
    base_nodes: Dict[str, Tuple[float, float, float]],
    base_edges: List[Tuple[str, str]],
    *,
    detail: int,
    prefix: str,
) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    detail_n = max(1, int(detail))
    nodes_obj: Dict[str, Dict[str, Any]] = {
        node_id: {"pos": [float(pos[0]), float(pos[1]), float(pos[2])]} for node_id, pos in base_nodes.items()
    }
    edges: List[List[str]] = []
    for idx, (u, v) in enumerate(base_edges):
        prev = u
        ux, uy, uz = base_nodes[u]
        vx, vy, vz = base_nodes[v]
        for step in range(1, detail_n):
            t = float(step) / float(detail_n)
            mid = f"{prefix}:{idx}:{step}"
            nodes_obj[mid] = {
                "pos": [
                    float(ux + (vx - ux) * t),
                    float(uy + (vy - uy) * t),
                    float(uz + (vz - uz) * t),
                ]
            }
            edges.append([prev, mid])
            prev = mid
        edges.append([prev, v])
    return nodes_obj, edges


def _build_cube_topology(detail: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    base_nodes: Dict[str, Tuple[float, float, float]] = {
        "f0": (-1.1, 1.0, 0.0),
        "f1": (1.0, 1.0, 0.0),
        "f2": (1.0, -1.1, 0.0),
        "f3": (-1.1, -1.1, 0.0),
        "b0": (-0.25, 1.8, 0.0),
        "b1": (1.85, 1.8, 0.0),
        "b2": (1.85, -0.25, 0.0),
        "b3": (-0.25, -0.25, 0.0),
    }
    base_edges: List[Tuple[str, str]] = [
        ("f0", "f1"),
        ("f1", "f2"),
        ("f2", "f3"),
        ("f3", "f0"),
        ("b0", "b1"),
        ("b1", "b2"),
        ("b2", "b3"),
        ("b3", "b0"),
        ("f0", "b0"),
        ("f1", "b1"),
        ("f2", "b2"),
        ("f3", "b3"),
    ]
    return _edge_subdivide(base_nodes, base_edges, detail=max(1, detail), prefix="cube")


def _build_star_topology(detail: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    outer_r = 2.2
    inner_r = 0.95
    base_nodes: Dict[str, Tuple[float, float, float]] = {"c": (0.0, 0.0, 0.0)}
    for i in range(5):
        outer_theta = -math.pi / 2.0 + (2.0 * math.pi * i / 5.0)
        inner_theta = outer_theta + math.pi / 5.0
        base_nodes[f"o{i}"] = (outer_r * math.cos(outer_theta), outer_r * math.sin(outer_theta), 0.0)
        base_nodes[f"i{i}"] = (inner_r * math.cos(inner_theta), inner_r * math.sin(inner_theta), 0.0)

    base_edges: List[Tuple[str, str]] = []
    for i in range(5):
        base_edges.append((f"o{i}", f"i{i}"))
        base_edges.append((f"i{i}", f"o{(i + 1) % 5}"))
        base_edges.append((f"o{i}", f"o{(i + 2) % 5}"))
        base_edges.append(("c", f"i{i}"))

    return _edge_subdivide(base_nodes, base_edges, detail=max(1, detail), prefix="star")


def _build_figure8_topology(detail: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    n = max(6, int(detail))
    r = 1.15
    c_id = "c"
    base_nodes: Dict[str, Tuple[float, float, float]] = {c_id: (0.0, 0.0, 0.0)}

    left_nodes: List[str] = []
    for k in range(1, n):
        angle = 2.0 * math.pi * float(k) / float(n)
        nid = f"l{k}"
        base_nodes[nid] = (-1.5 + r * math.cos(angle), r * math.sin(angle), 0.0)
        left_nodes.append(nid)

    right_nodes: List[str] = []
    for k in range(0, n - 1):
        angle = 2.0 * math.pi * float(k) / float(n)
        nid = f"r{k}"
        base_nodes[nid] = (1.5 + r * math.cos(angle), r * math.sin(angle), 0.0)
        right_nodes.append(nid)

    base_edges: List[Tuple[str, str]] = []
    seq_left = [c_id] + left_nodes
    seq_right = [c_id] + right_nodes
    for idx in range(len(seq_left)):
        base_edges.append((seq_left[idx], seq_left[(idx + 1) % len(seq_left)]))
    for idx in range(len(seq_right)):
        base_edges.append((seq_right[idx], seq_right[(idx + 1) % len(seq_right)]))

    return _edge_subdivide(base_nodes, base_edges, detail=1, prefix="fig8")


def _default_terminals_from_nodes(nodes_obj: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    if len(nodes_obj) < 2:
        return {}
    ranked = sorted(
        (
            (node_id, float(node.get("pos", [0.0, 0.0, 0.0])[0]), float(node.get("pos", [0.0, 0.0, 0.0])[1]))
            for node_id, node in nodes_obj.items()
        ),
        key=lambda item: (item[1], item[2], item[0]),
    )
    left = ranked[0][0]
    right = ranked[-1][0]
    if left == right and len(ranked) >= 2:
        right = ranked[1][0]
    return {"A": [left, right]} if left != right else {}


def build_graph_json(
    *,
    layout: str,
    width: int,
    height: int,
    nodes: int,
    meta: Dict[str, str],
    edge_additions: Optional[List[Tuple[str, str]]] = None,
    edge_removals: Optional[List[Tuple[str, str]]] = None,
    warp_edges: Optional[List[Tuple[str, str]]] = None,
    wall_edges: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    space: Dict[str, Any] = {"type": "graph"}
    terminals: Dict[str, List[str]] = {}

    if layout == "line":
        node_ids = [str(i) for i in range(nodes)]
        space["nodes"] = {nid: {"pos": [float(i), 0.0, 0.0]} for i, nid in enumerate(node_ids)}
        space["edges"] = [[node_ids[i], node_ids[i + 1]] for i in range(nodes - 1)]
        if nodes >= 2:
            terminals = {"A": [node_ids[0], node_ids[-1]]}
    elif layout == "grid":
        node_ids: List[str] = []
        nodes_obj: Dict[str, Dict[str, Any]] = {}
        edges: List[List[str]] = []
        for y in range(height):
            for x in range(width):
                nid = f"{x},{y}"
                node_ids.append(nid)
                nodes_obj[nid] = {"pos": [float(x), float(-y), 0.0]}
                if x > 0:
                    edges.append([f"{x-1},{y}", nid])
                if y > 0:
                    edges.append([f"{x},{y-1}", nid])
        space["nodes"] = nodes_obj
        space["edges"] = edges
        if len(node_ids) >= 2:
            terminals = {"A": [node_ids[0], node_ids[-1]]}
    elif layout == "cube":
        detail = max(1, int(width) if width > 0 else int(nodes) if nodes > 0 else 2)
        nodes_obj, edges = _build_cube_topology(detail)
        space["nodes"] = nodes_obj
        space["edges"] = edges
        space["topology"] = "cube"
        terminals = _default_terminals_from_nodes(nodes_obj)
    elif layout == "star":
        detail = max(1, int(width) if width > 0 else int(nodes) if nodes > 0 else 2)
        nodes_obj, edges = _build_star_topology(detail)
        space["nodes"] = nodes_obj
        space["edges"] = edges
        space["topology"] = "star"
        terminals = _default_terminals_from_nodes(nodes_obj)
    elif layout == "figure8":
        detail = max(6, int(width) if width > 0 else int(nodes) if nodes > 0 else 8)
        nodes_obj, edges = _build_figure8_topology(detail)
        space["nodes"] = nodes_obj
        space["edges"] = edges
        space["topology"] = "figure8"
        terminals = _default_terminals_from_nodes(nodes_obj)
    else:
        raise ValueError(f"Unknown graph layout: {layout!r}")

    add_pairs = _dedupe_edge_pairs(
        (edge_additions or []) + (warp_edges or [])
    )
    remove_pairs = _dedupe_edge_pairs(
        (edge_removals or []) + (wall_edges or [])
    )
    if add_pairs or remove_pairs:
        overrides: Dict[str, Any] = {}
        if add_pairs:
            overrides["add"] = [[u, v] for u, v in add_pairs]
        if remove_pairs:
            overrides["remove"] = [[u, v] for u, v in remove_pairs]
        space["edge_overrides"] = overrides
    if warp_edges:
        space["warps"] = [[u, v] for u, v in _dedupe_edge_pairs(warp_edges)]
    if wall_edges:
        space["walls"] = [[u, v] for u, v in _dedupe_edge_pairs(wall_edges)]

    return {"space": space, "terminals": terminals, "meta": meta}
