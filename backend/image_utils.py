from __future__ import annotations

import io
from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from PIL import Image, ImageStat

from flow_solver.topologies import (
    build_cube_topology as build_registered_cube_topology,
    build_figure8_topology as build_registered_figure8_topology,
    build_radial_star_topology as build_registered_radial_star_topology,
)


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
    x_lines: Tuple[float, ...] = ()
    y_lines: Tuple[float, ...] = ()


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


def _accelerated_gray(
    image: Image.Image,
    *,
    cv2: Any,
    np: Any,
    rgb: Optional[Any] = None,
) -> Tuple[Any, str]:
    cached = getattr(image, "_flow_gray_u8", None)
    cached_backend = getattr(image, "_flow_gray_backend", None)
    if cached is not None and getattr(cached, "shape", None) == (image.height, image.width):
        return cached, str(cached_backend or "cpu-cache")
    if rgb is None:
        rgb = np.asarray(image.convert("RGB"))
    try:
        from .acceleration import accelerated_gray_u8

        gray, backend = accelerated_gray_u8(rgb, cv2=cv2, np=np)
    except Exception:
        gray, backend = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), "cpu"
    try:
        setattr(image, "_flow_gray_u8", gray)
        setattr(image, "_flow_gray_backend", backend)
    except Exception:
        pass
    return gray, backend


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
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 30, 110)

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
        x0, y0, ww, hh = cv2.boundingRect(contour)
        if ww <= 0 or hh <= 0:
            continue
        min_candidate_span = max(32.0, float(min(width, height)) * 0.18)
        if (
            area < img_area * 0.0004
            and (float(ww) < min_candidate_span or float(hh) < min_candidate_span)
        ):
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
        x0, y0, ww, hh = cv2.boundingRect(contour)
        if ww <= 0 or hh <= 0:
            continue
        min_candidate_span = max(32.0, float(min(width, height)) * 0.18)
        if (
            area < img_area * 0.0003
            and (float(ww) < min_candidate_span or float(hh) < min_candidate_span)
        ):
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


