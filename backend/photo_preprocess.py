from __future__ import annotations

import copy
import hashlib
import io
import math
import os
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageOps

try:  # Optional decoder for iPhone HEIC/HEIF camera originals.
    from pillow_heif import register_heif_opener  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    HEIF_DECODER_AVAILABLE = False
else:  # pragma: no cover - depends on the optional package
    register_heif_opener()
    HEIF_DECODER_AVAILABLE = True


PHOTO_PREPROCESSING_VERSION = "camera-v3"
MAX_CAMERA_DECODE_PIXELS = 80_000_000
# Quality metrics are measured after resampling the prepared board to this
# short side so thresholds do not drift with camera resolution.
QUALITY_CANONICAL_SHORT_SIDE = 720
MIN_QUAD_EDGE_SUPPORT = 0.05
# Automatic display outlines closer than this in score are ambiguous and are
# routed to review so the user confirms the corners.
AMBIGUOUS_DISPLAY_MARGIN = 0.05
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


class UnsupportedPhotoFormat(ValueError):
    """The upload is a camera format this server cannot decode."""


def _looks_like_heif(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


@dataclass(frozen=True)
class PhotoQuadCandidate:
    corners: Tuple[Tuple[float, float], ...]
    score: float
    source: str
    metrics: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "corners": [
                {"x": round(float(x), 3), "y": round(float(y), 3)}
                for x, y in self.corners
            ],
            "score": round(float(self.score), 5),
            "source": self.source,
            "metrics": {
                key: round(float(value), 5)
                for key, value in sorted(self.metrics.items())
            },
        }


@dataclass
class PreparedPhoto:
    color_image: Image.Image
    geometry_image: Image.Image
    glare_mask: Image.Image
    info: dict[str, Any]
    # Display-rectified color image before the board correction; OCR reads the
    # level header from this view.
    display_image: Optional[Image.Image] = None
    # Board plus half a cell of surrounding pixels, for warp detection.
    board_context_image: Optional[Image.Image] = None


def load_camera_image(data: bytes) -> tuple[Image.Image, dict[str, Any]]:
    """Decode a camera image while honoring orientation without touching screenshot decode."""

    if _looks_like_heif(data) and not HEIF_DECODER_AVAILABLE:
        raise UnsupportedPhotoFormat(
            "HEIC/HEIF photos need the optional pillow-heif package on the server; "
            "export the photo as JPEG or install pillow-heif"
        )
    with Image.open(io.BytesIO(data)) as opened:
        source_format = str(opened.format or "unknown").lower()
        original_size = tuple(int(value) for value in opened.size)
        if original_size[0] <= 0 or original_size[1] <= 0:
            raise ValueError("camera photo has invalid dimensions")
        if original_size[0] * original_size[1] > MAX_CAMERA_DECODE_PIXELS:
            raise ValueError(
                f"camera photo exceeds the {MAX_CAMERA_DECODE_PIXELS:,}-pixel decode limit"
            )
        exif = opened.getexif()
        orientation = int(exif.get(274, 1) or 1)
        camera_tags = {
            "make": exif.get(271),
            "model": exif.get(272),
            "captured_at": exif.get(36867) or exif.get(306),
            "focal_length": exif.get(37386),
        }
        camera_metadata_fields = sorted(
            key
            for key, value in camera_tags.items()
            if value is not None and str(value).strip()
        )
        likelihood = 0.0
        if source_format in {"jpeg", "jpg", "heif", "heic"}:
            likelihood += 0.22
        if camera_metadata_fields:
            likelihood += 0.58
        if orientation not in {0, 1}:
            likelihood += 0.08
        oriented = ImageOps.exif_transpose(opened)
        image = oriented.convert("RGB")
    # Pseudonymous camera model id, so real-photo corpora can be split into
    # tuning and held-out sets by device after the metadata itself is stripped.
    device_label = "|".join(str(camera_tags.get(key) or "").strip() for key in ("make", "model"))
    device_id = (
        hashlib.sha256(device_label.encode("utf-8")).hexdigest()[:12]
        if device_label.strip("|")
        else None
    )
    return image, {
        "device_id": device_id,
        "format": source_format,
        "original_size": {"width": original_size[0], "height": original_size[1]},
        "oriented_size": {"width": image.width, "height": image.height},
        "exif_orientation": orientation,
        "orientation_applied": orientation not in {0, 1},
        "camera_metadata_fields": camera_metadata_fields,
        "camera_metadata_present": bool(camera_metadata_fields),
        "camera_likelihood": round(min(1.0, likelihood), 4),
    }


@dataclass(frozen=True)
class SanitizedPhoto:
    data: bytes
    # Replacement file suffix when the photo had to be re-encoded.
    suffix: Optional[str]
    method: str