def _infer_regular_lattice(values: List[float], *, extent: int) -> List[float]:
    """Fill a small number of grid lines hidden by dots or thick barriers."""
    positions = sorted(float(value) for value in values)
    if len(positions) < 4 or extent <= 0:
        return positions
    min_gap = max(4.0, float(extent) / 48.0)
    gaps = [right - left for left, right in zip(positions, positions[1:])]
    useful_gaps = [gap for gap in gaps if gap >= min_gap]
    if len(useful_gaps) < 3:
        return positions

    ordered_gaps = sorted(useful_gaps)
    # Missing lines create 2x/3x gaps. The lower half therefore gives a more
    # stable estimate of one cell than the overall mean.
    base_sample = ordered_gaps[: max(2, (len(ordered_gaps) + 1) // 2)]
    base_gap = base_sample[len(base_sample) // 2]
    if base_gap <= 0.0:
        return positions
    steps = [max(1, int(round(gap / base_gap))) for gap in gaps]
    total_steps = sum(steps)
    inferred_count = total_steps + 1
    if inferred_count < len(positions) or inferred_count > 33:
        return positions
    if inferred_count - len(positions) > max(4, inferred_count // 3):
        return positions

    refined_gap = (positions[-1] - positions[0]) / float(max(1, total_steps))
    if refined_gap < min_gap:
        return positions
    errors = [
        abs((gap / refined_gap) - float(step))
        for gap, step in zip(gaps, steps)
    ]
    if errors and max(errors) > 0.22:
        return positions
    return [positions[0] + refined_gap * index for index in range(inferred_count)]


def _merge_thick_line_edges(values: List[float], *, extent: int) -> List[float]:
    """Merge the two Canny edges of thick grid strokes without merging cells."""

    positions = sorted(float(value) for value in values)
    if len(positions) < 3 or extent <= 0:
        return positions
    gaps = sorted(
        right - left
        for left, right in zip(positions, positions[1:])
        if right - left > 0.5
    )
    if len(gaps) < 2:
        return positions
    best_index = -1
    best_ratio = 0.0
    for index, (small, large) in enumerate(zip(gaps, gaps[1:])):
        if small > float(extent) * 0.035:
            continue
        ratio = large / max(0.5, small)
        if ratio >= 2.2 and ratio > best_ratio:
            best_index = index
            best_ratio = ratio
    if best_index < 0:
        return positions
    merge_tolerance = min(
        float(extent) * 0.035,
        (gaps[best_index] + gaps[best_index + 1]) * 0.5,
    )
    return _cluster_positions(positions, merge_tolerance)


def _regular_line_spacing(values: List[float]) -> Optional[float]:
    positions = sorted(float(value) for value in values)
    if len(positions) < 4:
        return None
    gaps = [right - left for left, right in zip(positions, positions[1:]) if right > left]
    if len(gaps) < 3:
        return None
    median_gap = sorted(gaps)[len(gaps) // 2]
    if median_gap <= 1.0:
        return None
    consistent = sum(
        1 for gap in gaps if abs(gap - median_gap) <= max(2.0, median_gap * 0.18)
    )
    return median_gap if consistent >= max(3, int(math.ceil(len(gaps) * 0.65))) else None


def _select_lattice_by_spacing(values: List[float], *, spacing: float) -> List[float]:
    """Select the strongest regular subset using cell pitch from the other axis."""

    positions = sorted(float(value) for value in values)
    if len(positions) < 3 or spacing <= 1.0:
        return positions
    tolerance = max(2.5, spacing * 0.13)
    best: List[float] = []
    best_error = float("inf")
    for origin in positions:
        by_step: Dict[int, Tuple[float, float]] = {}
        for value in positions:
            step = int(round((value - origin) / spacing))
            predicted = origin + float(step) * spacing
            error = abs(value - predicted)
            if error > tolerance:
                continue
            current = by_step.get(step)
            if current is None or error < current[1]:
                by_step[step] = (value, error)
        if len(by_step) < 3:
            continue
        steps = sorted(by_step)
        # A board lattice is contiguous; decorative lines may match isolated
        # multiples but must not create holes in the selected sequence.
        runs: List[List[int]] = [[steps[0]]]
        for step in steps[1:]:
            if step == runs[-1][-1] + 1:
                runs[-1].append(step)
            else:
                runs.append([step])
        run = max(runs, key=len)
        chosen = [by_step[step][0] for step in run]
        total_error = sum(by_step[step][1] for step in run)
        if len(chosen) > len(best) or (len(chosen) == len(best) and total_error < best_error):
            best = chosen
            best_error = total_error
    return sorted(best) if len(best) >= 3 else positions


def _detect_grid_hough_positions(
    image: Image.Image,
    *,
    line_threshold: float,
    max_dim: int = 800,
) -> Optional[Tuple[List[float], List[float], int, int]]:
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

    gray, _gray_backend = _accelerated_gray(img, cv2=cv2, np=np)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 20, 80)

    # Warps deliberately interrupt outer grid lines at their ports. Requiring
    # one line to span most of the board drops those borders and changes the
    # inferred dimensions, especially on small or downscaled screenshots.
    # A warp board's outside rows/columns are intentionally dashed and may
    # contain only short pieces of the lattice.  A permissive first pass is
    # safe here because the regular-lattice selection below rejects isolated
    # decorative strokes (including bridge glyphs).
    min_len_ratio = max(0.045, min(0.18, float(line_threshold) * 0.10))
    min_len = max(10, int(min(width, height) * min_len_ratio))
    max_gap = max(6, int(min(width, height) * 0.03))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=min_len, maxLineGap=max_gap)
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

    tol = max(8, int(min(width, height) * 0.0125))
    v_clusters = _cluster_positions(verticals, tol)
    h_clusters = _cluster_positions(horizontals, tol)

    v_clusters = _merge_thick_line_edges(v_clusters, extent=width)
    h_clusters = _merge_thick_line_edges(h_clusters, extent=height)

    vertical_spacing = _regular_line_spacing(v_clusters)
    horizontal_spacing = _regular_line_spacing(h_clusters)
    if vertical_spacing is not None:
        selected_horizontal = _select_lattice_by_spacing(
            h_clusters,
            spacing=vertical_spacing,
        )
        if len(selected_horizontal) >= 3:
            h_clusters = selected_horizontal
    if horizontal_spacing is not None:
        selected_vertical = _select_lattice_by_spacing(
            v_clusters,
            spacing=horizontal_spacing,
        )
        if len(selected_vertical) >= 3:
            v_clusters = selected_vertical

    if len(v_clusters) < 2 or len(h_clusters) < 2:
        return None

    v_clusters = _infer_regular_lattice(v_clusters, extent=width)
    h_clusters = _infer_regular_lattice(h_clusters, extent=height)
    v_gaps = [right - left for left, right in zip(v_clusters, v_clusters[1:])]
    h_gaps = [bottom - top for top, bottom in zip(h_clusters, h_clusters[1:])]
    if v_gaps and h_gaps:
        v_pitch = sorted(v_gaps)[len(v_gaps) // 2]
        h_pitch = sorted(h_gaps)[len(h_gaps) // 2]
        pitch_ratio = v_pitch / max(1e-6, h_pitch)
        # Square/bridge/warp cells remain approximately square under ordinary
        # screenshot scaling. Pointy hex boards expose horizontal/vertical
        # line fragments at the characteristic sqrt(3) pitch ratio; accepting
        # those fragments as a 3x23 rectangle was the main hex misroute.
        if pitch_ratio < 0.72 or pitch_ratio > 1.38:
            return None
    x_coverage = (v_clusters[-1] - v_clusters[0]) / float(max(1, width))
    y_coverage = (h_clusters[-1] - h_clusters[0]) / float(max(1, height))
    if min(x_coverage, y_coverage) / max(1e-6, max(x_coverage, y_coverage)) < 0.35:
        return None
    return v_clusters, h_clusters, width, height


def _detect_grid_hough(
    image: Image.Image,
    *,
    line_threshold: float,
    max_dim: int = 800,
) -> Optional[GridDetection]:
    detected = _detect_grid_hough_positions(
        image,
        line_threshold=line_threshold,
        max_dim=max_dim,
    )
    if detected is None:
        return None
    v_clusters, h_clusters, width, height = detected

    source_width, source_height = image.size
    x_scale = source_width / float(max(1, width))
    y_scale = source_height / float(max(1, height))
    x_lines = tuple(float(value) * x_scale for value in v_clusters)
    y_lines = tuple(float(value) * y_scale for value in h_clusters)
    return GridDetection(
        rows=len(h_clusters) - 1,
        cols=len(v_clusters) - 1,
        vertical_lines=len(v_clusters),
        horizontal_lines=len(h_clusters),
        width=width,
        height=height,
        x_lines=x_lines,
        y_lines=y_lines,
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

    def run_centers(flags: List[bool]) -> List[float]:
        centers: List[float] = []
        start: Optional[int] = None
        for index, flag in enumerate(flags + [False]):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                centers.append((start + index - 1) * 0.5)
                start = None
        return centers

    source_width, source_height = image.size
    x_scale = source_width / float(max(1, width))
    y_scale = source_height / float(max(1, height))
    x_lines = tuple(value * x_scale for value in run_centers(col_flags))
    y_lines = tuple(value * y_scale for value in run_centers(row_flags))
    return GridDetection(
        rows=horizontal_lines - 1,
        cols=vertical_lines - 1,
        vertical_lines=vertical_lines,
        horizontal_lines=horizontal_lines,
        width=width,
        height=height,
        x_lines=x_lines,
        y_lines=y_lines,
    )


def _grid_lines_for_dimensions(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
    line_threshold: float = 0.35,
) -> Tuple[List[float], List[float], str]:
    """Return image-coordinate lattice lines, falling back to the full frame."""

    width, height = image.size
    detected = _detect_grid_hough_positions(image, line_threshold=line_threshold)
    if detected is not None:
        raw_x, raw_y, detected_width, detected_height = detected
        if len(raw_x) == cols + 1 and len(raw_y) == rows + 1:
            x_scale = width / float(max(1, detected_width))
            y_scale = height / float(max(1, detected_height))
            return (
                [float(value) * x_scale for value in raw_x],
                [float(value) * y_scale for value in raw_y],
                "detected",
            )
    return (
        [width * index / float(max(1, cols)) for index in range(cols + 1)],
        [height * index / float(max(1, rows)) for index in range(rows + 1)],
        "frame-fallback",
    )


def _angle_distance_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _cluster_angles_deg(values: List[float], *, tol: float) -> List[float]:
    angles = sorted(float(value) % 360.0 for value in values)
    if not angles:
        return []
    clusters: List[List[float]] = [[angles[0]]]
    for angle in angles[1:]:
        if angle - clusters[-1][-1] <= tol:
            clusters[-1].append(angle)
        else:
            clusters.append([angle])
    if len(clusters) > 1 and (clusters[0][0] + 360.0) - clusters[-1][-1] <= tol:
        clusters[0] = [value - 360.0 for value in clusters[-1]] + clusters[0]
        clusters.pop()

    out: List[float] = []
    for cluster in clusters:
        sin_mean = sum(math.sin(math.radians(value)) for value in cluster)
        cos_mean = sum(math.cos(math.radians(value)) for value in cluster)
        out.append(math.degrees(math.atan2(sin_mean, cos_mean)) % 360.0)
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
    gray, gray_backend = _accelerated_gray(image, cv2=cv2, np=np, rgb=rgb)
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

    x_lines, y_lines, geometry_source = _grid_lines_for_dimensions(
        image,
        rows=rows,
        cols=cols,
    )
    board_left = max(0, int(round(x_lines[0])))
    board_top = max(0, int(round(y_lines[0])))
    board_right = min(width, int(round(x_lines[-1])))
    board_bottom = min(height, int(round(y_lines[-1])))
    if board_right > board_left and board_bottom > board_top:
        image = image.crop((board_left, board_top, board_right, board_bottom))
        width, height = image.size
    else:
        board_left = board_top = 0
        board_right, board_bottom = width, height
        geometry_source = "frame-fallback"

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
    # Dark game themes often tint empty cells and barrier-adjacent cells blue.
    # Their weak chroma used to pass the permissive default saturation cutoff
    # and create large fake terminal clusters. Real dots on these themes are
    # substantially more saturated, including their darker red/green colors.
    effective_sat_threshold = max(sat_threshold, 45.0 if bg_brightness < 90.0 else sat_threshold)
    neutral_brightness_min = max(brightness_min, bg_brightness + 25.0, 140.0)
    # Neutral endpoints include pure white dots. Keep the user-facing ceiling
    # for colorful pixels, but allow the full RGB range for high-contrast
    # achromatic terminals on a dark board.
    neutral_brightness_max = 255.0
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
            is_colorful = (
                sat >= effective_sat_threshold
                and brightness_min <= bright <= brightness_max
                and dist_bg >= bg_threshold
            )
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
        "effective_sat_threshold": round(float(effective_sat_threshold), 2),
        "sampling_geometry": {
            "source": geometry_source,
            "left": board_left,
            "top": board_top,
            "right": board_right,
            "bottom": board_bottom,
        },
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
    neutral_brightness_max = 255.0
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

    # Region-derived graphs already carry centers in the source image's pixel
    # coordinate system.  Re-normalizing those positions would shift samples
    # away from the actual cells, especially for irregular silhouettes.
    direct: Dict[str, Tuple[float, float]] = {}
    for node_id, node in nodes.items():
        data = node.get("data") if isinstance(node, dict) else None
        center = data.get("pixel_center") if isinstance(data, dict) else None
        if not isinstance(center, (list, tuple)) or len(center) < 2:
            direct = {}
            break
        try:
            px = float(center[0])
            py = float(center[1])
        except (TypeError, ValueError):
            direct = {}
            break
        if not (0.0 <= px < float(width) and 0.0 <= py < float(height)):
            direct = {}
            break
        direct[str(node_id)] = (px, py)
    if len(direct) == len(nodes) and len(direct) >= 2:
        return direct, {
            "mode": "pixel_center",
            "projected_nodes": len(direct),
        }

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
    neutral_brightness_max = 255.0
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
        if any(tok.startswith("warp") or tok in {"portal", "boundless"} for tok in hint_tokens):
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
        if len(grid.x_lines) >= 2 and len(grid.y_lines) >= 2:
            image_width, image_height = image.size
            x_coverage = (grid.x_lines[-1] - grid.x_lines[0]) / float(max(1, image_width))
            y_coverage = (grid.y_lines[-1] - grid.y_lines[0]) / float(max(1, image_height))
            signals["grid"]["coverage"] = {
                "x": round(x_coverage, 4),
                "y": round(y_coverage, 4),
            }
            # Decorative line fragments can form a tiny, internally regular
            # lattice inside a much larger free-form board.  Treating that as
            # the whole board produced 2x2/4x4 imports with missing terminals.
            if min(x_coverage, y_coverage) < 0.45:
                scores["square"] -= 0.24
                scores["graph"] += 0.42
                signals["recommended_graph_layout"] = "regions"
        scores["square"] += 0.40
        if abs(grid.vertical_lines - grid.horizontal_lines) <= 2:
            scores["square"] += 0.12
        if grid.rows > 0 and grid.cols > 0:
            ratio = float(grid.cols) / float(max(1, grid.rows))
            signals["grid_ratio"] = round(ratio, 3)
            if 0.8 <= ratio <= 1.25:
                scores["square"] += 0.06
        bridge_cells, bridge_info = detect_bridge_cells(image, rows=grid.rows, cols=grid.cols)
        signals["bridge_detection"] = bridge_info
        if bridge_cells:
            modifier_scores["bridges"] += 0.78
        warp_edges, warp_info = detect_warp_edges(image, rows=grid.rows, cols=grid.cols)
        signals["warp_detection"] = warp_info
        if warp_edges:
            modifier_scores["warps"] += 0.82
        wall_edges, wall_info = detect_wall_edges(image, rows=grid.rows, cols=grid.cols)
        signals["wall_detection"] = wall_info
        wall_fraction = len(wall_edges) / float(max(1, int(wall_info.get("samples", 0))))
        # Modifiers are independent: walls can appear in classic, bridge, and
        # warp puzzles.  The wall detector samples only internal adjacencies,
        # so a confidently detected wall set must not be discarded merely
        # because another mechanic was also found.
        if len(wall_edges) >= 3 and wall_fraction >= 0.015:
            modifier_scores["walls"] += 0.78
    else:
        warnings.append("Grid lines were not strongly detected.")

    cv2, np = _try_import_cv2()
    if cv2 is not None and np is not None:
        gray, gray_backend = _accelerated_gray(image, cv2=cv2, np=np)
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

                strong_template_hint = any(
                    token.startswith(("cube", "star", "figure8"))
                    or token in {"figure", "lemniscate", "infinity"}
                    for token in hint_tokens
                )
                strong_irregular_outline = (
                    radial_spread >= 0.20
                    and len(approx) >= 7
                    and extent <= 0.88
                )
                looks_like_freeform_cells = (
                    (
                        grid is None
                        or (
                            (
                                (
                                    modifier_scores["warps"] < 0.60
                                    and modifier_scores["bridges"] < 0.60
                                )
                                or strong_irregular_outline
                            )
                            and radial_spread >= 0.15
                            and extent <= 0.78
                        )
                        or strong_irregular_outline
                    )
                    and not strong_template_hint
                    and 0.04 <= area / float(max(1, h * w)) <= 0.92
                    and len(approx) >= 5
                    and extent <= 0.88
                    and not (
                        circularity >= 0.72
                        and radial_spread <= 0.14
                        and len(approx) <= 10
                    )
                )
                if looks_like_freeform_cells:
                    # When a screenshot contains enclosed cells inside a
                    # non-rectangular silhouette, deriving its region graph is
                    # safer than force-fitting a cube/star/figure-8 template.
                    scores["graph"] += 0.62
                    signals["recommended_graph_layout"] = "regions"

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

        # A successful concentric-grid fit is substantially stronger evidence
        # than raw line orientation. Radial spokes often look hexagonal to the
        # Hough scorer, which previously routed true ring boards to the hex
        # detector and failed before generation could begin.
        direct_circle_grid, direct_circle_info = detect_circle_grid(
            image,
            min_sectors=3,
            max_sectors=32,
        )
        if direct_circle_grid is not None:
            contour_circularity = float(direct_circle_info.get("contour_circularity", 0.0))
            signals["circle_grid"] = {
                "rings": int(direct_circle_grid.rings),
                "sectors": int(direct_circle_grid.sectors),
                "contour_circularity": round(contour_circularity, 4),
            }
            if direct_circle_grid.rings >= 2 and contour_circularity >= 0.84:
                scores["circle"] += 0.72
                scores["hex"] -= 0.08
                scores["square"] -= 0.05
    else:
        warnings.append("OpenCV is unavailable; classifier is using lightweight fallback signals.")

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    geometry = ordered[0][0]
    top_score = ordered[0][1]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    margin = max(0.0, top_score - second_score)

    if geometry == "hex" and grid is None:
        # The production Hexes boards are clipped/staggered rather than a
        # width*height parallelogram. Preserve their exact cell set through
        # region extraction, then regularize the six-neighbor adjacency.
        signals["recommended_graph_layout"] = "regions"

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
    if geometry != "square" and "walls" in selected_modifiers:
        selected_modifiers = tuple(mod for mod in selected_modifiers if mod != "walls")
        warnings.append("Wall modifier dropped because detected geometry is not a square grid.")
    if geometry == "circle" and "warps" in selected_modifiers:
        selected_modifiers = tuple(mod for mod in selected_modifiers if mod != "warps")
        warnings.append("Warp modifier dropped because a concentric circle grid was detected.")

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


def detect_bridge_cells(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
    center_ratio: float = 0.58,
    contrast_threshold: float = 38.0,
) -> Tuple[List[Tuple[int, int]], Dict[str, Any]]:
    """Detect both legacy cross and official double-arch bridge glyphs."""
    cv2, np = _try_import_cv2()
    if cv2 is None or np is None:
        return [], {"warnings": ["OpenCV is unavailable for bridge detection."]}
    width, height = image.size
    if rows <= 0 or cols <= 0 or width <= 0 or height <= 0:
        return [], {"warnings": ["Invalid dimensions for bridge detection."]}

    rgb = np.asarray(image.convert("RGB"))
    gray, gray_backend = _accelerated_gray(image, cv2=cv2, np=np, rgb=rgb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    x_lines, y_lines, geometry_source = _grid_lines_for_dimensions(
        image,
        rows=rows,
        cols=cols,
    )
    half_ratio = max(0.2, min(0.45, center_ratio * 0.5))
    detections: List[Tuple[int, int]] = []
    candidates: List[Dict[str, Any]] = []

    for row in range(rows):
        for col in range(cols):
            cell_w = x_lines[col + 1] - x_lines[col]
            cell_h = y_lines[row + 1] - y_lines[row]
            cx = (x_lines[col] + x_lines[col + 1]) * 0.5
            cy = (y_lines[row] + y_lines[row + 1]) * 0.5
            x0 = max(0, int(cx - cell_w * half_ratio))
            x1 = min(width, int(cx + cell_w * half_ratio))
            y0 = max(0, int(cy - cell_h * half_ratio))
            y1 = min(height, int(cy + cell_h * half_ratio))
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            region_gray = gray[y0:y1, x0:x1]
            region_hsv = hsv[y0:y1, x0:x1]
            background = float(np.median(region_gray))
            contrast = np.abs(region_gray.astype(np.float32) - background)
            # Bridge glyphs are normally white/gray. Excluding strongly
            # saturated pixels prevents colored terminal circles from looking
            # like crosses through their horizontal/vertical diameters.
            neutral = region_hsv[:, :, 1] <= 80
            bright_cutoff = max(125.0, background + contrast_threshold)
            mask = (contrast >= contrast_threshold) & neutral
            bright_mask = (region_gray.astype(np.float32) >= bright_cutoff) & neutral
            hh, ww = mask.shape
            band_y = max(1, int(round(hh * 0.10)))
            band_x = max(1, int(round(ww * 0.10)))
            mid_y = hh // 2
            mid_x = ww // 2
            horizontal = mask[max(0, mid_y - band_y) : min(hh, mid_y + band_y + 1), :]
            vertical = mask[:, max(0, mid_x - band_x) : min(ww, mid_x + band_x + 1)]
            horizontal_coverage = float(np.count_nonzero(np.any(horizontal, axis=0))) / float(max(1, ww))
            vertical_coverage = float(np.count_nonzero(np.any(vertical, axis=1))) / float(max(1, hh))
            fill_fraction = float(np.count_nonzero(mask)) / float(max(1, mask.size))
            is_cross = (
                horizontal_coverage >= 0.58
                and vertical_coverage >= 0.58
                and 0.025 <= fill_fraction <= 0.34
            )
            # The production Flow Free: Bridges artwork is not a plus.  It is
            # drawn as two horizontal rails with matching semicircular humps.
            # Detect the two separated, wide neutral strokes; the center crop
            # keeps ordinary grid boundaries out of this signature.
            row_coverage = np.mean(bright_mask, axis=1)
            active_rows = [bool(value >= 0.42) for value in row_coverage]
            stroke_runs: List[Tuple[int, int]] = []
            run_start: Optional[int] = None
            for index, active in enumerate(active_rows + [False]):
                if active and run_start is None:
                    run_start = index
                elif not active and run_start is not None:
                    stroke_runs.append((run_start, index - 1))
                    run_start = None
            stroke_centers = [0.5 * (start + end) for start, end in stroke_runs]
            has_upper = any(center <= hh * 0.46 for center in stroke_centers)
            has_lower = any(center >= hh * 0.54 for center in stroke_centers)
            separated = any(
                lower - upper >= hh * 0.18
                for upper in stroke_centers
                for lower in stroke_centers
                if lower > upper
            )
            bright_fill = float(np.count_nonzero(bright_mask)) / float(max(1, bright_mask.size))
            is_double_arch = (
                len(stroke_runs) >= 2
                and has_upper
                and has_lower
                and separated
                and 0.035 <= bright_fill <= 0.38
            )
            is_bridge = is_cross or is_double_arch
            if is_bridge:
                detections.append((row, col))
                candidates.append(
                    {
                        "row": row,
                        "col": col,
                        "horizontal_coverage": round(horizontal_coverage, 3),
                        "vertical_coverage": round(vertical_coverage, 3),
                        "fill_fraction": round(fill_fraction, 3),
                        "bright_fill_fraction": round(bright_fill, 3),
                        "stroke_runs": len(stroke_runs),
                        "glyph": "double-arch" if is_double_arch else "cross",
                    }
                )

    warnings = [] if detections else ["No bridge-cell markers were confidently detected."]
    return detections, {
        "detected_bridges": len(detections),
        "cells": candidates,
        "grid_geometry_source": geometry_source,
        "warnings": warnings,
    }


def detect_wall_edges(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
    sample_span_ratio: float = 0.55,
    sample_thickness_ratio: float = 0.12,
    contrast_margin: float = 0.16,
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
    detected_positions = _detect_grid_hough_positions(
        image,
        line_threshold=0.35,
    )
    if detected_positions is not None:
        raw_x, raw_y, detected_width, detected_height = detected_positions
    else:
        raw_x, raw_y, detected_width, detected_height = [], [], width, height
    if len(raw_x) == cols + 1:
        x_scale = width / float(max(1, detected_width))
        x_lines = [value * x_scale for value in raw_x]
    else:
        x_lines = [width * index / float(cols) for index in range(cols + 1)]
    if len(raw_y) == rows + 1:
        y_scale = height / float(max(1, detected_height))
        y_lines = [value * y_scale for value in raw_y]
    else:
        y_lines = [height * index / float(rows) for index in range(rows + 1)]

    cell_widths = [right - left for left, right in zip(x_lines, x_lines[1:])]
    cell_heights = [bottom - top for top, bottom in zip(y_lines, y_lines[1:])]
    cell_w = sorted(cell_widths)[len(cell_widths) // 2]
    cell_h = sorted(cell_heights)[len(cell_heights) // 2]
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

    def sample_brightness(region: Tuple[int, int, int, int]) -> float:
        stat = ImageStat.Stat(gray.crop(region))
        bright = float(stat.mean[0]) if stat.mean else 255.0
        return max(0.0, min(1.0, bright / 255.0))

    # Vertical boundaries (between (x,y) and (x+1,y))
    for y in range(rows):
        y_mid = int(round((y_lines[y] + y_lines[y + 1]) * 0.5))
        for x in range(cols - 1):
            x_mid = int(round(x_lines[x + 1]))
            region = clamp_region(
                x_mid - thickness // 2,
                y_mid - span // 2,
                x_mid + (thickness + 1) // 2,
                y_mid + (span + 1) // 2,
            )
            if region is None:
                continue
            brightness = sample_brightness(region)
            u = f"{x},{y}"
            v = f"{x + 1},{y}"
            samples.append(brightness)
            candidates.append((brightness, u, v))

    # Horizontal boundaries (between (x,y) and (x,y+1))
    for y in range(rows - 1):
        y_mid = int(round(y_lines[y + 1]))
        for x in range(cols):
            x_mid = int(round((x_lines[x] + x_lines[x + 1]) * 0.5))
            region = clamp_region(
                x_mid - span // 2,
                y_mid - thickness // 2,
                x_mid + (span + 1) // 2,
                y_mid + (thickness + 1) // 2,
            )
            if region is None:
                continue
            brightness = sample_brightness(region)
            u = f"{x},{y}"
            v = f"{x},{y + 1}"
            samples.append(brightness)
            candidates.append((brightness, u, v))

    if not candidates:
        return [], {"warnings": ["No wall boundary samples were collected."]}

    sorted_brightness = sorted(samples)
    median = sorted_brightness[len(sorted_brightness) // 2]
    bright_threshold = min(1.0, median + contrast_margin)
    dark_threshold = max(0.0, median - contrast_margin)
    bright_walls = [(u, v) for brightness, u, v in candidates if brightness >= bright_threshold]
    dark_walls = [(u, v) for brightness, u, v in candidates if brightness <= dark_threshold]
    # Choose one polarity per screenshot. Combining both tails makes gradients
    # and colored terminal bleed much more likely to create false walls.
    wall_edges = bright_walls if len(bright_walls) >= len(dark_walls) else dark_walls
    polarity = "bright" if wall_edges is bright_walls else "dark"
    wall_edges = _dedupe_edge_pairs(wall_edges)

    warnings: List[str] = []
    if wall_edges and len(wall_edges) > int(len(candidates) * max_wall_fraction):
        warnings.append("Wall detection was too dense; wall edges were discarded.")
        wall_edges = []
    if not wall_edges:
        warnings.append("No wall edges exceeded confidence threshold.")

    info: Dict[str, Any] = {
        "samples": len(candidates),
        "count": len(wall_edges),
        "detected_walls": len(wall_edges),
        "polarity": polarity,
        "median_brightness": round(float(median), 4),
        "bright_threshold": round(float(bright_threshold), 4),
        "dark_threshold": round(float(dark_threshold), 4),
        "grid_bounds": {
            "left": round(float(x_lines[0]), 2),
            "top": round(float(y_lines[0]), 2),
            "right": round(float(x_lines[-1]), 2),
            "bottom": round(float(y_lines[-1]), 2),
        },
        "warnings": warnings,
    }
    return wall_edges, info


def detect_warp_edges(
    image: Image.Image,
    *,
    rows: int,
    cols: int,
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """Detect paired Flow Warps ports as aligned gaps in opposite borders.

    Official Warps boards mark a usable wrap with a break in both opposing
    border segments, commonly accompanied by a short dotted guide extending
    out of the board.  Requiring an aligned pair avoids treating arbitrary
    missing/antialiased border pixels as a nonlocal connection.
    """

    cv2, np = _try_import_cv2()
    width, height = image.size
    if cv2 is None or np is None:
        return [], {"warnings": ["OpenCV is unavailable for warp-port detection."]}
    if rows <= 0 or cols <= 0 or width <= 0 or height <= 0:
        return [], {"warnings": ["Invalid dimensions for warp-port detection."]}

    x_lines, y_lines, geometry_source = _grid_lines_for_dimensions(
        image,
        rows=rows,
        cols=cols,
        line_threshold=0.35,
    )
    if geometry_source != "detected":
        return [], {"warnings": ["Grid bounds were not detected for warp-port inference."]}
    cell_widths = [right - left for left, right in zip(x_lines, x_lines[1:])]
    cell_heights = [bottom - top for top, bottom in zip(y_lines, y_lines[1:])]
    cell_w = sorted(cell_widths)[len(cell_widths) // 2]
    cell_h = sorted(cell_heights)[len(cell_heights) // 2]
    base_cell = max(2.0, min(cell_w, cell_h))

    rgb = np.asarray(image.convert("RGB"))
    gray, gray_backend = _accelerated_gray(image, cv2=cv2, np=np, rgb=rgb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    band = max(2, int(round(base_cell * 0.14)))
    segment_margin = 0.18

    image_median = float(np.median(gray))
    bright_cutoff = max(70.0, min(170.0, image_median + 55.0))
    border_paint = (gray >= bright_cutoff) & (hsv[:, :, 1] <= 200)

    def vertical_coverage(x: float, y0: float, y1: float) -> float:
        inset = max(1, int(round((y1 - y0) * segment_margin)))
        left = max(0, int(round(x)) - band)
        right = min(width, int(round(x)) + band + 1)
        top = max(0, int(round(y0)) + inset)
        bottom = min(height, int(round(y1)) - inset)
        if right <= left or bottom <= top:
            return 0.0
        region = border_paint[top:bottom, left:right]
        return float(np.count_nonzero(region)) / float(max(1, region.size))

    def horizontal_coverage(y: float, x0: float, x1: float) -> float:
        inset = max(1, int(round((x1 - x0) * segment_margin)))
        left = max(0, int(round(x0)) + inset)
        right = min(width, int(round(x1)) - inset)
        top = max(0, int(round(y)) - band)
        bottom = min(height, int(round(y)) + band + 1)
        if right <= left or bottom <= top:
            return 0.0
        region = border_paint[top:bottom, left:right]
        return float(np.count_nonzero(region)) / float(max(1, region.size))

    left_strengths = [vertical_coverage(x_lines[0], y_lines[row], y_lines[row + 1]) for row in range(rows)]
    right_strengths = [vertical_coverage(x_lines[-1], y_lines[row], y_lines[row + 1]) for row in range(rows)]
    top_strengths = [horizontal_coverage(y_lines[0], x_lines[col], x_lines[col + 1]) for col in range(cols)]
    bottom_strengths = [horizontal_coverage(y_lines[-1], x_lines[col], x_lines[col + 1]) for col in range(cols)]

    # Warps artwork reserves roughly one cell outside the detected board for
    # its shadow cells.  Requiring that room prevents a tightly cropped normal
    # grid with a faint border from being interpreted as fully toroidal.
    horizontal_margin = min(x_lines[0], width - x_lines[-1]) / max(1.0, cell_w)
    vertical_margin = min(y_lines[0], height - y_lines[-1]) / max(1.0, cell_h)

    # A fully Boundless board can have no intact outer-border segment at all.
    # Its stepped internal perimeter still contains the characteristic thick
    # stroke, so measure the strongest segment anywhere on the lattice.
    all_segment_strengths: List[float] = []
    for x in x_lines:
        all_segment_strengths.extend(
            vertical_coverage(x, y_lines[row], y_lines[row + 1]) for row in range(rows)
        )
    for y in y_lines:
        all_segment_strengths.extend(
            horizontal_coverage(y, x_lines[col], x_lines[col + 1]) for col in range(cols)
        )
    perimeter_signature = max(all_segment_strengths, default=0.0)
    has_warp_artwork = perimeter_signature >= 0.20
    gap_threshold = 0.14

    vertical_border_reference = max(left_strengths + right_strengths, default=0.0)
    horizontal_border_reference = max(top_strengths + bottom_strengths, default=0.0)
    horizontal_has_context = horizontal_margin >= 0.28 or vertical_border_reference >= 0.20
    vertical_has_context = vertical_margin >= 0.28 or horizontal_border_reference >= 0.20
    horizontal_rows = (
        [
            index
            for index, (left, right) in enumerate(zip(left_strengths, right_strengths))
            if left <= gap_threshold and right <= gap_threshold
        ]
        if has_warp_artwork and horizontal_has_context
        else []
    )
    vertical_cols = (
        [
            index
            for index, (top, bottom) in enumerate(zip(top_strengths, bottom_strengths))
            if top <= gap_threshold and bottom <= gap_threshold
        ]
        if has_warp_artwork and vertical_has_context
        else []
    )
    warp_edges: List[Tuple[str, str]] = []
    if cols > 2:
        warp_edges.extend((f"0,{row}", f"{cols - 1},{row}") for row in horizontal_rows)
    if rows > 2:
        warp_edges.extend((f"{col},0", f"{col},{rows - 1}") for col in vertical_cols)
    warp_edges = _dedupe_edge_pairs(warp_edges)

    warnings: List[str] = []
    if not warp_edges:
        warnings.append("No paired opposite-border warp ports were confidently detected.")
    return warp_edges, {
        "count": len(warp_edges),
        "mode": "paired-opposite-border-gaps",
        "horizontal_rows": horizontal_rows,
        "vertical_columns": vertical_cols,
        "border_reference": {
            "vertical": round(float(vertical_border_reference), 4),
            "horizontal": round(float(horizontal_border_reference), 4),
        },
        "bright_cutoff": round(float(bright_cutoff), 2),
        "perimeter_signature": round(float(perimeter_signature), 4),
        "outside_margin_cells": {
            "horizontal": round(float(horizontal_margin), 4),
            "vertical": round(float(vertical_margin), 4),
        },
        "coverage": {
            "left": [round(float(value), 4) for value in left_strengths],
            "right": [round(float(value), 4) for value in right_strengths],
            "top": [round(float(value), 4) for value in top_strengths],
            "bottom": [round(float(value), 4) for value in bottom_strengths],
        },
        "grid_bounds": {
            "left": round(x_lines[0], 2),
            "top": round(y_lines[0], 2),
            "right": round(x_lines[-1], 2),
            "bottom": round(y_lines[-1], 2),
        },
        "warnings": warnings,
    }


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


_SQRT3_OVER_2 = math.sqrt(3.0) / 2.0


def _axial_to_xy(q: float, r: float) -> Tuple[float, float]:
    return (q + 0.5 * r, -_SQRT3_OVER_2 * r)


def _point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            denom = (yj - yi)
            if abs(denom) < 1e-12:
                j = i
                continue
            x_cross = (xj - xi) * (y - yi) / denom + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _build_lattice_from_mask(
    *,
    prefix: str,
    q_extent: int,
    r_extent: int,
    mask: Any,
) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    q_lim = max(2, int(q_extent))
    r_lim = max(2, int(r_extent))
    ids: Dict[Tuple[int, int], str] = {}
    positions: Dict[Tuple[int, int], Tuple[float, float]] = {}
    nodes_obj: Dict[str, Dict[str, Any]] = {}

    for q in range(-q_lim, q_lim + 1):
        for r in range(-r_lim, r_lim + 1):
            x, y = _axial_to_xy(float(q), float(r))
            if not bool(mask(x, y)):
                continue
            node_id = f"{prefix}:{q},{r}"
            ids[(q, r)] = node_id
            positions[(q, r)] = (x, y)
            nodes_obj[node_id] = {"pos": [float(x), float(y), 0.0]}

    edges: List[List[str]] = []
    dirs = ((1, 0), (0, 1), (1, -1))
    for (q, r), node_id in ids.items():
        x0, y0 = positions[(q, r)]
        for dq, dr in dirs:
            nb = (q + dq, r + dr)
            nb_id = ids.get(nb)
            if nb_id is None:
                continue
            x1, y1 = positions[nb]
            mx = 0.5 * (x0 + x1)
            my = 0.5 * (y0 + y1)
            if bool(mask(mx, my)):
                edges.append([node_id, nb_id])

    return nodes_obj, edges


def _cube_face_center(
    tl: Tuple[float, float],
    tr: Tuple[float, float],
    bl: Tuple[float, float],
    *,
    row: int,
    col: int,
    size: int,
) -> Tuple[float, float]:
    s = max(1, int(size))
    u = (float(col) + 0.5) / float(s)
    v = (float(row) + 0.5) / float(s)
    x = tl[0] + (tr[0] - tl[0]) * u + (bl[0] - tl[0]) * v
    y = tl[1] + (tr[1] - tl[1]) * u + (bl[1] - tl[1]) * v
    return x, y


def _build_cube_topology(width: int, height: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    # Model cube boards as 3 visible square faces (top/left/right), each n x n cells.
    n = max(1, int(width), int(height))
    top_tl = (0.0, 2.0)
    top_tr = (2.0, 1.0)
    top_bl = (-2.0, 1.0)
    left_tl = (-2.0, 1.0)
    left_tr = (0.0, 0.0)
    left_bl = (-2.0, -1.0)
    right_tl = (0.0, 0.0)
    right_tr = (2.0, 1.0)
    right_bl = (0.0, -2.0)

    nodes_obj: Dict[str, Dict[str, Any]] = {}
    ids: Dict[Tuple[str, int, int], str] = {}

    def add_face(face: str, tl: Tuple[float, float], tr: Tuple[float, float], bl: Tuple[float, float]) -> None:
        for row in range(n):
            for col in range(n):
                node_id = f"{face}:{col},{row}"
                x, y = _cube_face_center(tl, tr, bl, row=row, col=col, size=n)
                nodes_obj[node_id] = {"pos": [float(x), float(y), 0.0]}
                ids[(face, col, row)] = node_id

    add_face("cube:t", top_tl, top_tr, top_bl)
    add_face("cube:l", left_tl, left_tr, left_bl)
    add_face("cube:r", right_tl, right_tr, right_bl)

    edge_set: Set[Tuple[str, str]] = set()

    def add_edge(a: str, b: str) -> None:
        if a == b:
            return
        edge = (a, b) if a < b else (b, a)
        edge_set.add(edge)

    for face in ("cube:t", "cube:l", "cube:r"):
        for row in range(n):
            for col in range(n):
                u = ids[(face, col, row)]
                if col + 1 < n:
                    add_edge(u, ids[(face, col + 1, row)])
                if row + 1 < n:
                    add_edge(u, ids[(face, col, row + 1)])

    # Shared edges between visible cube faces.
    for k in range(n):
        add_edge(ids[("cube:t", k, n - 1)], ids[("cube:l", k, 0)])
        add_edge(ids[("cube:t", n - 1, k)], ids[("cube:r", n - 1 - k, 0)])
        add_edge(ids[("cube:l", n - 1, k)], ids[("cube:r", 0, k)])

    edges = [[a, b] for a, b in sorted(edge_set)]
    return nodes_obj, edges


def _build_star_topology(width: int, height: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    w = max(1, int(width))
    h = max(1, int(height))
    outer_r = 1.35 + 1.1 * float(w) + 0.45 * float(h)
    inner_r = max(0.9, 0.52 * outer_r + 0.18 * float(h) - 0.25)
    if inner_r >= outer_r:
        inner_r = outer_r * 0.55

    polygon: List[Tuple[float, float]] = []
    for i in range(12):
        angle = (math.pi / 2.0) - (math.pi / 6.0) * float(i)
        radius = outer_r if (i % 2 == 0) else inner_r
        polygon.append((radius * math.cos(angle), radius * math.sin(angle)))

    def mask(x: float, y: float) -> bool:
        return _point_in_polygon(x, y, polygon)

    extent = int(math.ceil(outer_r * 2.0)) + 3
    return _build_lattice_from_mask(prefix="star", q_extent=extent, r_extent=extent, mask=mask)


def _build_figure8_topology(width: int, height: int) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]]]:
    w = max(1, int(width))
    h = max(1, int(height))
    outer_r = 0.85 + 0.5 * float(h) + 0.35 * float(w)
    inner_r = max(0.45, outer_r - (0.75 + 0.55 * float(w)))
    sep = max(0.95, inner_r + 0.22 * float(h) + 0.35)
    bridge_half_w = max(0.5, 0.35 * float(w) + 0.15 * float(h))
    bridge_half_h = max(0.6, 0.35 * float(h) + 0.2)

    def dist(x: float, y: float, cx: float, cy: float) -> float:
        return math.hypot(x - cx, y - cy)

    def mask(x: float, y: float) -> bool:
        top_outer = dist(x, y, 0.0, sep) <= outer_r
        bot_outer = dist(x, y, 0.0, -sep) <= outer_r
        bridge = abs(x) <= bridge_half_w and abs(y) <= bridge_half_h
        if not (top_outer or bot_outer or bridge):
            return False
        in_hole = (dist(x, y, 0.0, sep) < inner_r) or (dist(x, y, 0.0, -sep) < inner_r)
        if bridge:
            return True
        return not in_hole

    extent_q = int(math.ceil(outer_r + sep + 2.5)) + 3
    extent_r = int(math.ceil((outer_r + sep) / _SQRT3_OVER_2)) + 3
    return _build_lattice_from_mask(prefix="fig8", q_extent=extent_q, r_extent=extent_r, mask=mask)


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


def detect_region_topology(
    image: Image.Image,
    *,
    min_area_ratio: float = 0.0008,
    max_area_ratio: float = 0.35,
    adjacency_gap: Optional[int] = None,
    max_regions: int = 500,
    prefer_hex: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], List[List[str]], Dict[str, Any]]:
    """Extract an arbitrary Shapes board as a region-adjacency graph.

    Bright/colored board lines are treated as barriers. Enclosed dark regions
    become physical cells, and sufficiently long shared barriers become graph
    adjacencies. This is a topology fallback for silhouettes/tracks that do not
    match a parametric template; it intentionally derives cells rather than
    sampling a six-neighbor lattice inside the outline.
    """

    try:
        import cv2  # type: ignore
        import numpy as np
    except Exception as exc:  # pragma: no cover - dependencies are required by the app
        return {}, [], {"warnings": [f"Region topology detection requires OpenCV/numpy: {exc}"]}

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width < 16 or height < 16:
        return {}, [], {"warnings": ["Image is too small for region topology detection."]}

    gray, gray_backend = _accelerated_gray(image, cv2=cv2, np=np, rgb=rgb)
    value = rgb.max(axis=2).astype(np.uint8)
    saturation = (rgb.max(axis=2) - rgb.min(axis=2)).astype(np.uint8)

    # Otsu catches white/bright boundaries; the chroma branch preserves dim
    # colored grid lines against black/glowing game backgrounds.
    _threshold, bright_mask = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    colored_mask = np.where((saturation >= 28) & (value >= 42), 255, 0).astype(np.uint8)
    barrier = cv2.bitwise_or(bright_mask, colored_mask)

    # Endpoint dots are compact filled blobs inside cells, not barriers. At
    # low resolutions they can consume most of a cell and fragment the region
    # graph, so remove only isolated round/compact components before closing
    # the actual line network. This is scale-relative and also covers white or
    # gray endpoints that enter through the brightness mask.
    component_count, component_labels, component_stats, _component_centers = cv2.connectedComponentsWithStats(
        barrier,
        connectivity=8,
    )
    removed_terminal_blobs = 0
    min_span = float(min(width, height))
    for label in range(1, component_count):
        x = int(component_stats[label, cv2.CC_STAT_LEFT])
        y = int(component_stats[label, cv2.CC_STAT_TOP])
        component_width = int(component_stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(component_stats[label, cv2.CC_STAT_HEIGHT])
        area = float(component_stats[label, cv2.CC_STAT_AREA])
        if component_width <= 0 or component_height <= 0:
            continue
        touches_frame = (
            x <= 0
            or y <= 0
            or x + component_width >= width
            or y + component_height >= height
        )
        short_side = float(min(component_width, component_height))
        long_side = float(max(component_width, component_height))
        fill_fraction = area / float(component_width * component_height)
        if (
            not touches_frame
            and short_side >= max(4.0, min_span * 0.015)
            and long_side <= max(14.0, min_span * 0.16)
            and long_side / max(1.0, short_side) <= 1.65
            and fill_fraction >= 0.42
        ):
            barrier[component_labels == label] = 0
            removed_terminal_blobs += 1

    close_size = max(3, int(round(min(width, height) / 320.0)) | 1)
    barrier = cv2.morphologyEx(
        barrier,
        cv2.MORPH_CLOSE,
        np.ones((close_size, close_size), dtype=np.uint8),
        iterations=1,
    )
    # A one-pixel expansion closes antialiased gaps without erasing thin cells.
    barrier = cv2.dilate(barrier, np.ones((3, 3), dtype=np.uint8), iterations=1)
    passable = cv2.bitwise_not(barrier)
    label_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        passable,
        connectivity=4,
    )

    border_labels = set(int(value_) for value_ in labels[0, :])
    border_labels.update(int(value_) for value_ in labels[-1, :])
    border_labels.update(int(value_) for value_ in labels[:, 0])
    border_labels.update(int(value_) for value_ in labels[:, -1])
    image_area = float(width * height)
    min_area = max(12.0, image_area * max(0.0, min_area_ratio))
    max_area = image_area * max(0.01, min(0.95, max_area_ratio))

    kept_labels = [
        label
        for label in range(1, label_count)
        if label not in border_labels
        and min_area <= float(stats[label, cv2.CC_STAT_AREA]) <= max_area
    ]
    warnings: List[str] = []
    if not kept_labels:
        return {}, [], {
            "warnings": ["No enclosed cell regions were detected."],
            "barrier_fraction": round(float(np.count_nonzero(barrier)) / image_area, 4),
        }
    if len(kept_labels) > max_regions:
        return {}, [], {
            "warnings": [
                f"Detected {len(kept_labels)} regions, exceeding the safety limit of {max_regions}."
            ]
        }

    label_to_id = {
        label: f"region:{index:03d}" for index, label in enumerate(kept_labels)
    }
    nodes_obj: Dict[str, Dict[str, Any]] = {}
    masks: Dict[int, Any] = {}
    for label in kept_labels:
        node_id = label_to_id[label]
        mask = np.where(labels == label, 255, 0).astype(np.uint8)
        masks[label] = mask
        contours, _hierarchy = cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        polygon: List[List[float]] = []
        if contours:
            contour = max(contours, key=cv2.contourArea)
            epsilon = max(1.0, 0.01 * cv2.arcLength(contour, True))
            approximation = cv2.approxPolyDP(contour, epsilon, True)
            polygon = [
                [round(float(point[0][0]), 2), round(float(point[0][1]), 2)]
                for point in approximation
            ]
        # Filled endpoint circles can remove an asymmetric bite from the
        # passable component and noticeably pull its area centroid away from
        # the real cell center.  The component bounding-box center remains
        # stable for regular hex cells and improves both terminal projection
        # and geometric adjacency reconstruction.
        cx = float(stats[label, cv2.CC_STAT_LEFT]) + (float(stats[label, cv2.CC_STAT_WIDTH]) - 1.0) * 0.5
        cy = float(stats[label, cv2.CC_STAT_TOP]) + (float(stats[label, cv2.CC_STAT_HEIGHT]) - 1.0) * 0.5
        nodes_obj[node_id] = {
            "pos": [float(cx), float(-cy), 0.0],
            "data": {
                "pixel_center": [round(float(cx), 2), round(float(cy), 2)],
                "pixel_area": int(stats[label, cv2.CC_STAT_AREA]),
                "polygon": polygon,
                "region_label": int(label),
            },
        }

    preferred_hex_edges: Optional[Set[Tuple[str, str]]] = None
    if prefer_hex and len(nodes_obj) >= 4:
        centers = {
            node_id: (
                float(node["data"]["pixel_center"][0]),
                float(node["data"]["pixel_center"][1]),
            )
            for node_id, node in nodes_obj.items()
        }
        nearest_distances = [
            min(
                math.hypot(point[0] - other[0], point[1] - other[1])
                for other_id, other in centers.items()
                if other_id != node_id
            )
            for node_id, point in centers.items()
        ]
        pitch = float(np.median(np.asarray(nearest_distances, dtype=np.float32)))
        if pitch > 1.0:
            candidate_hex_edges: Set[Tuple[str, str]] = set()
            center_items = sorted(centers.items())
            for index, (left_id, left_center) in enumerate(center_items):
                for right_id, right_center in center_items[index + 1 :]:
                    if math.hypot(
                        left_center[0] - right_center[0],
                        left_center[1] - right_center[1],
                    ) <= pitch * 1.22:
                        candidate_hex_edges.add((left_id, right_id))
            candidate_degrees = {node_id: 0 for node_id in nodes_obj}
            for left_id, right_id in candidate_hex_edges:
                candidate_degrees[left_id] += 1
                candidate_degrees[right_id] += 1
            if candidate_hex_edges and max(candidate_degrees.values(), default=0) <= 6:
                preferred_hex_edges = candidate_hex_edges

    if adjacency_gap is None:
        # The regions are separated by the *processed* barrier, whose width is
        # the source stroke plus the close/dilate margin above.  A scale-only
        # gap misses ordinary 2--5 px grid strokes (especially in small
        # screenshots), while an oversized fixed gap turns diagonal corner
        # contacts into false edges.  The 80th percentile of the barrier's
        # distance transform estimates a typical half-stroke while ignoring
        # most intersections and terminal dots.  Expanding a region by roughly
        # twice that radius reaches the region on the other side of the stroke.
        barrier_distance = cv2.distanceTransform(barrier, cv2.DIST_L2, 5)
        barrier_radii = barrier_distance[barrier_distance > 0]
        typical_radius = (
            float(np.percentile(barrier_radii, 80.0))
            if barrier_radii.size
            else 1.0
        )
        # Connected-component masks begin one pixel beyond each side of the
        # barrier, so the reach also needs the two boundary pixels in addition
        # to the estimated full stroke width.
        max_gap = min(64, max(4, int(math.ceil(typical_radius * 2.0)) + 2))
    else:
        typical_radius = 0.0
        max_gap = max(1, int(adjacency_gap))
    kept_set = set(kept_labels)
    def edges_for_gap(candidate_gap: int) -> Set[Tuple[str, str]]:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (candidate_gap * 2 + 1, candidate_gap * 2 + 1),
        )
        candidate_edges: Set[Tuple[str, str]] = set()
        for label in kept_labels:
            expanded = cv2.dilate(masks[label], kernel, iterations=1)
            contact_labels = labels[expanded > 0]
            counts = np.bincount(contact_labels.ravel(), minlength=label_count)
            source_area = float(stats[label, cv2.CC_STAT_AREA])
            for other in kept_set:
                if other <= label:
                    continue
                # Corner contacts generate only a few overlap pixels; a shared
                # cell boundary scales with the smaller region's linear size.
                minimum_contact = max(
                    3,
                    int(0.35 * math.sqrt(min(source_area, float(stats[other, cv2.CC_STAT_AREA])))),
                )
                if int(counts[other]) >= minimum_contact:
                    candidate_edges.add((label_to_id[label], label_to_id[other]))
        return candidate_edges

    def graph_components(candidate_edges: Set[Tuple[str, str]]) -> List[Set[str]]:
        neighbors: Dict[str, Set[str]] = {node_id: set() for node_id in nodes_obj}
        for left, right in candidate_edges:
            neighbors[left].add(right)
            neighbors[right].add(left)
        remaining = set(nodes_obj)
        components: List[Set[str]] = []
        while remaining:
            start = remaining.pop()
            component = {start}
            pending = [start]
            while pending:
                current = pending.pop()
                for neighbor in neighbors[current]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
            components.append(component)
        return sorted(components, key=len, reverse=True)

    search_gaps = [max_gap] if adjacency_gap is not None else list(range(2, max_gap + 1))
    edge_set: Set[Tuple[str, str]] = set(preferred_hex_edges or set())
    gap = search_gaps[-1]
    search_attempts: List[Dict[str, Any]] = []
    selected_nodes = set(nodes_obj)
    previous_viable: Optional[Tuple[int, Set[Tuple[str, str]], Set[str]]] = None
    if preferred_hex_edges is not None:
        components = graph_components(preferred_hex_edges)
        selected_nodes = components[0] if components else set(nodes_obj)
        search_attempts.append(
            {
                "gap": 0,
                "edges": len(preferred_hex_edges),
                "dominant_regions": len(selected_nodes),
                "components": len(components),
                "max_degree": max(
                    (
                        sum(1 for edge in preferred_hex_edges if node_id in edge)
                        for node_id in nodes_obj
                    ),
                    default=0,
                ),
                "viable": len(selected_nodes) == len(nodes_obj),
                "mode": "regular-hex-centers",
            }
        )
        gap = 0
    else:
        for candidate_gap in search_gaps:
            candidate_edges = edges_for_gap(candidate_gap)
            components = graph_components(candidate_edges)
            dominant = components[0] if components else set()
            dominant_edges = {
                edge for edge in candidate_edges if edge[0] in dominant and edge[1] in dominant
            }
            candidate_degrees = {node_id: 0 for node_id in nodes_obj}
            for left, right in dominant_edges:
                candidate_degrees[left] += 1
                candidate_degrees[right] += 1
            max_candidate_degree = max(
                (candidate_degrees[node_id] for node_id in dominant),
                default=0,
            )
            dominant_ratio = len(dominant) / float(max(1, len(nodes_obj)))
            viable = dominant_ratio >= 0.85 and max_candidate_degree <= 6
            search_attempts.append(
                {
                    "gap": candidate_gap,
                    "edges": len(candidate_edges),
                    "dominant_regions": len(dominant),
                    "components": len(components),
                    "max_degree": max_candidate_degree,
                    "viable": viable,
                }
            )
            edge_set = candidate_edges
            gap = candidate_gap
            if viable:
                if previous_viable is not None and dominant_edges == previous_viable[1]:
                    # Select the first gap of a stable topology plateau. A one-pixel
                    # early gap can leave antialiased shared sides disconnected;
                    # later growth eventually creates diagonal corner contacts.
                    gap, edge_set, selected_nodes = previous_viable
                    break
                previous_viable = (candidate_gap, dominant_edges, dominant)
            else:
                previous_viable = None
        else:
            if previous_viable is not None:
                gap, edge_set, selected_nodes = previous_viable

    dropped_regions = sorted(set(nodes_obj) - selected_nodes)
    if dropped_regions:
        for node_id in dropped_regions:
            nodes_obj.pop(node_id, None)
        edge_set = {
            edge for edge in edge_set if edge[0] in selected_nodes and edge[1] in selected_nodes
        }

    geometric_hex_repair = False
    if prefer_hex and len(nodes_obj) >= 4:
        centers = {
            node_id: (
                float(node["data"]["pixel_center"][0]),
                float(node["data"]["pixel_center"][1]),
            )
            for node_id, node in nodes_obj.items()
        }
        nearest_distances = [
            min(
                math.hypot(point[0] - other[0], point[1] - other[1])
                for other_id, other in centers.items()
                if other_id != node_id
            )
            for node_id, point in centers.items()
        ]
        pitch = float(np.median(np.asarray(nearest_distances, dtype=np.float32)))
        if pitch > 1.0:
            geometric_edges: Set[Tuple[str, str]] = set()
            center_items = sorted(centers.items())
            for index, (left_id, left_center) in enumerate(center_items):
                for right_id, right_center in center_items[index + 1 :]:
                    distance = math.hypot(
                        left_center[0] - right_center[0],
                        left_center[1] - right_center[1],
                    )
                    if distance <= pitch * 1.22:
                        geometric_edges.add((left_id, right_id))
            geometric_degrees = {node_id: 0 for node_id in nodes_obj}
            for left_id, right_id in geometric_edges:
                geometric_degrees[left_id] += 1
                geometric_degrees[right_id] += 1
            components = graph_components(geometric_edges)
            if (
                geometric_edges
                and max(geometric_degrees.values(), default=0) <= 6
                and components
                and len(components[0]) == len(nodes_obj)
            ):
                geometric_hex_repair = geometric_edges != edge_set
                edge_set = geometric_edges

    edges = [[u, v] for u, v in sorted(edge_set)]
    if not edges:
        warnings.append("Cell regions were found, but no shared-boundary adjacencies were detected.")

    degrees = {node_id: 0 for node_id in nodes_obj}
    for u, v in edges:
        degrees[u] += 1
        degrees[v] += 1
    suspicious = [node_id for node_id, degree in degrees.items() if degree > 4]
    if suspicious:
        warnings.append(
            f"{len(suspicious)} detected regions have degree greater than four; review corner contacts."
        )

    info = {
        "regions": len(nodes_obj),
        "edges": len(edges),
        "barrier_fraction": round(float(np.count_nonzero(barrier)) / image_area, 4),
        "adjacency_gap": gap,
        "estimated_barrier_radius": round(typical_radius, 3),
        "removed_terminal_blobs": removed_terminal_blobs,
        "adjacency_search": search_attempts,
        "dropped_enclosed_regions": dropped_regions,
        "max_degree": max(degrees.values(), default=0),
        "geometric_hex_repair": geometric_hex_repair,
        "warnings": warnings,
    }
    return nodes_obj, edges, info


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
    star_faces: int = 5,
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
        detail_w = max(1, int(width) if width > 0 else int(nodes) if nodes > 0 else 2)
        detail_h = max(1, int(height) if height > 0 else detail_w)
        topology = build_registered_cube_topology(size=max(detail_w, detail_h))
        nodes_obj = {
            node.id: {
                "pos": [float(node.pos[0]), float(node.pos[1]), float(node.pos[2])],
                **({"data": dict(node.data)} if node.data else {}),
            }
            for node in topology.nodes
        }
        edges = [[u, v] for u, v in topology.edges]
        space["nodes"] = nodes_obj
        space["edges"] = edges
        space["topology"] = "cube"
        terminals = _default_terminals_from_nodes(nodes_obj)
    elif layout == "star":
        detail_w = max(1, int(width) if width > 0 else int(nodes) if nodes > 0 else 2)
        detail_h = max(1, int(height) if height > 0 else detail_w)
        topology = build_registered_radial_star_topology(
            size=max(detail_w, detail_h),
            faces=int(star_faces),
        )
        nodes_obj = {
            node.id: {
                "pos": [float(node.pos[0]), float(node.pos[1]), float(node.pos[2])],
                **({"data": dict(node.data)} if node.data else {}),
            }
            for node in topology.nodes
        }
        edges = [[u, v] for u, v in topology.edges]
        space["nodes"] = nodes_obj
        space["edges"] = edges
        space["topology"] = "star"
        terminals = _default_terminals_from_nodes(nodes_obj)
    elif layout == "figure8":
        topology = build_registered_figure8_topology()
        nodes_obj = {
            node.id: {
                "pos": [float(node.pos[0]), float(node.pos[1]), float(node.pos[2])],
                **({"data": dict(node.data)} if node.data else {}),
            }
            for node in topology.nodes
        }
        edges = [[u, v] for u, v in topology.edges]
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