# JFIF, ICC colour profile and Adobe colour-transform segments are needed to
# decode identical pixels; every other APPn/COM segment is metadata.
_JPEG_DECODE_APP_MARKERS = {0xE0, 0xE2, 0xEE}
_PNG_METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _orientation_only_exif(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[274] = int(orientation)
    return exif.tobytes()  # "Exif\0\0" + TIFF payload


def _strip_jpeg_metadata(data: bytes, orientation: int) -> Optional[bytes]:
    if data[:2] != b"\xff\xd8":
        return None
    segments: list[bytes] = []
    index = 2
    length_total = len(data)
    while index + 1 < length_total:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker == 0xD9:
            # Drop anything appended after the primary image (MPF companion
            # frames can carry their own EXIF blocks).
            segments.append(b"\xff\xd9")
            header = [segment for segment in segments[:1] if segment[1] == 0xE0]
            body = segments[len(header):]
            exif = b""
            if orientation not in {0, 1}:
                payload = _orientation_only_exif(orientation)
                exif = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
            return b"\xff\xd8" + b"".join(header) + exif + b"".join(body)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            segments.append(data[index : index + 2])
            index += 2
            continue
        if index + 4 > length_total:
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        end = index + 2 + length
        if length < 2 or end > length_total:
            return None
        if marker == 0xDA:
            # Entropy-coded data runs until the next marker that is neither a
            # stuffed 0xFF00 nor a restart marker.
            scan = end
            while True:
                position = data.find(b"\xff", scan)
                if position < 0 or position + 1 >= length_total:
                    return None
                following = data[position + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    scan = position + 2
                    continue
                if following == 0xFF:
                    scan = position + 1
                    continue
                break
            segments.append(data[index:position])
            index = position
            continue
        segment = data[index:end]
        is_metadata = marker == 0xFE or (0xE1 <= marker <= 0xEF and marker not in _JPEG_DECODE_APP_MARKERS)
        if marker == 0xE2 and not segment[4:16].startswith(b"ICC_PROFILE\x00"):
            is_metadata = True  # MPF and other APP2 payloads
        if not is_metadata:
            segments.append(segment)
        index = end
    return None


def _strip_png_metadata(data: bytes, orientation: int) -> Optional[bytes]:
    import zlib

    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return None
    chunks: list[bytes] = []
    index = len(signature)
    inserted_exif = orientation in {0, 1}
    while index + 12 <= len(data):
        length = struct.unpack(">I", data[index : index + 4])[0]
        kind = data[index + 4 : index + 8]
        end = index + 12 + length
        if end > len(data):
            return None
        if kind == b"IDAT" and not inserted_exif:
            payload = _orientation_only_exif(orientation)[6:]
            crc = zlib.crc32(b"eXIf" + payload) & 0xFFFFFFFF
            chunks.append(struct.pack(">I", len(payload)) + b"eXIf" + payload + struct.pack(">I", crc))
            inserted_exif = True
        if kind not in _PNG_METADATA_CHUNKS:
            chunks.append(data[index:end])
        index = end
        if kind == b"IEND":
            return signature + b"".join(chunks)
    return None


def strip_photo_metadata(data: bytes) -> SanitizedPhoto:
    """Remove location/device metadata from a camera upload before archiving it.

    JPEG and PNG files are rewritten losslessly (pixel data is untouched) and
    keep only their EXIF orientation, so archived photos decode to the same
    oriented image and persisted manual corners stay valid.  Other formats that
    can embed EXIF are re-encoded as PNG after orientation is applied.
    """

    try:
        with Image.open(io.BytesIO(data)) as opened:
            source_format = str(opened.format or "").upper()
            orientation = int(opened.getexif().get(274, 1) or 1)
    except Exception:
        return SanitizedPhoto(data=data, suffix=None, method="undecodable")
    if source_format in {"JPEG", "MPO"}:
        stripped = _strip_jpeg_metadata(data, orientation)
        if stripped is not None:
            return SanitizedPhoto(data=stripped, suffix=".jpg" if source_format == "MPO" else None, method="jpeg-segments")
    elif source_format == "PNG":
        stripped = _strip_png_metadata(data, orientation)
        if stripped is not None:
            return SanitizedPhoto(data=stripped, suffix=None, method="png-chunks")
    elif source_format in {"GIF", "BMP"}:
        return SanitizedPhoto(data=data, suffix=None, method="no-metadata-format")
    image, _info = load_camera_image(data)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return SanitizedPhoto(data=output.getvalue(), suffix=".png", method="png-reencode")


def _imports() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover - declared runtime dependencies
        raise RuntimeError("Camera-photo preprocessing requires OpenCV and NumPy") from exc
    return cv2, np


def _order_points(points: Any, np: Any) -> Any:
    """Return corners clockwise from the top-left.

    Sorting by angle around the centroid stays well-defined when the display
    is rotated near 45 degrees, where x+y / y-x ordering assigns one point to
    two corners and collapses the homography.
    """

    pts = np.asarray(points, dtype="float32").reshape(4, 2)
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    # Image y points down, so ascending angle is clockwise on screen.
    clockwise = pts[np.argsort(angles, kind="stable")]
    start = int(clockwise.sum(axis=1).argmin())
    return np.roll(clockwise, -start, axis=0).astype("float32")


def _line_length(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def _candidate_metrics(
    corners: Any,
    *,
    edges: Any,
    image_width: int,
    image_height: int,
    contour_area: Optional[float],
    cv2: Any,
    np: Any,
) -> tuple[float, dict[str, float]]:
    ordered = _order_points(corners, np)
    quad_area = abs(float(cv2.contourArea(ordered.reshape(-1, 1, 2))))
    image_area = float(max(1, image_width * image_height))
    area_ratio = quad_area / image_area
    if area_ratio <= 0.0:
        return 0.0, {}

    top = _line_length(ordered[0], ordered[1])
    right = _line_length(ordered[1], ordered[2])
    bottom = _line_length(ordered[3], ordered[2])
    left = _line_length(ordered[0], ordered[3])
    min_side = min(top, right, bottom, left)
    if min_side < max(24.0, min(image_width, image_height) * 0.08):
        return 0.0, {}

    opposite_ratio = min(
        min(top, bottom) / max(1e-6, max(top, bottom)),
        min(left, right) / max(1e-6, max(left, right)),
    )
    rectangularity = (
        min(1.0, max(0.0, float(contour_area) / max(1.0, quad_area)))
        if contour_area is not None
        else 0.72
    )

    edge_band = np.zeros_like(edges, dtype=np.uint8)
    thickness = max(2, int(round(min(image_width, image_height) * 0.006)))
    cv2.polylines(
        edge_band,
        [np.round(ordered).astype(np.int32).reshape(-1, 1, 2)],
        True,
        255,
        thickness,
    )
    band_pixels = int(np.count_nonzero(edge_band))
    edge_support = float(np.count_nonzero((edges > 0) & (edge_band > 0))) / float(max(1, band_pixels))
    # A quadrilateral whose outline follows no image edges is a threshold
    # artifact (typically a luminance region bleeding into the background),
    # not a display or board boundary.
    if edge_support < MIN_QUAD_EDGE_SUPPORT:
        return 0.0, {}
    border_margin = max(2.0, min(image_width, image_height) * 0.008)
    touches_frame = sum(
        1
        for x, y in ordered
        if x <= border_margin
        or y <= border_margin
        or x >= image_width - 1 - border_margin
        or y >= image_height - 1 - border_margin
    )
    # Binary masks often return the image frame itself as a perfect rectangle.
    # It carries no perspective evidence and should remain the unwarped fallback.
    if area_ratio >= 0.97 and touches_frame >= 3 and edge_support < 0.03:
        return 0.0, {}

    center = ordered.mean(axis=0)
    center_distance = math.hypot(
        (float(center[0]) - image_width * 0.5) / max(1.0, image_width * 0.5),
        (float(center[1]) - image_height * 0.5) / max(1.0, image_height * 0.5),
    )
    center_score = max(0.0, 1.0 - center_distance / 1.25)
    area_score = min(1.0, area_ratio / 0.58)
    perspective_score = min(1.0, opposite_ratio / 0.55)
    score = (
        edge_support * 0.34
        + area_score * 0.28
        + rectangularity * 0.16
        + perspective_score * 0.14
        + center_score * 0.08
    )
    return score, {
        "area_ratio": area_ratio,
        "edge_support": edge_support,
        "opposite_side_ratio": opposite_ratio,
        "rectangularity": rectangularity,
        "center_score": center_score,
        "touches_frame": float(touches_frame),
    }


def _contour_candidates(
    rgb: Any,
    *,
    offset_x: float,
    offset_y: float,
    source_scale: float,
    cv2: Any,
    np: Any,
) -> list[PhotoQuadCandidate]:
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(clahe, (5, 5), 0)

    masks: list[tuple[str, Any]] = []
    combined_edges = np.zeros_like(gray, dtype=np.uint8)
    for low, high in ((24, 72), (45, 135), (70, 210)):
        edges = cv2.Canny(blurred, low, high)
        combined_edges = cv2.bitwise_or(combined_edges, edges)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        masks.append((f"canny-{low}-{high}", closed))

    otsu_value, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append((f"luminance-{int(otsu_value)}", cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))))
    masks.append(("luminance-inverse", cv2.bitwise_not(otsu)))

    candidates: list[PhotoQuadCandidate] = []
    min_area = float(width * height) * 0.055
    for mask_name, mask in masks:
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:80]
        for contour in contours:
            contour_area = float(cv2.contourArea(contour))
            if contour_area < min_area:
                continue
            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0.0:
                continue
            for epsilon_ratio in (0.012, 0.02, 0.032):
                approx = cv2.approxPolyDP(contour, epsilon_ratio * perimeter, True)
                if len(approx) != 4 or not cv2.isContourConvex(approx):
                    continue
                ordered = _order_points(approx.reshape(4, 2), np)
                score, metrics = _candidate_metrics(
                    ordered,
                    edges=combined_edges,
                    image_width=width,
                    image_height=height,
                    contour_area=contour_area,
                    cv2=cv2,
                    np=np,
                )
                if score <= 0.0:
                    continue
                full = tuple(
                    (
                        float(point[0]) / source_scale + offset_x,
                        float(point[1]) / source_scale + offset_y,
                    )
                    for point in ordered
                )
                candidates.append(
                    PhotoQuadCandidate(
                        corners=full,
                        score=score,
                        source=f"{mask_name}:contour",
                        metrics=metrics,
                    )
                )
                break
    return candidates


def _line_intersection(first: Sequence[float], second: Sequence[float]) -> Optional[tuple[float, float]]:
    x1, y1, x2, y2 = (float(value) for value in first)
    x3, y3, x4, y4 = (float(value) for value in second)
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-7:
        return None
    determinant_a = x1 * y2 - y1 * x2
    determinant_b = x3 * y4 - y3 * x4
    return (
        (determinant_a * (x3 - x4) - (x1 - x2) * determinant_b) / denominator,
        (determinant_a * (y3 - y4) - (y1 - y2) * determinant_b) / denominator,
    )


def _hough_candidate(
    rgb: Any,
    *,
    offset_x: float,
    offset_y: float,
    source_scale: float,
    cv2: Any,
    np: Any,
) -> Optional[PhotoQuadCandidate]:
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 110)
    minimum = max(40, int(min(width, height) * 0.24))
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360.0,
        threshold=max(45, int(min(width, height) * 0.06)),
        minLineLength=minimum,
        maxLineGap=max(12, int(min(width, height) * 0.035)),
    )
    if lines is None or len(lines) < 4:
        return None

    segments = [tuple(float(value) for value in line) for line in lines.reshape(-1, 4)]
    segments.sort(key=lambda line: _line_length(line[:2], line[2:]), reverse=True)
    segments = segments[:60]
    samples = []
    for x1, y1, x2, y2 in segments:
        angle = math.atan2(y2 - y1, x2 - x1)
        samples.append([math.cos(2.0 * angle), math.sin(2.0 * angle)])
    sample_array = np.asarray(samples, dtype=np.float32)
    _compactness, labels, centers = cv2.kmeans(
        sample_array,
        2,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-4),
        8,
        cv2.KMEANS_PP_CENTERS,
    )
    family_angles = [0.5 * math.atan2(float(center[1]), float(center[0])) for center in centers]
    separation = abs(math.degrees(family_angles[0] - family_angles[1])) % 180.0
    separation = min(separation, 180.0 - separation)
    if separation < 38.0:
        return None

    boundary_lines: list[tuple[Sequence[float], Sequence[float]]] = []
    for family_index, angle in enumerate(family_angles):
        normal = (-math.sin(angle), math.cos(angle))
        members: list[tuple[float, Sequence[float]]] = []
        for index, segment in enumerate(segments):
            if int(labels[index][0]) != family_index:
                continue
            midpoint = ((segment[0] + segment[2]) * 0.5, (segment[1] + segment[3]) * 0.5)
            projection = midpoint[0] * normal[0] + midpoint[1] * normal[1]
            members.append((projection, segment))
        if len(members) < 2:
            return None
        members.sort(key=lambda item: item[0])
        boundary_lines.append((members[0][1], members[-1][1]))

    first_low, first_high = boundary_lines[0]
    second_low, second_high = boundary_lines[1]
    intersections = [
        _line_intersection(first_low, second_low),
        _line_intersection(first_low, second_high),
        _line_intersection(first_high, second_high),
        _line_intersection(first_high, second_low),
    ]
    if any(point is None for point in intersections):
        return None
    raw = np.asarray(intersections, dtype=np.float32)
    if (
        raw[:, 0].min() < -width * 0.25
        or raw[:, 0].max() > width * 1.25
        or raw[:, 1].min() < -height * 0.25
        or raw[:, 1].max() > height * 1.25
    ):
        return None
    ordered = _order_points(raw, np)
    score, metrics = _candidate_metrics(
        ordered,
        edges=edges,
        image_width=width,
        image_height=height,
        contour_area=None,
        cv2=cv2,
        np=np,
    )
    if score <= 0.0:
        return None
    full = tuple(
        (
            float(point[0]) / source_scale + offset_x,
            float(point[1]) / source_scale + offset_y,
        )
        for point in ordered
    )
    return PhotoQuadCandidate(full, score, "hough-lines", metrics)


def _corner_distance(first: PhotoQuadCandidate, second: PhotoQuadCandidate) -> float:
    return sum(
        math.hypot(a[0] - b[0], a[1] - b[1])
        for a, b in zip(first.corners, second.corners)
    ) / 4.0


def _deduplicate_candidates(
    candidates: Iterable[PhotoQuadCandidate],
    *,
    image_size: tuple[int, int],
    limit: int,
) -> list[PhotoQuadCandidate]:
    threshold = min(image_size) * 0.035
    selected: list[PhotoQuadCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if any(_corner_distance(candidate, prior) <= threshold for prior in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _manual_candidate(corners: Sequence[Sequence[float]], np: Any) -> PhotoQuadCandidate:
    if len(corners) != 4 or any(len(point) != 2 for point in corners):
        raise ValueError("manual photo corners must contain four x/y pairs")
    ordered = _order_points(np.asarray(corners, dtype=np.float32), np)
    return PhotoQuadCandidate(
        corners=tuple((float(point[0]), float(point[1])) for point in ordered),
        score=1.0,
        source="manual",
        metrics={"manual": 1.0},
    )


def _warp_candidate(
    image: Image.Image,
    candidate: PhotoQuadCandidate,
    *,
    max_output_dim: int,
    target_aspect_ratio: Optional[float] = None,
    cv2: Any,
    np: Any,
) -> tuple[Image.Image, Any, Any]:
    source = np.asarray(image.convert("RGB"))
    corners = _order_points(np.asarray(candidate.corners, dtype=np.float32), np)
    top = _line_length(corners[0], corners[1])
    bottom = _line_length(corners[3], corners[2])
    left = _line_length(corners[0], corners[3])
    right = _line_length(corners[1], corners[2])
    output_width = max(2, int(round(max(top, bottom))))
    output_height = max(2, int(round(max(left, right))))
    if target_aspect_ratio is not None and 0.15 <= target_aspect_ratio <= 6.0:
        pixel_area = float(max(4, output_width * output_height))
        output_width = max(2, int(round(math.sqrt(pixel_area * target_aspect_ratio))))
        output_height = max(2, int(round(output_width / target_aspect_ratio)))
    scale = min(1.0, float(max_output_dim) / float(max(output_width, output_height)))
    output_width = max(2, int(round(output_width * scale)))
    output_height = max(2, int(round(output_height * scale)))
    destination = np.asarray(
        [[0, 0], [output_width - 1, 0], [output_width - 1, output_height - 1], [0, output_height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(corners, destination)
    inverse = cv2.getPerspectiveTransform(destination, corners)
    warped = cv2.warpPerspective(
        source,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return Image.fromarray(warped, mode="RGB"), matrix, inverse


def _cluster_positions(values: Sequence[float], tolerance: float) -> list[float]:
    if not values:
        return []
    ordered = sorted(float(value) for value in values)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _trim_discontinuous_end_lines(
    lines: Sequence[float],
    edges: Any,
    *,
    horizontal: bool,
    span: tuple[float, float],
) -> list[float]:
    """Drop end lines that are not continuous across the lattice.

    Board grid lines run the full width/height of the board, while header
    text baselines and buttons that happen to fall on the lattice pitch only
    produce fragmented edges.  Without this, a tall board can gain an extra
    row that extends into the level header.
    """

    positions = list(lines)
    limit = edges.shape[0] if horizontal else edges.shape[1]
    start = max(0, int(round(min(span))))
    stop = min(edges.shape[1] if horizontal else edges.shape[0], int(round(max(span))) + 1)
    if stop - start < 8:
        return positions

    def continuity(position: float) -> float:
        center = int(round(position))
        low, high = max(0, center - 2), min(limit, center + 3)
        if high <= low:
            return 0.0
        strip = edges[low:high, start:stop] if horizontal else edges[start:stop, low:high]
        hits = (strip > 0).any(axis=0 if horizontal else 1)
        return float(hits.mean())

    scores = [continuity(position) for position in positions]
    reference = sorted(scores)[len(scores) // 2]
    while len(positions) > 3 and scores[0] < reference * 0.6:
        positions.pop(0)
        scores.pop(0)
    while len(positions) > 3 and scores[-1] < reference * 0.6:
        positions.pop()
        scores.pop()
    return positions


def _spacing_regularity(positions: Sequence[float]) -> float:
    if len(positions) < 3:
        return 0.0
    gaps = [right - left for left, right in zip(positions, positions[1:]) if right > left]
    if len(gaps) < 2:
        return 0.0
    ordered = sorted(gaps)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return 0.0
    deviations = sorted(abs(value - median) for value in gaps)
    median_deviation = deviations[len(deviations) // 2]
    return max(0.0, 1.0 - median_deviation / max(1.0, median * 0.30))


def _candidate_pitches(ordered: Sequence[float], minimum_pitch: float, maximum_pitch: float) -> list[float]:
    raw: list[float] = []
    for left_index, left in enumerate(ordered[:-1]):
        for right in ordered[left_index + 1 :]:
            difference = right - left
            for steps in range(1, min(16, len(ordered))):
                pitch = difference / float(steps)
                if minimum_pitch <= pitch <= maximum_pitch:
                    raw.append(pitch)
    raw.sort()
    # Matching tolerates 14% pitch error, so pitches within 0.2% of each other
    # select the same lines.  Collapsing them keeps the search near-linear in
    # the pitch range instead of quadratic in the number of detected lines.
    clusters: list[list[float]] = []
    for pitch in raw:
        if clusters and pitch - clusters[-1][0] <= clusters[-1][0] * 0.002:
            clusters[-1].append(pitch)
        else:
            clusters.append([pitch])
    return [cluster[len(cluster) // 2] for cluster in clusters]


def _dominant_lattice_run(positions: Sequence[float], *, extent: int) -> list[float]:
    """Return the strongest contiguous arithmetic run among decorative lines."""

    ordered = sorted(float(value) for value in positions)
    if len(ordered) < 3 or extent <= 0:
        return ordered
    minimum_pitch = max(5.0, float(extent) / 36.0)
    maximum_pitch = float(extent) / 2.0

    best: list[float] = []
    best_score = float("-inf")
    for pitch in _candidate_pitches(ordered, minimum_pitch, maximum_pitch):
        tolerance = max(3.0, pitch * 0.14)
        # Origins with the same lattice phase select the same lines.
        phase_step = pitch * 0.02
        seen_phases: set[int] = set()
        for origin in ordered:
            phase = int((origin % pitch) / phase_step)
            if phase in seen_phases:
                continue
            seen_phases.add(phase)
            matched: dict[int, tuple[float, float]] = {}
            for value in ordered:
                step = int(round((value - origin) / pitch))
                predicted = origin + float(step) * pitch
                error = abs(value - predicted)
                if error > tolerance:
                    continue
                current = matched.get(step)
                if current is None or error < current[1]:
                    matched[step] = (value, error)
            if len(matched) < 3:
                continue
            keys = sorted(matched)
            runs: list[list[int]] = [[keys[0]]]
            for key in keys[1:]:
                if key == runs[-1][-1] + 1:
                    runs[-1].append(key)
                else:
                    runs.append([key])
            for run in runs:
                if len(run) < 3 or len(run) > 34:
                    continue
                values = [matched[key][0] for key in run]
                error = sum(matched[key][1] for key in run) / float(len(run))
                coverage = (values[-1] - values[0]) / float(max(1, extent))
                score = len(values) * 2.0 + coverage - error / max(1.0, pitch)
                if score > best_score:
                    best = values
                    best_score = score
    return sorted(best) if len(best) >= 3 else ordered


def _lattice_metrics(image: Image.Image, *, cv2: Any, np: Any) -> dict[str, float]:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 22, 85)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180.0,
        threshold=max(35, int(min(width, height) * 0.045)),
        minLineLength=max(18, int(min(width, height) * 0.12)),
        maxLineGap=max(8, int(min(width, height) * 0.025)),
    )
    if lines is None:
        return {"score": 0.0}
    horizontal: list[float] = []
    vertical: list[float] = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        angle = abs(math.degrees(math.atan2(float(y2 - y1), float(x2 - x1))))
        if angle <= 12.0 or abs(angle - 180.0) <= 12.0:
            horizontal.append((float(y1) + float(y2)) * 0.5)
        elif abs(angle - 90.0) <= 12.0:
            vertical.append((float(x1) + float(x2)) * 0.5)
    tolerance = max(5.0, min(width, height) * 0.012)
    x_lines = _cluster_positions(vertical, tolerance)
    y_lines = _cluster_positions(horizontal, tolerance)
    x_lines = _dominant_lattice_run(x_lines, extent=width)
    y_lines = _dominant_lattice_run(y_lines, extent=height)
    if len(x_lines) >= 3 and len(y_lines) >= 3:
        y_lines = _trim_discontinuous_end_lines(y_lines, edges, horizontal=True, span=(x_lines[0], x_lines[-1]))
        x_lines = _trim_discontinuous_end_lines(x_lines, edges, horizontal=False, span=(y_lines[0], y_lines[-1]))
    if len(x_lines) < 3 or len(y_lines) < 3:
        return {
            "score": 0.0,
            "vertical_lines": float(len(x_lines)),
            "horizontal_lines": float(len(y_lines)),
        }
    x_coverage = (x_lines[-1] - x_lines[0]) / float(max(1, width))
    y_coverage = (y_lines[-1] - y_lines[0]) / float(max(1, height))
    regularity = min(_spacing_regularity(x_lines), _spacing_regularity(y_lines))
    count_score = min(1.0, max(0.0, (min(len(x_lines), len(y_lines)) - 2.0) / 7.0))
    coverage_score = min(1.0, min(x_coverage, y_coverage) / 0.82)
    score = regularity * 0.42 + coverage_score * 0.38 + count_score * 0.20
    return {
        "score": score,
        "vertical_lines": float(len(x_lines)),
        "horizontal_lines": float(len(y_lines)),
        "x_coverage": x_coverage,
        "y_coverage": y_coverage,
        "regularity": regularity,
        "target_aspect_ratio": float(max(1, len(x_lines) - 1)) / float(max(1, len(y_lines) - 1)),
        # Cell pitch as a fraction of the analyzed image, so it survives the
        # resampling between candidate previews and the final board warp.
        "pitch_x_fraction": (x_lines[-1] - x_lines[0]) / float(max(1, len(x_lines) - 1)) / float(max(1, width)),
        "pitch_y_fraction": (y_lines[-1] - y_lines[0]) / float(max(1, len(y_lines) - 1)) / float(max(1, height)),
        "x_min_fraction": x_lines[0] / float(max(1, width)),
        "x_max_fraction": x_lines[-1] / float(max(1, width)),
        "y_min_fraction": y_lines[0] / float(max(1, height)),
        "y_max_fraction": y_lines[-1] / float(max(1, height)),
        "x_min": x_lines[0],
        "x_max": x_lines[-1],
        "y_min": y_lines[0],
        "y_max": y_lines[-1],
    }


@dataclass(frozen=True)
class _BoardEvaluation:
    candidate: PhotoQuadCandidate
    lattice: dict[str, float]
    area_ratio: float


def _display_is_board(lattice: dict[str, float]) -> bool:
    """True when a regular lattice extrapolated by one pitch reaches all four edges."""

    if float(lattice.get("score", 0.0)) < 0.5 or float(lattice.get("regularity", 0.0)) < 0.72:
        return False
    try:
        pitch_x, pitch_y = float(lattice["pitch_x_fraction"]), float(lattice["pitch_y_fraction"])
        x_min, x_max = float(lattice["x_min_fraction"]), float(lattice["x_max_fraction"])
        y_min, y_max = float(lattice["y_min_fraction"]), float(lattice["y_max_fraction"])
    except KeyError:
        return False
    return (
        x_min <= 1.2 * pitch_x
        and 1.0 - x_max <= 1.2 * pitch_x
        and y_min <= 1.2 * pitch_y
        and 1.0 - y_max <= 1.2 * pitch_y
    )


def _evaluate_board_candidates(image: Image.Image, *, cv2: Any, np: Any) -> list[_BoardEvaluation]:
    """Warp each plausible board quadrilateral once and measure its lattice.

    This is the expensive part of board correction and does not depend on the
    requested grid size, so it is cached per display and re-ranked cheaply.
    """

    scale = min(1.0, 1400.0 / float(max(image.size)))
    detection = image
    if scale < 1.0:
        detection = image.resize(
            (max(2, int(round(image.width * scale))), max(2, int(round(image.height * scale)))),
            Image.Resampling.LANCZOS,
        )
    proposals = _contour_candidates(
        np.asarray(detection.convert("RGB")),
        offset_x=0.0,
        offset_y=0.0,
        source_scale=scale,
        cv2=cv2,
        np=np,
    )
    display_lattice = _lattice_metrics(detection, cv2=cv2, np=np)
    if (
        float(display_lattice.get("score", 0.0)) >= 0.66
        and float(display_lattice.get("regularity", 0.0)) >= 0.72
        and min(
            float(display_lattice.get("vertical_lines", 0.0)),
            float(display_lattice.get("horizontal_lines", 0.0)),
        )
        >= 4.0
    ):
        x_min = float(display_lattice["x_min"]) / scale
        x_max = float(display_lattice["x_max"]) / scale
        y_min = float(display_lattice["y_min"]) / scale
        y_max = float(display_lattice["y_max"]) / scale
        area_ratio = ((x_max - x_min) * (y_max - y_min)) / float(max(1, image.width * image.height))
        if 0.075 <= area_ratio <= 0.90:
            proposals.append(
                PhotoQuadCandidate(
                    corners=((x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)),
                    score=0.86,
                    source="regular-lattice-envelope",
                    metrics={
                        "area_ratio": area_ratio,
                        "center_score": 1.0,
                        "edge_support": 1.0,
                        "opposite_side_ratio": 1.0,
                        "rectangularity": 1.0,
                        "touches_frame": 0.0,
                    },
                )
            )
    candidates = _deduplicate_candidates(proposals, image_size=image.size, limit=12)
    evaluations: list[_BoardEvaluation] = []
    if _display_is_board(display_lattice):
        # The display outline already sits on the board: its own lattice is
        # regular and reaches every edge within about a cell.  Searching
        # inside would drop the edge rows that line detection misses at the
        # image border, so offer the whole display as a board candidate.
        width, height = image.size
        evaluations.append(
            _BoardEvaluation(
                candidate=PhotoQuadCandidate(
                    corners=((0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0)),
                    score=1.0,
                    source="display-is-board",
                    metrics={"area_ratio": 1.0, "edge_support": 1.0, "opposite_side_ratio": 1.0, "rectangularity": 1.0},
                ),
                lattice=display_lattice,
                area_ratio=1.0,
            )
        )
    for candidate in candidates:
        area_ratio = float(candidate.metrics.get("area_ratio", 0.0))
        if not 0.075 <= area_ratio <= 0.90:
            continue
        preview, _matrix, _inverse = _warp_candidate(
            image,
            candidate,
            max_output_dim=1200,
            cv2=cv2,
            np=np,
        )
        evaluations.append(
            _BoardEvaluation(
                candidate=candidate,
                lattice=_lattice_metrics(preview, cv2=cv2, np=np),
                area_ratio=area_ratio,
            )
        )
    return evaluations


def _lattice_dimensions(lattice: dict[str, float]) -> tuple[int, int]:
    rows = max(0, int(round(float(lattice.get("horizontal_lines", 0.0)))) - 1)
    cols = max(0, int(round(float(lattice.get("vertical_lines", 0.0)))) - 1)
    return rows, cols


def _rank_board_candidates(
    evaluations: Sequence[_BoardEvaluation],
    expected_grid: Optional[tuple[int, int]],
) -> list[tuple[float, PhotoQuadCandidate, dict[str, float]]]:
    ranked: list[tuple[float, PhotoQuadCandidate, dict[str, float]]] = []
    for evaluation in evaluations:
        candidate = evaluation.candidate
        lattice = dict(evaluation.lattice)
        lattice_score = float(lattice.get("score", 0.0))
        area_term = min(1.0, evaluation.area_ratio / 0.35)
        if expected_grid is not None:
            expected_rows, expected_cols = expected_grid
            detected_rows, detected_cols = _lattice_dimensions(lattice)
            dimension_error = abs(detected_rows - expected_rows) + abs(detected_cols - expected_cols)
            dimension_score = max(
                0.0,
                1.0 - dimension_error / float(max(1, expected_rows + expected_cols)),
            )
            lattice["dimension_score"] = dimension_score
            combined = (
                lattice_score * 0.50
                + candidate.score * 0.18
                + area_term * 0.07
                + dimension_score * 0.25
            )
        else:
            combined = lattice_score * 0.68 + candidate.score * 0.24 + area_term * 0.08
        ranked.append((combined, candidate, lattice))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _board_ranking_is_confident(ranked: Sequence[tuple[float, PhotoQuadCandidate, dict[str, float]]]) -> bool:
    return bool(ranked) and ranked[0][0] >= 0.48 and float(ranked[0][2].get("score", 0.0)) >= 0.38


# Warp detection only trusts border gaps when it can see at least 0.28 cells
# outside the outer grid lines (image_utils.detect_warp_edges); the board
# itself is cropped at those lines, so warps get their own padded view.
BOARD_CONTEXT_MARGIN_CELLS = 0.5


def _board_context_view(
    display_image: Image.Image,
    corners: Sequence[Sequence[float]],
    lattice: dict[str, float],
    board_size: tuple[int, int],
    *,
    cv2: Any,
    np: Any,
) -> Optional[Image.Image]:
    """The rectified board plus half a cell of real surrounding pixels."""

    pitch_x = float(lattice.get("pitch_x_fraction", 0.0))
    pitch_y = float(lattice.get("pitch_y_fraction", 0.0))
    if pitch_x <= 0.0 or pitch_y <= 0.0:
        return None
    margin_x, margin_y = BOARD_CONTEXT_MARGIN_CELLS * pitch_x, BOARD_CONTEXT_MARGIN_CELLS * pitch_y
    quad = _order_points(np.asarray(corners, dtype=np.float32), np)
    unit = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    to_display = cv2.getPerspectiveTransform(unit, quad)
    local = np.float32(
        [[-margin_x, -margin_y], [1 + margin_x, -margin_y], [1 + margin_x, 1 + margin_y], [-margin_x, 1 + margin_y]]
    ).reshape(-1, 1, 2)
    expanded = cv2.perspectiveTransform(local, to_display).reshape(-1, 2).astype(np.float32)
    width = max(2, int(round(board_size[0] * (1 + 2 * margin_x))))
    height = max(2, int(round(board_size[1] * (1 + 2 * margin_y))))
    destination = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    matrix = cv2.getPerspectiveTransform(expanded, destination)
    # Constant black outside the display: replicated edges would smear the
    # outer grid line into a fake solid border.
    warped = cv2.warpPerspective(
        np.asarray(display_image.convert("RGB")),
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    return Image.fromarray(warped, mode="RGB")


def _rectified_board(
    image: Image.Image,
    evaluations: Sequence[_BoardEvaluation],
    *,
    max_output_dim: int,
    expected_grid: Optional[tuple[int, int]],
    cv2: Any,
    np: Any,
) -> tuple[Image.Image, Optional[PhotoQuadCandidate], dict[str, Any], Optional[Any], Optional[Any]]:
    ranked = _rank_board_candidates(evaluations, None)
    strategy = "lattice"
    if expected_grid is not None:
        # Keep the unhinted choice, which the corrected preview shows, whenever
        # it already agrees with the requested grid so every stage of one
        # import rectifies the board identically.
        if not (
            _board_ranking_is_confident(ranked)
            and _lattice_dimensions(ranked[0][2]) == tuple(int(value) for value in expected_grid)
        ):
            ranked = _rank_board_candidates(evaluations, expected_grid)
            strategy = "expected-grid"
        else:
            strategy = "lattice-matches-expected-grid"
    if not _board_ranking_is_confident(ranked):
        return image, None, {"selected": None, "candidates": [], "strategy": strategy}, None, None

    combined, selected, lattice = ranked[0]
    target_aspect = (
        float(lattice.get("target_aspect_ratio", 0.0))
        if float(lattice.get("regularity", 0.0)) >= 0.55
        and min(float(lattice.get("x_coverage", 0.0)), float(lattice.get("y_coverage", 0.0))) >= 0.64
        else None
    )
    board, matrix, inverse = _warp_candidate(
        image,
        selected,
        max_output_dim=max_output_dim,
        target_aspect_ratio=target_aspect,
        cv2=cv2,
        np=np,
    )
    info = {
        "selected": selected.as_dict(),
        "strategy": strategy,
        "combined_score": round(float(combined), 5),
        "lattice": {key: round(float(value), 5) for key, value in lattice.items()},
        "target_aspect_ratio": round(float(target_aspect), 6) if target_aspect else None,
        "candidates": [
            {
                **candidate.as_dict(),
                "combined_score": round(float(score), 5),
                "lattice": {key: round(float(value), 5) for key, value in metrics.items()},
            }
            for score, candidate, metrics in ranked[:5]
        ],
    }
    return board, selected, info, matrix, inverse


_EDGE_SIGMA_TABLE: Optional[list[tuple[float, float]]] = None
_EDGE_CONTRAST_WINDOW = 21


def _edge_slope_ratio(gray: Any, *, cv2: Any, np: Any) -> Optional[float]:
    """Median edge slope divided by local contrast (1/pixel), independent of exposure."""

    edges = cv2.Canny(gray, 30, 90) > 0
    kernel = np.ones((_EDGE_CONTRAST_WINDOW, _EDGE_CONTRAST_WINDOW), np.uint8)
    contrast = cv2.dilate(gray, kernel).astype(np.float32) - cv2.erode(gray, kernel).astype(np.float32)
    selected = edges & (contrast >= 40.0)
    if int(np.count_nonzero(selected)) < 150:
        return None
    values = gray.astype(np.float32)
    gradient_x = cv2.Sobel(values, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gradient_y = cv2.Sobel(values, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    slope = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y)
    return float(np.median(slope[selected] / contrast[selected]))


def _edge_sigma_table(*, cv2: Any, np: Any) -> list[tuple[float, float]]:
    """Map slope ratios to Gaussian blur sigma using synthetic disks.

    Built once from the same measurement code, so the estimate is calibrated
    rather than tied to hand-tuned Laplacian thresholds.
    """

    global _EDGE_SIGMA_TABLE
    if _EDGE_SIGMA_TABLE is None:
        table: list[tuple[float, float]] = []
        # Beyond ~8 px Canny finds no edges at all, which callers treat as
        # "sharpness could not be measured" (routed to review).
        for sigma in (0.35, 0.6, 0.9, 1.2, 1.6, 2.0, 2.5, 3.0, 3.6, 4.3, 5.0, 6.0, 7.0, 8.0):
            disk = np.full((220, 220), 18, np.uint8)
            cv2.circle(disk, (110, 110), 60, 205, -1, lineType=cv2.LINE_AA)
            blurred = cv2.GaussianBlur(disk, (0, 0), sigma)
            ratio = _edge_slope_ratio(blurred, cv2=cv2, np=np)
            if ratio is not None:
                table.append((ratio, sigma))
        table.sort(reverse=True)
        _EDGE_SIGMA_TABLE = table
    return _EDGE_SIGMA_TABLE


def _estimate_blur_sigma(gray: Any, *, cv2: Any, np: Any) -> Optional[float]:
    ratio = _edge_slope_ratio(gray, cv2=cv2, np=np)
    table = _edge_sigma_table(cv2=cv2, np=np)
    if ratio is None or not table:
        return None
    if ratio >= table[0][0]:
        return table[0][1]
    for (high_ratio, low_sigma), (low_ratio, high_sigma) in zip(table, table[1:]):
        if low_ratio <= ratio <= high_ratio:
            weight = (high_ratio - ratio) / max(1e-9, high_ratio - low_ratio)
            return low_sigma + (high_sigma - low_sigma) * weight
    return table[-1][1]


def _smooth_profile(values: Any, sigma: float, *, np: Any) -> Any:
    radius = max(1, int(math.ceil(sigma * 3.0)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(offsets * offsets) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    padded = np.pad(values, radius, mode="reflect")
    return np.convolve(padded, kernel, mode="valid")


def _estimate_cell_size(image: Image.Image, *, cv2: Any, np: Any) -> Optional[float]:
    scale = min(1.0, 1200.0 / float(max(image.size)))
    preview = image
    if scale < 1.0:
        preview = image.resize(
            (max(2, int(round(image.width * scale))), max(2, int(round(image.height * scale)))),
            Image.Resampling.BILINEAR,
        )
    lattice = _lattice_metrics(preview, cv2=cv2, np=np)
    if (
        float(lattice.get("score", 0.0)) < 0.5
        or float(lattice.get("regularity", 0.0)) < 0.6
        or min(float(lattice.get("vertical_lines", 0.0)), float(lattice.get("horizontal_lines", 0.0))) < 4
    ):
        return None
    return min(
        float(lattice["pitch_x_fraction"]) * image.width,
        float(lattice["pitch_y_fraction"]) * image.height,
    )


def _exclude_terminal_like_regions(
    mask: Any,
    gray: Any,
    *,
    cell_size: Optional[float],
    cv2: Any,
    np: Any,
) -> tuple[Any, int]:
    """Keep white/gray terminal dots out of the glare mask.

    The game's white and gray endpoints are bright and unsaturated, exactly
    like reflections.  A terminal is a compact disk no larger than one cell
    with darker board background all around it; reflections spread across
    cells or are not disk-shaped.
    """

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return mask, 0
    height, width = mask.shape[:2]
    if cell_size:
        low, high = cell_size * 0.30, cell_size * 0.95
    else:
        low, high = min(width, height) * 0.02, min(width, height) * 0.16
    cleaned = mask.copy()
    excluded = 0
    kernel = np.ones((3, 3), np.uint8)
    for label in range(1, count):
        x, y, box_width, box_height, area = (int(value) for value in stats[label])
        size = max(box_width, box_height)
        if size < low or size > high or min(box_width, box_height) < size * 0.7:
            continue
        if not 0.55 <= area / float(max(1, box_width * box_height)) <= 0.95:
            continue
        pad = max(3, int(round(size * 0.3)))
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(width, x + box_width + pad), min(height, y + box_height + pad)
        component = labels[y0:y1, x0:x1] == label
        ring = (cv2.dilate(component.astype(np.uint8), kernel, iterations=pad) > 0) & ~component
        if int(np.count_nonzero(ring)) < 8:
            continue
        local = gray[y0:y1, x0:x1]
        if float(np.median(local[ring])) > float(np.median(local[component])) - 45.0:
            continue
        cleaned[y0:y1, x0:x1][component] = 0
        excluded += 1
    return cleaned, excluded


# Review/warning thresholds.  Blur is expressed relative to the board cell
# because terminal color sampling tolerates blur in proportion to dot size.
BLUR_CELL_RATIO_WARN = 0.045
BLUR_CELL_RATIO_REVIEW = 0.075
BANDING_WARN_LEVELS = 2.0
MIN_RELIABLE_CELL_PX = 26.0


def _quality_and_views(
    image: Image.Image,
    *,
    cell_size: Optional[float] = None,
    cv2: Any,
    np: Any,
) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    tenengrad = float(np.mean(sobel_x * sobel_x + sobel_y * sobel_y))

    cell_source = "lattice" if cell_size else None
    if not cell_size:
        cell_size = _estimate_cell_size(image, cv2=cv2, np=np)
        cell_source = "estimated" if cell_size else None

    canonical_scale = float(QUALITY_CANONICAL_SHORT_SIDE) / float(max(1, min(gray.shape[:2])))
    canonical = cv2.resize(
        gray,
        None,
        fx=canonical_scale,
        fy=canonical_scale,
        interpolation=cv2.INTER_AREA if canonical_scale < 1.0 else cv2.INTER_LINEAR,
    )
    blur_sigma = _estimate_blur_sigma(canonical, cv2=cv2, np=np)
    # Without a detected lattice assume a mid-sized (9-cell) board.
    canonical_cell = (
        cell_size * canonical_scale if cell_size else float(QUALITY_CANONICAL_SHORT_SIDE) / 9.0
    )
    blur_cell_ratio = blur_sigma / canonical_cell if blur_sigma is not None else None

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    clipped_glare = ((hsv[:, :, 2] >= 246) & (hsv[:, :, 1] <= 45)).astype(np.uint8) * 255
    clipped_glare = cv2.morphologyEx(
        clipped_glare,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8),
    )
    # Reflections on dark phone screens are often broad and gray rather than
    # clipped white. A larger opening keeps those regions while rejecting the
    # thin low-saturation grid strokes that are valid puzzle evidence.
    diffuse_glare = ((hsv[:, :, 2] >= 105) & (hsv[:, :, 1] <= 70)).astype(np.uint8) * 255
    diffuse_kernel_size = max(5, int(round(min(image.size) * 0.012)))
    if diffuse_kernel_size % 2 == 0:
        diffuse_kernel_size += 1
    diffuse_glare = cv2.morphologyEx(
        diffuse_glare,
        cv2.MORPH_OPEN,
        np.ones((diffuse_kernel_size, diffuse_kernel_size), np.uint8),
    )
    glare = cv2.max(clipped_glare, diffuse_glare)
    glare, terminal_like_regions = _exclude_terminal_like_regions(
        glare,
        gray,
        cell_size=cell_size,
        cv2=cv2,
        np=np,
    )
    glare = cv2.dilate(glare, np.ones((5, 5), np.uint8), iterations=1)
    glare_fraction = float(np.count_nonzero(glare)) / float(max(1, glare.size))
    underexposed_fraction = float(np.count_nonzero(gray <= 8)) / float(max(1, gray.size))
    overexposed_fraction = float(np.count_nonzero(gray >= 247)) / float(max(1, gray.size))
    # A correctly exposed board is mostly near-black, so "dim" means even the
    # brightest content (lines, dots) is dark rather than many dark pixels.
    bright_percentile = float(np.percentile(gray, 99.5))

    # Banding is broad row-to-row brightness variation.  A vertical opening
    # removes thin horizontal grid lines and the per-row median ignores dots,
    # so the remaining profile variation comes from the display or camera.
    opening_height = max(5, int(round(canonical.shape[0] * 0.02))) | 1
    opened = cv2.morphologyEx(
        canonical,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, opening_height)),
    )
    profile = np.median(opened, axis=1).astype(np.float32)
    if len(profile) >= 32:
        trend = _smooth_profile(profile, max(4.0, len(profile) * 0.06), np=np)
        banding_strength = float(np.std(profile - trend))
    else:
        banding_strength = 0.0

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
    if laplacian_variance < 140.0:
        base = cv2.bilateralFilter(clahe, 5, 30, 30)
        softened = cv2.GaussianBlur(base, (0, 0), 1.15)
        geometry = cv2.addWeighted(base, 1.55, softened, -0.55, 0)
    else:
        geometry = clahe

    warnings: list[str] = []
    if cell_size is not None and cell_size < MIN_RELIABLE_CELL_PX:
        warnings.append("Board cells are very small in this photo; move closer for more reliable cells and colors.")
    elif min(image.size) < 600:
        warnings.append("The rectified display is low resolution; move closer for more reliable cells and colors.")
    if blur_cell_ratio is not None and blur_cell_ratio > BLUR_CELL_RATIO_REVIEW:
        warnings.append("The photo is too blurred for reliable automatic terminal detection; retake it if possible.")
    elif blur_cell_ratio is not None and blur_cell_ratio > BLUR_CELL_RATIO_WARN:
        warnings.append("The photo is slightly blurred; grid recovery will use a sharpened geometry view.")
    if glare_fraction > 0.10:
        warnings.append("Glare obscures more than 10% of the rectified display; change the camera angle or lighting.")
    elif glare_fraction > 0.035:
        warnings.append("Some glare was detected and excluded from reliable color evidence.")
    if overexposed_fraction > 0.16:
        warnings.append("Large bright regions are clipped; lower the photographed screen or camera exposure.")
    if bright_percentile < 60.0:
        warnings.append("The photographed display looks very dim; increase screen brightness or ambient light.")
    if banding_strength > BANDING_WARN_LEVELS:
        warnings.append("Display/camera brightness banding may reduce line and color confidence.")

    quality = {
        "width": image.width,
        "height": image.height,
        "laplacian_variance": round(laplacian_variance, 4),
        "tenengrad": round(tenengrad, 4),
        "blur_sigma_px": round(blur_sigma, 4) if blur_sigma is not None else None,
        "blur_cell_ratio": round(blur_cell_ratio, 5) if blur_cell_ratio is not None else None,
        "cell_size_px": round(cell_size, 3) if cell_size else None,
        "cell_size_source": cell_source or "assumed",
        "glare_fraction": round(glare_fraction, 6),
        "terminal_like_regions_excluded": terminal_like_regions,
        "underexposed_fraction": round(underexposed_fraction, 6),
        "overexposed_fraction": round(overexposed_fraction, 6),
        "bright_percentile": round(bright_percentile, 3),
        "banding_strength": round(banding_strength, 6),
        "warnings": warnings,
    }
    return Image.fromarray(geometry, mode="L"), Image.fromarray(glare, mode="L"), quality


@dataclass(frozen=True)
class _DisplayStage:
    image: Image.Image
    matrix: Any
    inverse: Any
    candidates: tuple[PhotoQuadCandidate, ...]
    selected: Optional[PhotoQuadCandidate]
    scale: float
    roi: Optional[dict[str, int]]
    source_size: tuple[int, int]
    corroboration: Optional[int] = None
    corroborations: tuple[int, ...] = ()
    selected_index: int = 0


def _compute_display_stage(
    source: Image.Image,
    *,
    roi: Optional[tuple[int, int, int, int]],
    manual_corners: Optional[Sequence[Sequence[float]]],
    max_detection_dim: int,
    max_output_dim: int,
    candidate_limit: int,
    cv2: Any,
    np: Any,
) -> _DisplayStage:
    source_width, source_height = source.size

    offset_x = 0
    offset_y = 0
    region = source
    normalized_roi: Optional[dict[str, int]] = None
    if roi is not None:
        raw_x, raw_y, raw_width, raw_height = (int(value) for value in roi)
        left = max(0, min(source_width, raw_x))
        top = max(0, min(source_height, raw_y))
        right = max(left, min(source_width, raw_x + raw_width))
        bottom = max(top, min(source_height, raw_y + raw_height))
        if right <= left or bottom <= top:
            raise ValueError("camera photo ROI is empty")
        region = source.crop((left, top, right, bottom))
        offset_x, offset_y = left, top
        normalized_roi = {"x": left, "y": top, "width": right - left, "height": bottom - top}

    scale = min(1.0, float(max_detection_dim) / float(max(region.size)))
    detection_image = region
    if scale < 1.0:
        detection_image = region.resize(
            (max(2, int(round(region.width * scale))), max(2, int(round(region.height * scale)))),
            Image.Resampling.LANCZOS,
        )
    detection_rgb = np.asarray(detection_image.convert("RGB"))

    corroboration: Optional[int] = None
    corroborations: tuple[int, ...] = ()
    if manual_corners is not None:
        candidates = [_manual_candidate(manual_corners, np)]
    else:
        proposals = _contour_candidates(
            detection_rgb,
            offset_x=float(offset_x),
            offset_y=float(offset_y),
            source_scale=scale,
            cv2=cv2,
            np=np,
        )
        line_candidate = _hough_candidate(
            detection_rgb,
            offset_x=float(offset_x),
            offset_y=float(offset_y),
            source_scale=scale,
            cv2=cv2,
            np=np,
        )
        if line_candidate is not None:
            proposals.append(line_candidate)
        candidates = _deduplicate_candidates(
            proposals,
            image_size=source.size,
            limit=max(1, int(candidate_limit)),
        )
        # Independent detectors (edge thresholds, luminance, line fits) that
        # found the same outline.  A pick nothing else agrees with is weak
        # evidence even when its own score is high.
        agreement = min(source.size) * 0.035
        corroborations = tuple(
            len(
                {
                    proposal.source
                    for proposal in proposals
                    if proposal.source != candidate.source
                    and _corner_distance(proposal, candidate) <= agreement
                }
            )
            for candidate in candidates
        )
        corroboration = corroborations[0] if corroborations else None

    selected = candidates[0] if candidates else None
    matrix = None
    inverse = None
    if selected is not None:
        color_image, matrix, inverse = _warp_candidate(
            source,
            selected,
            max_output_dim=max(640, int(max_output_dim)),
            cv2=cv2,
            np=np,
        )
    else:
        color_image = region.copy()

    return _DisplayStage(
        image=color_image,
        matrix=matrix,
        inverse=inverse,
        candidates=tuple(candidates),
        selected=selected,
        scale=scale,
        roi=normalized_roi,
        source_size=(source_width, source_height),
        corroboration=corroboration,
        corroborations=corroborations,
    )


# One camera import calls classify, OCR, grid, terminal and generate
# endpoints with the same upload.  These bounded caches let every stage reuse
# one display rectification and one board-candidate evaluation.
_CACHE_LOCK = threading.Lock()
_DISPLAY_CACHE: "OrderedDict[Hashable, _DisplayStage]" = OrderedDict()
_BOARD_CACHE: "OrderedDict[Hashable, tuple[_BoardEvaluation, ...]]" = OrderedDict()
_RESULT_CACHE: "OrderedDict[Hashable, PreparedPhoto]" = OrderedDict()


def _cache_limit() -> int:
    try:
        return max(0, int(os.environ.get("FLOW_CAMERA_PREP_CACHE_SIZE", "4")))
    except ValueError:
        return 4


def _cache_get(cache: "OrderedDict[Hashable, Any]", key: Optional[Hashable]) -> Any:
    if key is None:
        return None
    with _CACHE_LOCK:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _cache_put(cache: "OrderedDict[Hashable, Any]", key: Optional[Hashable], value: Any) -> None:
    limit = _cache_limit()
    if key is None or limit <= 0:
        return
    if cache is not _RESULT_CACHE:
        # Display and board stages hold one entry per shortlisted outline.
        limit *= 3
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def clear_preparation_cache() -> None:
    with _CACHE_LOCK:
        _DISPLAY_CACHE.clear()
        _BOARD_CACHE.clear()
        _RESULT_CACHE.clear()


def _copy_prepared(prepared: PreparedPhoto, *, cache_hit: bool) -> PreparedPhoto:
    info = copy.deepcopy(prepared.info)
    info["cache_hit"] = cache_hit
    return PreparedPhoto(
        color_image=prepared.color_image.copy(),
        geometry_image=prepared.geometry_image.copy(),
        glare_mask=prepared.glare_mask.copy(),
        info=info,
        display_image=prepared.display_image.copy() if prepared.display_image is not None else None,
        board_context_image=(
            prepared.board_context_image.copy() if prepared.board_context_image is not None else None
        ),
    )


DISPLAY_BEAM_WIDTH = 5
# Two outlines "agree" when their boards' corners are within this fraction of
# the photo diagonal of each other.
BOARD_AGREEMENT_FRACTION = 0.02


def _selection_margin(
    candidates: Sequence[PhotoQuadCandidate],
    selected: Optional[PhotoQuadCandidate],
    beam: Optional[list[dict[str, Any]]],
) -> Optional[float]:
    """Score gap between the chosen display outline and the best alternative."""

    if selected is None or selected.source == "manual":
        return None
    if beam and len(beam) >= 2:
        return None  # board agreement (consensus_votes) replaces score margins
    if len(candidates) >= 2:
        return round(float(candidates[0].score - candidates[1].score), 5)
    return None


def _board_cache_key(display_key: Optional[Hashable], index: int) -> Optional[Hashable]:
    return (display_key, "board", index) if display_key is not None else None


def _board_evaluations(
    display_image: Image.Image,
    cache_key: Optional[Hashable],
    *,
    cv2: Any,
    np: Any,
) -> tuple[_BoardEvaluation, ...]:
    evaluations = _cache_get(_BOARD_CACHE, cache_key)
    if evaluations is None:
        evaluations = tuple(_evaluate_board_candidates(display_image, cv2=cv2, np=np))
        _cache_put(_BOARD_CACHE, cache_key, evaluations)
    return evaluations


def _select_display_by_board_evidence(
    source: Image.Image,
    display: _DisplayStage,
    *,
    display_key: Optional[Hashable],
    max_output_dim: int,
    cv2: Any,
    np: Any,
) -> tuple[_DisplayStage, list[dict[str, Any]]]:
    """Carry the top display outlines through board detection and keep the best.

    A wrong screen outline distorts the board, so its lattice is irregular or
    has the wrong extent.  Scoring each shortlisted outline by the board it
    produces uses downstream evidence instead of trusting the contour score
    alone.  The result depends only on the photo, so every endpoint agrees.
    """

    beam: list[dict[str, Any]] = []
    stages: list[_DisplayStage] = []
    for index, candidate in enumerate(display.candidates[:DISPLAY_BEAM_WIDTH]):
        if index == 0:
            stage = display
        else:
            stage_key = (display_key, "display", index) if display_key is not None else None
            stage = _cache_get(_DISPLAY_CACHE, stage_key)
            if stage is None:
                warped, matrix, inverse = _warp_candidate(
                    source,
                    candidate,
                    max_output_dim=max(640, int(max_output_dim)),
                    cv2=cv2,
                    np=np,
                )
                stage = _DisplayStage(
                    image=warped,
                    matrix=matrix,
                    inverse=inverse,
                    candidates=display.candidates,
                    selected=candidate,
                    scale=display.scale,
                    roi=display.roi,
                    source_size=display.source_size,
                    corroboration=display.corroborations[index] if display.corroborations else None,
                    corroborations=display.corroborations,
                    selected_index=index,
                )
                _cache_put(_DISPLAY_CACHE, stage_key, stage)
        evaluations = _board_evaluations(stage.image, _board_cache_key(display_key, index), cv2=cv2, np=np)
        ranked = _rank_board_candidates(evaluations, None)
        confident = _board_ranking_is_confident(ranked)
        board_score = float(ranked[0][0]) if confident else 0.0
        # Where this outline would put the board, in photo coordinates.  An
        # outline with no confident board falls back to the whole display.
        board_quad_display = (
            np.asarray(ranked[0][1].corners, dtype=np.float64)
            if confident
            else np.float64([[0, 0], [stage.image.width - 1, 0], [stage.image.width - 1, stage.image.height - 1], [0, stage.image.height - 1]])
        )
        board_quad = (
            cv2.perspectiveTransform(board_quad_display.reshape(-1, 1, 2), np.asarray(stage.inverse, np.float64)).reshape(-1, 2)
            if stage.inverse is not None
            else board_quad_display
        )
        beam.append(
            {
                "index": index,
                "source": candidate.source,
                "display_score": round(float(candidate.score), 5),
                "board_score": round(board_score, 5),
                "corroboration": display.corroborations[index] if display.corroborations else 0,
                "board_quad": _order_points(board_quad.astype(np.float32), np).tolist(),
            }
        )
        stages.append(stage)
    if not beam:
        return display, beam
    # Different wrong outlines disagree about where the board is, while
    # outlines around the right screen (or the board itself) converge on the
    # same board.  Choose the outline whose board most others agree with.
    diagonal = math.hypot(*display.source_size)
    for entry in beam:
        entry["votes"] = sum(
            1
            for other in beam
            if other is not entry
            and float(np.mean(np.linalg.norm(np.asarray(entry["board_quad"]) - np.asarray(other["board_quad"]), axis=1)))
            <= BOARD_AGREEMENT_FRACTION * diagonal
        )
    # With no agreement at all the board score breaks the tie; keeping the
    # detector's top outline instead measured worse (55 vs 58 of 77 boards).
    # Either way consensus_votes == 0 sends the photo to review.
    best = max(range(len(beam)), key=lambda position: (beam[position]["votes"], beam[position]["board_score"], -position))
    for entry in beam:
        entry["board_quad"] = [[round(float(x), 1), round(float(y), 1)] for x, y in entry["board_quad"]]
    return stages[best], beam


def prepare_camera_photo(
    image: Image.Image,
    *,
    roi: Optional[tuple[int, int, int, int]] = None,
    manual_corners: Optional[Sequence[Sequence[float]]] = None,
    max_detection_dim: int = 1600,
    max_output_dim: int = 2600,
    candidate_limit: int = 5,
    detect_board: bool = False,
    expected_grid: Optional[tuple[int, int]] = None,
    cache_key: Optional[str] = None,
) -> PreparedPhoto:
    """Find and rectify a photographed display while preserving color evidence.

    ``cache_key`` identifies the decoded upload (for example its SHA-256);
    when given, repeated calls for the same photo reuse earlier work.
    """

    cv2, np = _imports()
    source = image.convert("RGB")
    source_width, source_height = source.size
    if source_width <= 0 or source_height <= 0:
        raise ValueError("camera photo has invalid dimensions")

    display_key: Optional[Hashable] = None
    result_key: Optional[Hashable] = None
    if cache_key:
        corners_key = (
            tuple(tuple(round(float(value), 3) for value in point) for point in manual_corners)
            if manual_corners is not None
            else None
        )
        display_key = (
            PHOTO_PREPROCESSING_VERSION,
            cache_key,
            (source_width, source_height),
            tuple(int(value) for value in roi) if roi is not None else None,
            corners_key,
            int(max_detection_dim),
            int(max_output_dim),
            int(candidate_limit),
        )
        result_key = (
            display_key,
            bool(detect_board),
            tuple(int(value) for value in expected_grid) if expected_grid is not None else None,
        )
        cached = _cache_get(_RESULT_CACHE, result_key)
        if cached is not None:
            return _copy_prepared(cached, cache_hit=True)

    timings: dict[str, float] = {}
    started = time.perf_counter()
    display = _cache_get(_DISPLAY_CACHE, display_key)
    if display is None:
        display = _compute_display_stage(
            source,
            roi=roi,
            manual_corners=manual_corners,
            max_detection_dim=max_detection_dim,
            max_output_dim=max_output_dim,
            candidate_limit=candidate_limit,
            cv2=cv2,
            np=np,
        )
        _cache_put(_DISPLAY_CACHE, display_key, display)
    beam: Optional[list[dict[str, Any]]] = None
    if detect_board and manual_corners is None and len(display.candidates) > 1:
        # Outline scores alone mislead (the photo frame and phone body often
        # outrank the screen), so every shortlisted outline is judged by the
        # board it produces.  Board results are cached per outline.
        display, beam = _select_display_by_board_evidence(
            source,
            display,
            display_key=display_key,
            max_output_dim=max_output_dim,
            cv2=cv2,
            np=np,
        )
    timings["display_ms"] = (time.perf_counter() - started) * 1000.0

    color_image = display.image
    matrix = display.matrix
    inverse = display.inverse
    selected = display.selected
    candidates = display.candidates
    scale = display.scale
    normalized_roi = display.roi

    display_rectified_size = {"width": color_image.width, "height": color_image.height}
    board_info: Optional[dict[str, Any]] = None
    board_context: Optional[Image.Image] = None
    cell_size: Optional[float] = None
    if detect_board:
        started = time.perf_counter()
        evaluations = _board_evaluations(
            color_image,
            _board_cache_key(display_key, display.selected_index),
            cv2=cv2,
            np=np,
        )
        board_image, _board_candidate, board_info, board_matrix, board_inverse = _rectified_board(
            color_image,
            evaluations,
            max_output_dim=max_output_dim,
            expected_grid=expected_grid,
            cv2=cv2,
            np=np,
        )
        timings["board_ms"] = (time.perf_counter() - started) * 1000.0
        if board_info.get("selected") is not None:
            lattice = board_info.get("lattice") or {}
            board_context = _board_context_view(
                color_image,
                [(point["x"], point["y"]) for point in board_info["selected"]["corners"]],
                lattice,
                board_image.size,
                cv2=cv2,
                np=np,
            )
            if float(lattice.get("pitch_x_fraction", 0.0)) > 0 and float(lattice.get("pitch_y_fraction", 0.0)) > 0:
                cell_size = min(
                    float(lattice["pitch_x_fraction"]) * board_image.width,
                    float(lattice["pitch_y_fraction"]) * board_image.height,
                )
            color_image = board_image
            if matrix is not None and board_matrix is not None:
                matrix = board_matrix @ matrix
            elif board_matrix is not None:
                matrix = board_matrix
            if inverse is not None and board_inverse is not None:
                inverse = inverse @ board_inverse
            elif board_inverse is not None:
                inverse = board_inverse

    started = time.perf_counter()
    geometry_image, glare_mask, quality = _quality_and_views(
        color_image,
        cell_size=cell_size,
        cv2=cv2,
        np=np,
    )
    timings["quality_ms"] = (time.perf_counter() - started) * 1000.0
    warnings = list(quality.get("warnings", []))
    if selected is None:
        warnings.insert(0, "No confident display quadrilateral was found; adjust the four screen corners manually.")
    elif selected.metrics.get("opposite_side_ratio", 1.0) < 0.42:
        warnings.append("The display is strongly foreshortened; a less oblique photo will be more reliable.")

    info: dict[str, Any] = {
        "version": PHOTO_PREPROCESSING_VERSION,
        "source_size": {"width": source_width, "height": source_height},
        "roi": normalized_roi,
        "detection_scale": round(float(scale), 8),
        "selected": selected.as_dict() if selected is not None else None,
        "selection_margin": _selection_margin(candidates, selected, beam),
        "selected_corroboration": display.corroboration,
        "display_beam": beam,
        "consensus_votes": (
            next((entry["votes"] for entry in beam if entry["index"] == display.selected_index), None)
            if beam and len(beam) >= 2
            else None
        ),
        "candidates": [candidate.as_dict() for candidate in candidates],
        "quality": {**quality, "warnings": warnings},
        "display_rectified_size": display_rectified_size,
        "rectified_size": {"width": color_image.width, "height": color_image.height},
        "board": board_info,
        "timings_ms": {key: round(value, 1) for key, value in timings.items()},
    }
    if matrix is not None and inverse is not None:
        info["homography"] = [[round(float(value), 10) for value in row] for row in matrix]
        info["inverse_homography"] = [[round(float(value), 10) for value in row] for row in inverse]
    prepared = PreparedPhoto(
        color_image=color_image,
        geometry_image=geometry_image,
        glare_mask=glare_mask,
        info=info,
        display_image=display.image,
        board_context_image=board_context,
    )
    _cache_put(_RESULT_CACHE, result_key, prepared)
    return _copy_prepared(prepared, cache_hit=False)


def _quad_tilt_degrees(corners: Sequence[Sequence[float]]) -> float:
    """Largest deviation of any quadrilateral side from the nearest image axis."""

    tilt = 0.0
    for index in range(4):
        start = corners[index]
        end = corners[(index + 1) % 4]
        angle = math.degrees(math.atan2(float(end[1]) - float(start[1]), float(end[0]) - float(start[0])))
        tilt = max(tilt, abs(((angle + 45.0) % 90.0) - 45.0))
    return tilt
