from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow running `python backend/app.py` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from flow_solver.puzzle import Puzzle
from flow_solver.solver import solve_puzzle
from backend.image_utils import (
    CropBox,
    apply_crop,
    auto_crop,
    auto_perspective,
    build_graph_terminals_from_node_placements,
    build_flow_text,
    build_graph_json,
    build_grid,
    classify_level_type,
    detect_circle_grid,
    detect_circle_terminals,
    detect_grid,
    detect_terminals_on_nodes,
    detect_wall_edges,
    detect_terminals,
    load_image,
)

MAX_TIMEOUT_MS = 1_000_000
SUPPORTED_LEVEL_GEOMETRIES = {"square", "hex", "circle", "graph", "cube", "star", "figure8"}
SUPPORTED_LEVEL_MODIFIERS = {"bridges", "warps", "walls"}
FLOW_LEVEL_GEOMETRIES = {"square", "hex", "circle"}
TOPOLOGY_LEVEL_GEOMETRIES = {"cube", "star", "figure8"}


def _crop_templates_dir() -> Path:
    return _repo_root() / "puzzles" / "templates" / "crop"


def _safe_template_id(name: str) -> str:
    out = []
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_"}:
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or "template"


def _load_crop_templates() -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    base = _crop_templates_dir()
    if not base.exists():
        return templates
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["id"] = path.stem
            templates.append(data)
        except Exception:
            continue
    return templates


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _examples_dir() -> Path:
    return _repo_root() / "examples" / "puzzles"


def _user_puzzles_dir() -> Path:
    return _repo_root() / "puzzles"


def _type_label(kind: str) -> str:
    if kind == "square":
        return "grid"
    if kind == "graph":
        return "free-form"
    return kind


def _normalize_meta(meta: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in meta.items():
        key = str(k).strip().lower()
        if isinstance(v, list):
            out[key] = ", ".join(str(x) for x in v)
        else:
            out[key] = str(v)
    return out


def _scan_flow_text(text: str) -> Tuple[str, bool, Dict[str, str], List[List[str]]]:
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    grid_lines: List[str] = []
    meta: Dict[str, str] = {}
    fill = True
    board_type = "square"

    for ln in lines:
        raw = ln.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            hdr = raw[1:].strip()
            if ":" in hdr:
                k, v = [x.strip() for x in hdr.split(":", 1)]
                kl = k.lower()
                if kl == "type":
                    board_type = v.lower()
                elif kl == "fill":
                    fill = v.lower() in {"1", "true", "yes", "y", "on"}
                else:
                    meta[kl] = v
                continue
            if len(raw) >= 2 and raw[1].isspace():
                continue
        grid_lines.append(ln)

    token_rows: List[List[str]] = []
    for row in grid_lines:
        if " " in row.strip():
            toks = [t for t in row.strip().split() if t]
        else:
            toks = list(row.strip())
        if toks:
            token_rows.append(toks)

    return board_type, fill, meta, token_rows


def _scan_json_text(text: str) -> Tuple[str, Dict[str, str], Dict[str, int]]:
    obj = json.loads(text)
    space = obj.get("space", {})
    kind = space.get("type", "graph")
    if kind == "graph":
        topo = str(space.get("topology", "")).strip().lower()
        if topo in {"cube", "star", "figure8"}:
            kind = topo
    meta_raw = obj.get("meta", {})
    meta = _normalize_meta(meta_raw) if isinstance(meta_raw, dict) else {}
    metrics: Dict[str, int] = {}

    if kind == "square":
        grid = space.get("grid", [])
        height = len(grid) if isinstance(grid, list) else 0
        width = max((len(r) for r in grid), default=0) if isinstance(grid, list) else 0
        metrics["width"] = width
        metrics["height"] = height
    elif kind in {"graph", "cube", "star", "figure8"}:
        nodes = space.get("nodes", {})
        edges = space.get("edges", [])
        metrics["nodes"] = len(nodes) if isinstance(nodes, dict) else 0
        metrics["edges"] = len(edges) if isinstance(edges, list) else 0

    return kind, meta, metrics


def _flow_metrics(kind: str, token_rows: List[List[str]]) -> Dict[str, int]:
    metrics: Dict[str, int] = {}
    height = len(token_rows)
    width = max((len(r) for r in token_rows), default=0)
    if kind in {"square", "hex"}:
        metrics["width"] = width
        metrics["height"] = height
    elif kind == "circle":
        metrics["rings"] = height
        metrics["sectors"] = width
    return metrics


def _type_size_from_text(text: str, *, name: str) -> Tuple[str, str]:
    if name.lower().endswith(".json"):
        kind, _meta, metrics = _scan_json_text(text)
        if kind == "square" and "width" in metrics and "height" in metrics:
            return kind, f"{metrics['width']}x{metrics['height']}"
        if kind in {"graph", "cube", "star", "figure8"} and metrics.get("nodes"):
            return kind, f"{metrics['nodes']} nodes"
        return kind, "graph"

    kind, _fill, _meta, token_rows = _scan_flow_text(text)
    metrics = _flow_metrics(kind, token_rows)
    if kind in {"square", "hex"} and "width" in metrics and "height" in metrics:
        return kind, f"{metrics['width']}x{metrics['height']}"
    if kind == "circle" and "rings" in metrics and "sectors" in metrics:
        return kind, f"{metrics['rings']}x{metrics['sectors']}"
    return kind, "unknown"


def _format_size_label(kind: str, metrics: Dict[str, int], nodes: Optional[int]) -> str:
    if kind in {"square", "hex"} and metrics.get("width") and metrics.get("height"):
        return f"{metrics['width']}x{metrics['height']}"
    if kind == "circle" and metrics.get("rings") and metrics.get("sectors"):
        return f"{metrics['rings']}x{metrics['sectors']}"
    if kind in {"graph", "cube", "star", "figure8"} and nodes is not None:
        return f"{nodes} nodes"
    return "-"


def _parse_puzzle(text: str, *, name: str) -> Puzzle:
    if name.lower().endswith(".json"):
        return Puzzle.from_json(text)
    return Puzzle.from_flow_text(text, source_name=name)


def _list_puzzle_files() -> List[Tuple[str, Path]]:
    files: List[Tuple[str, Path]] = []
    for source, base in (("examples", _examples_dir()), ("user", _user_puzzles_dir())):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if "templates" in path.parts:
                continue
            if path.is_file() and path.suffix.lower() in {".flow", ".json"}:
                files.append((source, path))
    return files


def _build_entry(path: Path, source: str) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    ext = path.suffix.lower()
    error: Optional[str] = None

    kind = "unknown"
    meta: Dict[str, str] = {}
    metrics: Dict[str, int] = {}
    if ext == ".flow":
        try:
            kind, _fill, meta, token_rows = _scan_flow_text(text)
            metrics = _flow_metrics(kind, token_rows)
        except Exception as e:
            error = f"Scan error: {e}"
    else:
        try:
            kind, meta, metrics = _scan_json_text(text)
        except Exception as e:
            error = f"Scan error: {e}"

    nodes = edges = tiles = colors = None
    try:
        puzzle = _parse_puzzle(text, name=path.name)
        nodes = len(puzzle.graph)
        edges = sum(1 for _ in puzzle.graph.edges())
        tiles = len(puzzle.tiles)
        colors = len(puzzle.terminals)
    except Exception as e:
        if error is None:
            error = f"Parse error: {e}"

    size_label = _format_size_label(kind, metrics, nodes)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    base = _examples_dir() if source == "examples" else _user_puzzles_dir()
    try:
        rel_path = str(path.relative_to(base))
    except Exception:
        rel_path = path.name
    return {
        "name": path.name,
        "path": str(path),
        "rel_path": rel_path,
        "source": source,
        "kind": kind,
        "type_label": _type_label(kind),
        "size_label": size_label,
        "metrics": metrics,
        "nodes": nodes,
        "edges": edges,
        "tiles": tiles,
        "colors": colors,
        "meta": meta,
        "error": error,
        "mtime": mtime,
    }


def _puzzle_path(source: str, name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Invalid puzzle path")
    if source == "examples":
        base = _examples_dir()
    elif source == "user":
        base = _user_puzzles_dir()
    else:
        raise HTTPException(status_code=404, detail="Unknown puzzle source")
    base = base.resolve()
    full = (base / rel).resolve()
    if not full.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid puzzle path")
    return full


def _graph_payload(puzzle: Puzzle) -> Dict[str, Any]:
    nodes = []
    for node_id, node in puzzle.graph.nodes.items():
        nodes.append(
            {
                "id": node_id,
                "x": float(node.pos[0]),
                "y": float(node.pos[1]),
                "z": float(node.pos[2]),
                "kind": node.kind,
                "data": dict(node.data),
            }
        )
    edges = [[u, v] for u, v in puzzle.graph.edges()]
    terminals = {c: [a, b] for c, (a, b) in puzzle.terminals.items()}
    payload = {"nodes": nodes, "edges": edges, "terminals": terminals, "tiles": puzzle.tiles}
    terminal_colors = {
        key: value
        for key, value in _terminal_color_map_from_meta(puzzle.meta).items()
        if key in terminals
    }
    if terminal_colors:
        payload["terminal_colors"] = terminal_colors
    return payload


_HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_HEX_SHORT_COLOR_RE = re.compile(r"^[0-9a-fA-F]{3}$")


def _normalize_hex_color(raw: Any) -> Optional[str]:
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        if value.startswith("#"):
            value = value[1:]
        if _HEX_SHORT_COLOR_RE.match(value):
            value = "".join(ch * 2 for ch in value)
        if _HEX_COLOR_RE.match(value):
            return f"#{value.lower()}"
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        try:
            r = max(0, min(255, int(round(float(raw[0])))))
            g = max(0, min(255, int(round(float(raw[1])))))
            b = max(0, min(255, int(round(float(raw[2])))))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return None
    return None


def _parse_terminal_color_pairs(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in re.split(r"[;,]", text):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" in chunk:
            key, value = chunk.split("=", 1)
        elif ":" in chunk:
            key, value = chunk.split(":", 1)
        else:
            bits = chunk.split()
            if len(bits) < 2:
                continue
            key, value = bits[0], bits[1]
        label = str(key).strip().upper()
        if not label:
            continue
        color = _normalize_hex_color(value)
        if color is None:
            continue
        out[label] = color
    return out


def _parse_terminal_color_map(raw: Any) -> Dict[str, str]:
    if isinstance(raw, dict):
        out: Dict[str, str] = {}
        for key, value in raw.items():
            label = str(key).strip().upper()
            if not label:
                continue
            color = _normalize_hex_color(value)
            if color is None:
                continue
            out[label] = color
        return out

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        parsed: Any = None
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
        if isinstance(parsed, dict):
            return _parse_terminal_color_map(parsed)
        if isinstance(parsed, list):
            out: Dict[str, str] = {}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                key = item.get("letter") if "letter" in item else item.get("color_id")
                value = item.get("color")
                if key is None:
                    continue
                label = str(key).strip().upper()
                color = _normalize_hex_color(value)
                if label and color is not None:
                    out[label] = color
            return out
        return _parse_terminal_color_pairs(text)

    return {}


def _terminal_color_map_from_meta(meta: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(meta, dict):
        return {}
    for key in ("terminal_colors", "terminal_colours"):
        for candidate_key, candidate_value in meta.items():
            if str(candidate_key).strip().lower() == key:
                return _parse_terminal_color_map(candidate_value)
    return {}


def _parse_crop_box(
    crop_x: Optional[int],
    crop_y: Optional[int],
    crop_width: Optional[int],
    crop_height: Optional[int],
) -> Optional[CropBox]:
    if crop_x is None or crop_y is None or crop_width is None or crop_height is None:
        return None
    if crop_width <= 0 or crop_height <= 0:
        return None
    return CropBox(int(crop_x), int(crop_y), int(crop_width), int(crop_height))


def _image_meta(
    *,
    image_name: str,
    image_size: Tuple[int, int],
    crop: Optional[CropBox],
    base: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    meta = dict(base or {})
    meta["source_image"] = image_name
    meta["image_size"] = f"{image_size[0]}x{image_size[1]}"
    if crop:
        meta["crop"] = f"{crop.x},{crop.y},{crop.width},{crop.height}"
        meta["crop_size"] = f"{crop.width}x{crop.height}"
    meta.setdefault("generated", "image_import")
    return meta


def _maybe_perspective(image: Image.Image, enabled: bool) -> Tuple[Image.Image, Optional[Dict[str, Any]]]:
    if not enabled:
        return image, None
    return auto_perspective(image)


def _normalize_level_geometry(raw: str, *, default: str = "square") -> str:
    val = str(raw or "").strip().lower()
    if val in SUPPORTED_LEVEL_GEOMETRIES:
        return val
    return default


def _normalize_level_modifiers(raw: Any) -> List[str]:
    if raw is None:
        return []
    out: List[str] = []
    if isinstance(raw, str):
        items = [part.strip().lower() for part in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(part).strip().lower() for part in raw]
    else:
        items = [str(raw).strip().lower()]
    for item in items:
        if item in SUPPORTED_LEVEL_MODIFIERS and item not in out:
            out.append(item)
    return out


def _can_emit_flow(geometry: str, modifiers: List[str]) -> bool:
    if geometry not in FLOW_LEVEL_GEOMETRIES:
        return False
    if not modifiers:
        return True
    # Current .flow format only has explicit support for square bridges.
    return geometry == "square" and set(modifiers) <= {"bridges"}


def _recommended_target_type(geometry: str, modifiers: List[str]) -> str:
    geom = _normalize_level_geometry(geometry)
    if geom in TOPOLOGY_LEVEL_GEOMETRIES:
        return geom
    return geom if _can_emit_flow(geom, modifiers) else "graph"


def _level_type_id(geometry: str, modifiers: List[str]) -> str:
    if not modifiers:
        return geometry
    return f"{geometry}:{'+'.join(modifiers)}"


def _build_level_type_candidate(
    geometry: str,
    modifiers: List[str],
    *,
    confidence: float,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    geom = _normalize_level_geometry(geometry)
    mods = _normalize_level_modifiers(modifiers)
    can_emit = _can_emit_flow(geom, mods)
    target = _recommended_target_type(geom, mods)
    candidate = {
        "id": _level_type_id(geom, mods),
        "geometry": geom,
        "modifiers": mods,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "can_emit_flow": can_emit,
        "recommended_target_type": target,
        "recommended_output_format": "flow" if can_emit else "json",
    }
    if reason:
        candidate["reason"] = reason
    return candidate


def _build_level_type_payload(
    geometry: str,
    modifiers: List[str],
    *,
    confidence: float,
    source: str,
    candidates: Optional[List[Dict[str, Any]]] = None,
    notes: Optional[List[str]] = None,
    signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    geom = _normalize_level_geometry(geometry)
    mods = _normalize_level_modifiers(modifiers)
    can_emit = _can_emit_flow(geom, mods)
    target = _recommended_target_type(geom, mods)
    payload: Dict[str, Any] = {
        "id": _level_type_id(geom, mods),
        "geometry": geom,
        "modifiers": mods,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "source": source,
        "can_emit_flow": can_emit,
        "recommended_target_type": target,
        "recommended_output_format": "flow" if can_emit else "json",
        "candidates": candidates or [],
        "notes": notes or [],
    }
    if signals is not None:
        payload["signals"] = signals
    return payload


def _parse_level_type_payload(raw: Dict[str, Any], *, source_fallback: str = "manual") -> Dict[str, Any]:
    geometry = _normalize_level_geometry(str(raw.get("geometry", "square")))
    modifiers = _normalize_level_modifiers(raw.get("modifiers"))
    confidence = float(raw.get("confidence", 0.65))
    source = str(raw.get("source", source_fallback))

    raw_candidates = raw.get("candidates")
    candidates: List[Dict[str, Any]] = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates[:4]:
            if not isinstance(item, dict):
                continue
            candidates.append(
                _build_level_type_candidate(
                    str(item.get("geometry", geometry)),
                    _normalize_level_modifiers(item.get("modifiers")),
                    confidence=float(item.get("confidence", 0.0)),
                    reason=str(item.get("reason")) if item.get("reason") is not None else None,
                )
            )

    notes_raw = raw.get("notes")
    notes = [str(x) for x in notes_raw] if isinstance(notes_raw, list) else []
    signals = raw.get("signals") if isinstance(raw.get("signals"), dict) else None
    return _build_level_type_payload(
        geometry,
        modifiers,
        confidence=confidence,
        source=source,
        candidates=candidates,
        notes=notes,
        signals=signals,
    )


def _classify_level_type_payload(
    image: Image.Image,
    *,
    threshold: int,
    line_threshold: float,
    invert: bool,
    file_hint: Optional[str],
) -> Dict[str, Any]:
    detection = classify_level_type(
        image,
        threshold=threshold,
        line_threshold=line_threshold,
        invert=invert,
        file_hint=file_hint,
    )
    candidates = [
        _build_level_type_candidate(
            cand.geometry,
            list(cand.modifiers),
            confidence=cand.confidence,
            reason=cand.reason,
        )
        for cand in detection.candidates
    ]
    return _build_level_type_payload(
        detection.geometry,
        list(detection.modifiers),
        confidence=detection.confidence,
        source="classifier",
        candidates=candidates,
        notes=list(detection.warnings),
        signals=dict(detection.signals),
    )


def _grid_wrap_edges(width: int, height: int) -> List[Tuple[str, str]]:
    edges: List[Tuple[str, str]] = []
    if width > 2:
        for y in range(height):
            edges.append((f"0,{y}", f"{width - 1},{y}"))
    if height > 2:
        for x in range(width):
            edges.append((f"{x},0", f"{x},{height - 1}"))
    return edges


def _candidate_confidence(raw: Any) -> float:
    if not isinstance(raw, dict):
        return 0.0
    try:
        return max(0.0, float(raw.get("confidence", 0.0)))
    except Exception:
        return 0.0


def _best_topology_candidate(level_type: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    raw_candidates = level_type.get("candidates")
    if not isinstance(raw_candidates, list):
        return None
    best: Optional[Tuple[str, float]] = None
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        geom = _normalize_level_geometry(str(raw.get("geometry", "")), default="")
        if geom not in TOPOLOGY_LEVEL_GEOMETRIES:
            continue
        conf = _candidate_confidence(raw)
        if best is None or conf > best[1]:
            best = (geom, conf)
    return best


def _parse_edge_pairs(raw: Any, *, field: str) -> List[Tuple[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list of [u, v] pairs")
    out: List[Tuple[str, str]] = []
    for idx, pair in enumerate(raw):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{field}[{idx}] must be [u, v]")
        u = str(pair[0]).strip()
        v = str(pair[1]).strip()
        if not u or not v:
            raise ValueError(f"{field}[{idx}] contains an empty endpoint")
        if u == v:
            raise ValueError(f"{field}[{idx}] contains a self-loop")
        out.append((u, v))
    return out


def _parse_edge_overrides_payload(raw: Any) -> Dict[str, List[Tuple[str, str]]]:
    if raw is None:
        return {"add": [], "remove": [], "warps": [], "walls": []}
    if not isinstance(raw, dict):
        raise ValueError("edge_overrides_json must be a JSON object")
    return {
        "add": _parse_edge_pairs(raw.get("add"), field="edge_overrides.add"),
        "remove": _parse_edge_pairs(raw.get("remove"), field="edge_overrides.remove"),
        "warps": _parse_edge_pairs(raw.get("warps"), field="edge_overrides.warps"),
        "walls": _parse_edge_pairs(raw.get("walls"), field="edge_overrides.walls"),
    }


def _apply_flow_metadata(text: str, meta_updates: Dict[str, str], *, drop_empty: bool) -> str:
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    directives: Dict[str, str] = {}
    rest: List[str] = []

    for ln in lines:
        raw = ln.strip()
        if not raw:
            rest.append(ln)
            continue
        if raw.startswith("#"):
            hdr = raw[1:].strip()
            if ":" in hdr:
                k, v = [x.strip() for x in hdr.split(":", 1)]
                directives[k.lower()] = v
                continue
            if len(raw) >= 2 and raw[1].isspace():
                rest.append(ln)
                continue
        rest.append(ln)

    for key, value in meta_updates.items():
        if drop_empty and not value:
            directives.pop(key, None)
        else:
            directives[key] = value

    header: List[str] = []
    if "type" in directives:
        header.append(f"# type: {directives.pop('type')}")
    if "fill" in directives:
        header.append(f"# fill: {directives.pop('fill')}")
    for key in sorted(directives):
        header.append(f"# {key}: {directives[key]}")

    merged = header + rest
    return "\n".join(merged).rstrip() + "\n"


def _apply_json_metadata(text: str, meta_updates: Dict[str, str], *, drop_empty: bool) -> str:
    obj = json.loads(text)
    meta_raw = obj.get("meta", {})
    meta: Dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}

    for key, value in meta_updates.items():
        if drop_empty and not value:
            meta.pop(key, None)
        else:
            meta[key] = value

    obj["meta"] = meta
    return json.dumps(obj, indent=2, sort_keys=True)


class ParseRequest(BaseModel):
    name: str = Field(default="puzzle.flow")
    text: str
    fill: Optional[bool] = None


class SolveRequest(ParseRequest):
    solver: str = Field(default="z3")
    timeout_ms: Optional[int] = Field(default=30_000, ge=1, le=MAX_TIMEOUT_MS)


class SavePuzzleRequest(BaseModel):
    name: str
    text: str
    overwrite: bool = False
    metadata: Dict[str, str] = Field(default_factory=dict)
    drop_empty: bool = True


class RenamePuzzleRequest(BaseModel):
    source: str
    old_name: str
    new_name: str


class CropTemplateRequest(BaseModel):
    name: str
    image_width: int
    image_height: int
    crop: Dict[str, int]
    note: Optional[str] = None
    preview_png_base64: Optional[str] = None
    pipeline: Optional[Dict[str, Any]] = None


app = FastAPI(title="Flow Solver API", version="0.1.0")

cors_raw = os.environ.get("CORS_ORIGINS", "*")
cors_list = [c.strip() for c in cors_raw.split(",") if c.strip()]
allow_all_cors = cors_raw.strip() == "*" or "*" in cors_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_cors else (cors_list or ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/puzzles")
def list_puzzles() -> Dict[str, Any]:
    entries = [_build_entry(path, source) for source, path in _list_puzzle_files()]
    return {"entries": entries}


@app.post("/puzzles/save")
def save_puzzle(req: SavePuzzleRequest) -> Dict[str, Any]:
    safe_name = Path(req.name).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid puzzle name")
    ext = Path(safe_name).suffix.lower()
    if ext not in {".flow", ".json"}:
        raise HTTPException(status_code=400, detail="Puzzle name must end with .flow or .json")

    final_text = req.text
    if req.metadata:
        if ext == ".json":
            final_text = _apply_json_metadata(req.text, req.metadata, drop_empty=req.drop_empty)
        else:
            final_text = _apply_flow_metadata(req.text, req.metadata, drop_empty=req.drop_empty)
    # Validate terminals (each color must appear exactly twice).
    try:
        _parse_puzzle(final_text, name=safe_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Puzzle validation failed: {e}") from e
    kind, size = _type_size_from_text(final_text, name=safe_name)
    dest_dir = _user_puzzles_dir() / kind / size
    dest = dest_dir / safe_name
    if dest.exists() and not req.overwrite:
        raise HTTPException(status_code=409, detail="Puzzle already exists for that type/size")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(final_text, encoding="utf-8")
    return {"path": str(dest), "text": final_text}


@app.post("/puzzles/rename")
def rename_puzzle(req: RenamePuzzleRequest) -> Dict[str, Any]:
    if req.source != "user":
        raise HTTPException(status_code=400, detail="Only user puzzles can be renamed.")
    old_path = _puzzle_path(req.source, req.old_name)
    if not old_path.exists():
        raise HTTPException(status_code=404, detail="Puzzle not found")
    new_name = Path(req.new_name).name
    if not new_name:
        raise HTTPException(status_code=400, detail="Invalid new name")
    ext = Path(new_name).suffix.lower()
    if ext not in {".flow", ".json"}:
        raise HTTPException(status_code=400, detail="Puzzle name must end with .flow or .json")
    new_path = old_path.parent / new_name
    if new_path.exists():
        raise HTTPException(status_code=409, detail="Puzzle with that name already exists for this type/size")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    return {"old_path": str(old_path), "new_path": str(new_path)}


@app.delete("/puzzles/{source}/{name:path}")
def delete_puzzle(source: str, name: str) -> Dict[str, Any]:
    if source != "user":
        raise HTTPException(status_code=400, detail="Only user puzzles can be deleted.")
    path = _puzzle_path(source, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Puzzle not found")
    path.unlink()
    thumb_path = _thumbnail_path(source, name)
    if thumb_path.exists():
        thumb_path.unlink()
    return {"deleted": True, "path": str(path)}


@app.get("/templates/crop")
def list_crop_templates() -> Dict[str, Any]:
    return {"templates": _load_crop_templates()}


@app.post("/templates/crop")
def save_crop_template(req: CropTemplateRequest) -> Dict[str, Any]:
    if req.image_width <= 0 or req.image_height <= 0:
        raise HTTPException(status_code=400, detail="Invalid image dimensions")
    crop = req.crop
    for key in ("x", "y", "width", "height"):
        if key not in crop:
            raise HTTPException(status_code=400, detail="Invalid crop")
    if crop["width"] <= 0 or crop["height"] <= 0:
        raise HTTPException(status_code=400, detail="Invalid crop size")

    template_id = _safe_template_id(req.name)
    base = _crop_templates_dir()
    base.mkdir(parents=True, exist_ok=True)
    existing = [t for t in _load_crop_templates() if t.get("name") == req.name or t.get("id") == template_id]
    if existing:
        raise HTTPException(status_code=409, detail="Template already exists")

    crop_pct = {
        "x": crop["x"] / req.image_width,
        "y": crop["y"] / req.image_height,
        "width": crop["width"] / req.image_width,
        "height": crop["height"] / req.image_height,
    }
    data = {
        "name": req.name,
        "image_width": req.image_width,
        "image_height": req.image_height,
        "crop": crop,
        "crop_pct": crop_pct,
        "note": req.note,
        "created_at": time.time(),
        "has_preview": bool(req.preview_png_base64),
        "preview_png_base64": req.preview_png_base64,
        "pipeline": req.pipeline,
    }
    path = base / f"{template_id}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"id": template_id}


@app.get("/templates/crop/{template_id}/preview")
def get_crop_template_preview(template_id: str):
    base = _crop_templates_dir()
    path = base / f"{_safe_template_id(template_id)}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Template not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    preview = data.get("preview_png_base64")
    if not preview:
        raise HTTPException(status_code=404, detail="Template preview not found")
    try:
        raw = base64.b64decode(preview.encode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid preview data: {e}") from e
    return Response(raw, media_type="image/png")


@app.delete("/templates/crop/{template_id}")
def delete_crop_template(template_id: str) -> Dict[str, Any]:
    base = _crop_templates_dir()
    path = base / f"{_safe_template_id(template_id)}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Template not found")
    path.unlink()
    return {"deleted": True}


@app.post("/parse")
def parse_puzzle(req: ParseRequest) -> Dict[str, Any]:
    try:
        if req.name.lower().endswith(".json"):
            kind, meta, metrics = _scan_json_text(req.text)
        else:
            kind, _fill, meta, token_rows = _scan_flow_text(req.text)
            metrics = _flow_metrics(kind, token_rows)

        puzzle = _parse_puzzle(req.text, name=req.name)
        if req.fill is not None:
            puzzle = replace(puzzle, fill=req.fill)
        counts = {
            "nodes": len(puzzle.graph),
            "edges": sum(1 for _ in puzzle.graph.edges()),
            "tiles": len(puzzle.tiles),
            "colors": len(puzzle.terminals),
            "fill": puzzle.fill,
        }
        size_label = _format_size_label(kind, metrics, counts["nodes"])
        return {
            "kind": kind,
            "type_label": _type_label(kind),
            "size_label": size_label,
            "metrics": metrics,
            "counts": counts,
            "meta": meta,
            "terminals": {c: [a, b] for c, (a, b) in puzzle.terminals.items()},
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/solve")
def solve(req: SolveRequest) -> Dict[str, Any]:
    try:
        puzzle = _parse_puzzle(req.text, name=req.name)
        if req.fill is not None:
            puzzle = replace(puzzle, fill=req.fill)
        res = solve_puzzle(puzzle, solver=req.solver, timeout_ms=req.timeout_ms)
        node_color = {k: v for k, v in res.node_color.items()}
        return {
            "node_color": node_color,
            "paths": {c: path for c, path in res.paths.items()},
            "graph": _graph_payload(puzzle),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/graph")
def build_graph(req: ParseRequest) -> Dict[str, Any]:
    try:
        puzzle = _parse_puzzle(req.text, name=req.name)
        if req.fill is not None:
            puzzle = replace(puzzle, fill=req.fill)
        return {"graph": _graph_payload(puzzle)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/puzzles/{source}/{name:path}/graph")
def get_puzzle_graph(source: str, name: str) -> Dict[str, Any]:
    path = _puzzle_path(source, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Puzzle not found")
    text = path.read_text(encoding="utf-8")
    puzzle = _parse_puzzle(text, name=path.name)
    return {"graph": _graph_payload(puzzle)}


THUMBNAIL_VERSION = "v2-terminal-colors"


def _thumbnail_path(source: str, name: str) -> Path:
    safe_name = str(name).replace("/", "__")
    safe = f"{source}__{safe_name}__{THUMBNAIL_VERSION}.png"
    return _repo_root() / "out" / "thumbs" / safe


def _render_thumbnail(puzzle: Puzzle, *, size: Tuple[int, int] = (240, 180)) -> bytes:
    from PIL import Image, ImageDraw

    palette = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]

    def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

    width, height = size
    img = Image.new("RGB", (width, height), (15, 17, 22))
    draw = ImageDraw.Draw(img)
    nodes = list(puzzle.graph.nodes.values())
    if not nodes:
        return img.tobytes()

    xs = [n.pos[0] for n in nodes]
    ys = [n.pos[1] for n in nodes]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 12
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0

    def map_x(x: float) -> float:
        return pad + (x - min_x) / span_x * (width - pad * 2)

    def map_y(y: float) -> float:
        return pad + (max_y - y) / span_y * (height - pad * 2)

    # edges
    for u, v in puzzle.graph.edges():
        pu = puzzle.graph.nodes[u].pos
        pv = puzzle.graph.nodes[v].pos
        draw.line((map_x(pu[0]), map_y(pu[1]), map_x(pv[0]), map_y(pv[1])), fill=(110, 110, 110))

    # nodes
    terminals = puzzle.terminal_nodes()
    colors = puzzle.all_colors()
    terminal_color_overrides = _terminal_color_map_from_meta(puzzle.meta)
    color_to_rgb: Dict[str, Tuple[int, int, int]] = {}
    for i, c in enumerate(colors):
        override = terminal_color_overrides.get(c)
        if override:
            color_to_rgb[c] = hex_to_rgb(override)
        else:
            color_to_rgb[c] = hex_to_rgb(palette[i % len(palette)])
    for node_id, node in puzzle.graph.nodes.items():
        cx, cy = map_x(node.pos[0]), map_y(node.pos[1])
        r = 4 if node_id in terminals else 2
        if node_id in terminals:
            color = color_to_rgb.get(terminals[node_id], (255, 82, 82))
        else:
            color = (200, 200, 200)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/puzzles/{source}/{name:path}/thumbnail")
def get_puzzle_thumbnail(source: str, name: str):
    path = _puzzle_path(source, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Puzzle not found")

    thumb_path = _thumbnail_path(source, name)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        puzzle_mtime = path.stat().st_mtime
        if thumb_path.exists() and thumb_path.stat().st_mtime >= puzzle_mtime:
            return Response(thumb_path.read_bytes(), media_type="image/png")
    except OSError:
        pass

    text = path.read_text(encoding="utf-8")
    puzzle = _parse_puzzle(text, name=path.name)
    png = _render_thumbnail(puzzle)
    thumb_path.write_bytes(png)
    return Response(png, media_type="image/png")


@app.get("/puzzles/{source}/{name:path}")
def get_puzzle(source: str, name: str) -> Dict[str, Any]:
    path = _puzzle_path(source, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Puzzle not found")
    text = path.read_text(encoding="utf-8")
    entry = _build_entry(path, source)
    return {"name": path.name, "text": text, "entry": entry}


@app.post("/image/crop/auto")
async def image_auto_crop(
    file: UploadFile = File(...),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    invert: bool = Form(False),
    padding: int = Form(6),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    seed_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    image_for_crop = apply_crop(image, seed_crop)
    crop = auto_crop(image_for_crop, threshold=threshold, invert=invert, padding=padding)
    if crop is not None and seed_crop is not None:
        crop = CropBox(
            x=seed_crop.x + crop.x,
            y=seed_crop.y + crop.y,
            width=crop.width,
            height=crop.height,
        )
    elif crop is None and seed_crop is not None:
        # If refinement within the seed failed, retry on the full image.
        crop = auto_crop(image, threshold=threshold, invert=invert, padding=padding)
        if crop is None:
            # Last-resort behavior keeps the user-provided/templated seed.
            crop = seed_crop
    if crop is None:
        return {
            "crop": None,
            "image_size": {"width": image.width, "height": image.height},
            "seed_crop": (
                {"x": seed_crop.x, "y": seed_crop.y, "width": seed_crop.width, "height": seed_crop.height}
                if seed_crop
                else None
            ),
            "message": "No crop detected.",
        }
    return {
        "crop": {"x": crop.x, "y": crop.y, "width": crop.width, "height": crop.height},
        "image_size": {"width": image.width, "height": image.height},
        "seed_crop": (
            {"x": seed_crop.x, "y": seed_crop.y, "width": seed_crop.width, "height": seed_crop.height}
            if seed_crop
            else None
        ),
    }


@app.post("/image/classify")
async def image_classify(
    file: UploadFile = File(...),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    line_threshold: float = Form(0.6),
    invert: bool = Form(False),
    perspective: bool = Form(False),
    level_hint: Optional[str] = Form(None),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if crop is None:
        inferred_crop = auto_crop(
            image,
            threshold=threshold,
            invert=invert,
            padding=max(8, int(min(image.width, image.height) * 0.01)),
        )
        if inferred_crop is not None:
            crop = inferred_crop
            auto_crop_info = {
                "applied": True,
                "source": "auto",
                "x": crop.x,
                "y": crop.y,
                "width": crop.width,
                "height": crop.height,
            }
    cropped = apply_crop(image, crop)
    warped, perspective_info = _maybe_perspective(cropped, perspective)

    hint = level_hint if level_hint else file.filename
    level_type = _classify_level_type_payload(
        warped,
        threshold=threshold,
        line_threshold=line_threshold,
        invert=invert,
        file_hint=hint,
    )
    return {
        "level_type": level_type,
        "candidates": level_type.get("candidates", []),
        "warnings": level_type.get("notes", []),
        "signals": level_type.get("signals", {}),
        "image_size": {"width": image.width, "height": image.height},
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/grid/detect")
async def image_grid_detect(
    file: UploadFile = File(...),
    target_type: str = Form("square"),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    line_threshold: float = Form(0.6),
    invert: bool = Form(False),
    perspective: bool = Form(False),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if crop is None:
        inferred_crop = auto_crop(
            image,
            threshold=threshold,
            invert=invert,
            padding=max(8, int(min(image.width, image.height) * 0.01)),
        )
        if inferred_crop is not None:
            crop = inferred_crop
            auto_crop_info = {
                "applied": True,
                "source": "auto",
                "x": crop.x,
                "y": crop.y,
                "width": crop.width,
                "height": crop.height,
            }
    cropped = apply_crop(image, crop)
    warped, perspective_info = _maybe_perspective(cropped, perspective)
    raw_target = str(target_type or "square").strip().lower()
    normalized_target = _normalize_level_geometry(raw_target, default="square")

    if normalized_target == "circle":
        circle_grid, circle_info = detect_circle_grid(warped, min_sectors=3, max_sectors=32)
        if circle_grid is None:
            return {
                "grid": None,
                "image_size": {"width": image.width, "height": image.height},
                "perspective": perspective_info,
                "auto_crop": auto_crop_info,
                "circle": circle_info,
                "message": "Circle grid detection failed.",
            }
        return {
            "grid": {
                "rows": circle_grid.rings,
                "cols": circle_grid.sectors,
                "vertical_lines": circle_grid.sectors,
                "horizontal_lines": circle_grid.rings,
                "mode": "circle",
            },
            "circle": circle_info,
            "image_size": {"width": image.width, "height": image.height},
            "perspective": perspective_info,
            "auto_crop": auto_crop_info,
        }

    grid = detect_grid(warped, threshold=threshold, line_threshold=line_threshold, invert=invert)
    if grid is None and raw_target in {"auto", ""}:
        circle_grid, circle_info = detect_circle_grid(warped, min_sectors=3, max_sectors=32)
        if circle_grid is not None:
            return {
                "grid": {
                    "rows": circle_grid.rings,
                    "cols": circle_grid.sectors,
                    "vertical_lines": circle_grid.sectors,
                    "horizontal_lines": circle_grid.rings,
                    "mode": "circle",
                },
                "circle": circle_info,
                "image_size": {"width": image.width, "height": image.height},
                "perspective": perspective_info,
                "auto_crop": auto_crop_info,
            }
    if grid is None:
        return {
            "grid": None,
            "image_size": {"width": image.width, "height": image.height},
            "perspective": perspective_info,
            "auto_crop": auto_crop_info,
            "message": "Grid detection failed.",
        }
    return {
        "grid": {
            "rows": grid.rows,
            "cols": grid.cols,
            "vertical_lines": grid.vertical_lines,
            "horizontal_lines": grid.horizontal_lines,
            "mode": "rect",
        },
        "image_size": {"width": image.width, "height": image.height},
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/terminals/detect")
async def image_terminals_detect(
    file: UploadFile = File(...),
    target_type: str = Form("square"),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    rows: int = Form(...),
    cols: int = Form(...),
    sat_threshold: float = Form(30.0),
    brightness_min: float = Form(30.0),
    brightness_max: float = Form(230.0),
    margin_ratio: float = Form(0.15),
    cluster_threshold: float = Form(60.0),
    bg_threshold: float = Form(40.0),
    perspective: bool = Form(False),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if crop is None:
        inferred_crop = auto_crop(
            image,
            threshold=230,
            invert=False,
            padding=max(8, int(min(image.width, image.height) * 0.01)),
        )
        if inferred_crop is not None:
            crop = inferred_crop
            auto_crop_info = {
                "applied": True,
                "source": "auto",
                "x": crop.x,
                "y": crop.y,
                "width": crop.width,
                "height": crop.height,
            }
    cropped = apply_crop(image, crop)
    warped, perspective_info = _maybe_perspective(cropped, perspective)
    normalized_target = _normalize_level_geometry(str(target_type or "square"), default="square")
    if normalized_target == "circle":
        circle_grid, circle_info = detect_circle_grid(
            warped,
            min_sectors=max(3, min(cols, 12)),
            max_sectors=max(cols + 6, 24),
        )
        placements, info = detect_circle_terminals(
            warped,
            rings=rows,
            sectors=cols,
            sat_threshold=sat_threshold,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            margin_ratio=margin_ratio,
            cluster_threshold=cluster_threshold,
            bg_threshold=bg_threshold,
            circle_grid=circle_grid,
        )
        if circle_info:
            info["circle_detection"] = circle_info
    else:
        placements, info = detect_terminals(
            warped,
            rows=rows,
            cols=cols,
            sat_threshold=sat_threshold,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            margin_ratio=margin_ratio,
            cluster_threshold=cluster_threshold,
            bg_threshold=bg_threshold,
        )
    return {
        "terminals": [
            {
                "row": t.row,
                "col": t.col,
                "letter": t.letter,
                "color": [round(c, 2) for c in t.color],
            }
            for t in placements
        ],
        "info": info,
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/generate")
async def image_generate(
    file: UploadFile = File(...),
    target_type: str = Form("auto"),
    grid_width: Optional[int] = Form(None),
    grid_height: Optional[int] = Form(None),
    graph_layout: str = Form("grid"),
    graph_nodes: int = Form(10),
    auto_terminals: bool = Form(True),
    auto_classify: bool = Form(True),
    level_type_json: Optional[str] = Form(None),
    edge_overrides_json: Optional[str] = Form(None),
    metadata_json: Optional[str] = Form(None),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    line_threshold: float = Form(0.6),
    invert: bool = Form(False),
    sat_threshold: float = Form(30.0),
    brightness_min: float = Form(30.0),
    brightness_max: float = Form(230.0),
    margin_ratio: float = Form(0.15),
    cluster_threshold: float = Form(60.0),
    bg_threshold: float = Form(40.0),
    perspective: bool = Form(False),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if crop is None:
        inferred_crop = auto_crop(
            image,
            threshold=threshold,
            invert=invert,
            padding=max(8, int(min(image.width, image.height) * 0.01)),
        )
        if inferred_crop is not None:
            crop = inferred_crop
            auto_crop_info = {
                "applied": True,
                "source": "auto",
                "x": crop.x,
                "y": crop.y,
                "width": crop.width,
                "height": crop.height,
            }
    cropped = apply_crop(image, crop)
    warped, perspective_info = _maybe_perspective(cropped, perspective)

    extra_meta: Dict[str, str] = {}
    if metadata_json:
        try:
            raw = json.loads(metadata_json)
            if isinstance(raw, dict):
                extra_meta = {str(k): str(v) for k, v in raw.items()}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid metadata_json: {e}") from e

    manual_edge_overrides = {"add": [], "remove": [], "warps": [], "walls": []}
    if edge_overrides_json:
        try:
            manual_raw = json.loads(edge_overrides_json)
            manual_edge_overrides = _parse_edge_overrides_payload(manual_raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid edge_overrides_json: {e}") from e

    meta = _image_meta(
        image_name=file.filename or "image",
        image_size=(image.width, image.height),
        crop=crop,
        base=extra_meta,
    )

    detection_info: Dict[str, Any] = {"perspective": perspective_info, "auto_crop": auto_crop_info}
    requested_target = str(target_type or "auto").strip().lower() or "auto"
    classification_warnings: List[str] = []

    if level_type_json:
        try:
            raw_level = json.loads(level_type_json)
            if not isinstance(raw_level, dict):
                raise ValueError("level_type_json must be a JSON object")
            level_type = _parse_level_type_payload(raw_level, source_fallback="hint")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid level_type_json: {e}") from e
    elif auto_classify or requested_target == "auto":
        level_type = _classify_level_type_payload(
            warped,
            threshold=threshold,
            line_threshold=line_threshold,
            invert=invert,
            file_hint=file.filename,
        )
    else:
        manual_geometry = _normalize_level_geometry(requested_target, default="square")
        base_candidate = _build_level_type_candidate(
            manual_geometry,
            [],
            confidence=1.0,
            reason="manual target selection",
        )
        level_type = _build_level_type_payload(
            manual_geometry,
            [],
            confidence=1.0,
            source="manual",
            candidates=[base_candidate],
        )

    if requested_target == "auto":
        target_used = str(level_type.get("recommended_target_type", "square"))
    elif requested_target in SUPPORTED_LEVEL_GEOMETRIES:
        target_used = requested_target
    else:
        raise HTTPException(status_code=400, detail=f"Unknown target_type: {target_type}")

    auto_target_adjustment: Optional[Dict[str, Any]] = None
    if requested_target == "auto" and target_used in FLOW_LEVEL_GEOMETRIES:
        top_conf = 0.0
        raw_candidates = level_type.get("candidates")
        if isinstance(raw_candidates, list) and raw_candidates:
            top_conf = _candidate_confidence(raw_candidates[0])
        best_topology = _best_topology_candidate(level_type)
        level_signals = level_type.get("signals")
        has_grid_signal = isinstance(level_signals, dict) and isinstance(level_signals.get("grid"), dict)
        if (
            not has_grid_signal
            and top_conf <= 0.34
            and best_topology is not None
            and best_topology[1] >= max(0.12, top_conf * 0.45)
            and best_topology[0] in TOPOLOGY_LEVEL_GEOMETRIES
        ):
            previous_target = target_used
            target_used = best_topology[0]
            auto_target_adjustment = {
                "from": previous_target,
                "to": target_used,
                "reason": "weak_grid_signal",
                "top_confidence": round(float(top_conf), 4),
                "topology_confidence": round(float(best_topology[1]), 4),
            }
            classification_warnings.append(
                f"Auto target changed from {previous_target} to {target_used} due weak grid signal."
            )

    if target_used not in SUPPORTED_LEVEL_GEOMETRIES:
        raise HTTPException(status_code=400, detail=f"Unsupported target_type after classification: {target_used}")

    if requested_target in FLOW_LEVEL_GEOMETRIES and target_used in FLOW_LEVEL_GEOMETRIES:
        if not bool(level_type.get("can_emit_flow", True)):
            classification_warnings.append(
                "Detected modifiers may not fit .flow output; consider target_type=graph."
            )

    detection_info["level_type"] = level_type
    detection_info["level_type_candidates"] = level_type.get("candidates", [])
    detection_info["target_type_requested"] = requested_target
    detection_info["target_type_used"] = target_used
    if auto_target_adjustment is not None:
        detection_info["auto_target_adjustment"] = auto_target_adjustment
    level_notes = level_type.get("notes")
    if isinstance(level_notes, list):
        classification_warnings.extend(str(note) for note in level_notes if str(note))

    level_geometry = str(level_type.get("geometry", "square"))
    level_modifiers = _normalize_level_modifiers(level_type.get("modifiers"))
    meta["level_type_geometry"] = level_geometry
    meta["level_type_modifiers"] = ",".join(level_modifiers)
    meta["level_type_source"] = str(level_type.get("source", "classifier"))
    meta["recommended_output"] = str(level_type.get("recommended_output_format", "flow"))
    if any(manual_edge_overrides.values()):
        detection_info["manual_edge_overrides"] = {
            "add": len(manual_edge_overrides["add"]),
            "remove": len(manual_edge_overrides["remove"]),
            "warps": len(manual_edge_overrides["warps"]),
            "walls": len(manual_edge_overrides["walls"]),
        }

    if target_used in {"square", "hex", "circle"}:
        if any(manual_edge_overrides.values()):
            classification_warnings.append("Manual edge overrides are only applied for graph target output.")
        circle_grid = None
        circle_grid_info: Dict[str, Any] = {}
        if grid_width is None or grid_height is None:
            if target_used == "circle":
                circle_grid, circle_grid_info = detect_circle_grid(
                    warped,
                    min_sectors=3,
                    max_sectors=32,
                )
                if circle_grid is None:
                    detail = "Circle grid size not provided and auto-detection failed."
                    if circle_grid_info.get("warnings"):
                        detail = f"{detail} ({'; '.join(str(w) for w in circle_grid_info['warnings'])})"
                    raise HTTPException(status_code=400, detail=detail)
                grid_width = int(circle_grid.sectors)
                grid_height = int(circle_grid.rings)
                detection_info["grid"] = {
                    "rows": int(circle_grid.rings),
                    "cols": int(circle_grid.sectors),
                    "vertical_lines": int(circle_grid.sectors),
                    "horizontal_lines": int(circle_grid.rings),
                    "mode": "circle",
                }
                detection_info["circle_grid"] = circle_grid_info
            else:
                grid = detect_grid(warped, threshold=threshold, line_threshold=line_threshold, invert=invert)
                if grid is None:
                    raise HTTPException(status_code=400, detail="Grid size not provided and auto-detection failed.")
                grid_width = grid.cols
                grid_height = grid.rows
                detection_info["grid"] = {
                    "rows": grid.rows,
                    "cols": grid.cols,
                    "vertical_lines": grid.vertical_lines,
                    "horizontal_lines": grid.horizontal_lines,
                    "mode": "rect",
                }
        else:
            detection_info["grid"] = {
                "rows": grid_height,
                "cols": grid_width,
                "mode": "circle" if target_used == "circle" else "rect",
            }
            if target_used == "circle":
                circle_grid, circle_grid_info = detect_circle_grid(
                    warped,
                    min_sectors=max(3, min(int(grid_width), 12)),
                    max_sectors=max(int(grid_width) + 6, 24),
                )
                if circle_grid_info:
                    detection_info["circle_grid"] = circle_grid_info

        if grid_width <= 0 or grid_height <= 0:
            raise HTTPException(status_code=400, detail="Invalid grid size.")

        terminals_payload: List[Dict[str, Any]] = []
        terminal_warnings: List[str] = []
        terminal_info: Dict[str, Any] = {}
        if auto_terminals:
            if target_used == "circle":
                placements, info = detect_circle_terminals(
                    warped,
                    rings=grid_height,
                    sectors=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    circle_grid=circle_grid,
                )
            else:
                placements, info = detect_terminals(
                    warped,
                    rows=grid_height,
                    cols=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                )
            grid_tokens, grid_warnings = build_grid(rows=grid_height, cols=grid_width, terminals=placements)
            terminal_warnings = grid_warnings + info.get("warnings", [])
            terminal_info = info
            terminals_payload = [
                {
                    "row": t.row,
                    "col": t.col,
                    "letter": t.letter,
                    "color": [round(c, 2) for c in t.color],
                }
                for t in placements
            ]
        else:
            grid_tokens, grid_warnings = build_grid(rows=grid_height, cols=grid_width, terminals=[])
            terminal_warnings = grid_warnings

        flow_text = build_flow_text(target_used, grid_tokens, meta)
        name = f"{Path(meta.get('source_image', 'image')).stem}_{target_used}_{grid_width}x{grid_height}.flow"
        detection_info["terminals"] = terminals_payload
        detection_info["terminal_info"] = terminal_info
        detection_info["warnings"] = classification_warnings + terminal_warnings
        return {"name": name, "text": flow_text, "metadata": meta, "detection": detection_info}

    if target_used in {"graph", "cube", "star", "figure8"}:
        warp_edges: List[Tuple[str, str]] = []
        wall_edges: List[Tuple[str, str]] = []
        add_edges: List[Tuple[str, str]] = list(manual_edge_overrides["add"])
        remove_edges: List[Tuple[str, str]] = list(manual_edge_overrides["remove"])
        modifier_info: Dict[str, Any] = {}
        graph_terminal_payload: List[Dict[str, Any]] = []
        graph_terminal_info: Dict[str, Any] = {}
        graph_terminal_warnings: List[str] = []

        if target_used in {"cube", "star", "figure8"}:
            topo_width = int(grid_width) if grid_width is not None and grid_width > 0 else max(6, int(graph_nodes))
            topo_height = int(grid_height) if grid_height is not None and grid_height > 0 else topo_width
            warp_edges.extend(manual_edge_overrides["warps"])
            wall_edges.extend(manual_edge_overrides["walls"])
            if "warps" in level_modifiers:
                classification_warnings.append(
                    "Warp modifier detected; auto-warp inference is only available for grid graph layout."
                )
            if "walls" in level_modifiers:
                classification_warnings.append(
                    "Wall modifier auto-detection is only available for grid graph layout."
                )
            obj = build_graph_json(
                layout=target_used,
                width=topo_width,
                height=topo_height,
                nodes=graph_nodes,
                meta=meta,
                warp_edges=warp_edges,
                wall_edges=wall_edges,
                edge_additions=add_edges,
                edge_removals=remove_edges,
            )
            modifier_info["topology"] = {
                "name": target_used,
                "width_hint": topo_width,
                "height_hint": topo_height,
            }
            if auto_terminals:
                node_placements, node_info = detect_terminals_on_nodes(
                    warped,
                    nodes=obj.get("space", {}).get("nodes", {}),
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                )
                graph_terminal_info = node_info
                graph_terminal_warnings.extend([str(w) for w in node_info.get("warnings", [])])
                graph_terminal_payload = [
                    {
                        "node_id": placement.node_id,
                        "letter": placement.letter,
                        "color": [round(c, 2) for c in placement.color],
                    }
                    for placement in node_placements
                ]
                inferred_terminals = build_graph_terminals_from_node_placements(node_placements)
                if inferred_terminals:
                    obj["terminals"] = inferred_terminals
                else:
                    graph_terminal_warnings.append("No topology terminals were confidently detected.")
            name = f"{Path(meta.get('source_image', 'image')).stem}_{target_used}_{topo_width}x{topo_height}.json"
        elif graph_layout == "line":
            if graph_nodes < 2:
                raise HTTPException(status_code=400, detail="Line graphs need at least 2 nodes.")
            if "warps" in level_modifiers and graph_nodes >= 3:
                warp_edges = [("0", str(graph_nodes - 1))]
                modifier_info["warps"] = {"count": len(warp_edges), "mode": "line-endpoint-wrap"}
            warp_edges.extend(manual_edge_overrides["warps"])
            wall_edges.extend(manual_edge_overrides["walls"])
            if "walls" in level_modifiers:
                classification_warnings.append("Wall modifiers are currently only inferred for grid graph layout.")
            obj = build_graph_json(
                layout="line",
                width=0,
                height=0,
                nodes=graph_nodes,
                meta=meta,
                warp_edges=warp_edges,
                wall_edges=wall_edges,
                edge_additions=add_edges,
                edge_removals=remove_edges,
            )
            if auto_terminals:
                node_placements, node_info = detect_terminals_on_nodes(
                    warped,
                    nodes=obj.get("space", {}).get("nodes", {}),
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                )
                graph_terminal_info = node_info
                graph_terminal_warnings.extend([str(w) for w in node_info.get("warnings", [])])
                graph_terminal_payload = [
                    {
                        "node_id": placement.node_id,
                        "letter": placement.letter,
                        "color": [round(c, 2) for c in placement.color],
                    }
                    for placement in node_placements
                ]
                inferred_terminals = build_graph_terminals_from_node_placements(node_placements)
                if inferred_terminals:
                    obj["terminals"] = inferred_terminals
            name = f"{Path(meta.get('source_image', 'image')).stem}_line_{graph_nodes}.json"
        else:
            if grid_width is None or grid_height is None:
                grid = detect_grid(warped, threshold=threshold, line_threshold=line_threshold, invert=invert)
                if grid is not None:
                    grid_width = grid.cols
                    grid_height = grid.rows
                    detection_info["grid"] = {
                        "rows": grid.rows,
                        "cols": grid.cols,
                        "vertical_lines": grid.vertical_lines,
                        "horizontal_lines": grid.horizontal_lines,
                    }
            if grid_width is None or grid_height is None or grid_width * grid_height < 2:
                raise HTTPException(status_code=400, detail="Grid graphs need a valid width/height.")
            if "warps" in level_modifiers:
                warp_edges = _grid_wrap_edges(grid_width, grid_height)
                modifier_info["warps"] = {"count": len(warp_edges), "mode": "toroidal-wrap"}
                if not warp_edges:
                    classification_warnings.append("Warp modifier detected, but grid is too small for wrap edges.")
            warp_edges.extend(manual_edge_overrides["warps"])
            if "walls" in level_modifiers:
                wall_edges, wall_info = detect_wall_edges(
                    warped,
                    rows=grid_height,
                    cols=grid_width,
                )
                modifier_info["walls"] = wall_info
                if not wall_edges:
                    classification_warnings.append(
                        "Walls modifier detected, but no wall edges were confidently detected."
                    )
            wall_edges.extend(manual_edge_overrides["walls"])
            obj = build_graph_json(
                layout="grid",
                width=grid_width,
                height=grid_height,
                nodes=0,
                meta=meta,
                warp_edges=warp_edges,
                wall_edges=wall_edges,
                edge_additions=add_edges,
                edge_removals=remove_edges,
            )
            if auto_terminals:
                placements, term_info = detect_terminals(
                    warped,
                    rows=grid_height,
                    cols=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                )
                graph_terminal_info = term_info
                graph_terminal_warnings.extend([str(w) for w in term_info.get("warnings", [])])
                graph_terminal_payload = [
                    {
                        "row": placement.row,
                        "col": placement.col,
                        "node_id": f"{placement.col},{placement.row}",
                        "letter": placement.letter,
                        "color": [round(c, 2) for c in placement.color],
                    }
                    for placement in placements
                ]
                graph_terminals: Dict[str, List[str]] = {}
                for placement in placements:
                    node_id = f"{placement.col},{placement.row}"
                    graph_terminals.setdefault(placement.letter, []).append(node_id)
                mapped_terminals = {
                    letter: node_ids[:2]
                    for letter, node_ids in graph_terminals.items()
                    if len(node_ids) >= 2
                }
                if mapped_terminals:
                    obj["terminals"] = mapped_terminals
                else:
                    graph_terminal_warnings.append("No graph-grid terminals were confidently detected.")
            name = f"{Path(meta.get('source_image', 'image')).stem}_graph_{grid_width}x{grid_height}.json"
        manual_applied = len(add_edges) + len(remove_edges) + len(manual_edge_overrides["warps"]) + len(manual_edge_overrides["walls"])
        if manual_applied:
            modifier_info["manual_edge_overrides"] = {
                "add": len(add_edges),
                "remove": len(remove_edges),
                "warps": len(manual_edge_overrides["warps"]),
                "walls": len(manual_edge_overrides["walls"]),
            }
        if modifier_info:
            detection_info["modifier_info"] = modifier_info
        if graph_terminal_payload:
            detection_info["terminals"] = graph_terminal_payload
        if graph_terminal_info:
            detection_info["terminal_info"] = graph_terminal_info
        detection_info["warnings"] = classification_warnings + graph_terminal_warnings
        return {"name": name, "text": json.dumps(obj, indent=2), "metadata": meta, "detection": detection_info}

    raise HTTPException(status_code=400, detail=f"Unknown target_type after classification: {target_used}")


@app.post("/image/ocr")
async def image_ocr(
    file: UploadFile = File(...),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    perspective: bool = Form(False),
) -> Dict[str, Any]:
    data = await file.read()
    image = load_image(data)
    crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    cropped = apply_crop(image, crop)
    warped, _perspective = _maybe_perspective(cropped, perspective)
    try:
        import pytesseract  # type: ignore
    except Exception:
        return {"text": "", "suggested_name": None, "message": "pytesseract not installed"}

    tesseract_cmd = os.environ.get("TESSERACT_CMD")
    if tesseract_cmd:
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        except Exception:
            pass

    try:
        text = pytesseract.image_to_string(warped)
    except Exception as e:
        return {"text": "", "suggested_name": None, "message": f"OCR failed: {e}"}

    text_clean = " ".join(text.split())
    level_num = None
    import re

    m = re.search(r"(?:level|lvl)\s*([0-9]{1,5})", text_clean, re.IGNORECASE)
    if m:
        level_num = int(m.group(1))
    else:
        m2 = re.search(r"([0-9]{1,5})", text_clean)
        if m2:
            level_num = int(m2.group(1))

    suggested = f"classic_level_{level_num}.flow" if level_num is not None else None
    return {"text": text_clean, "suggested_name": suggested}


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1").lower() in {"1", "true", "yes", "y", "on"}
    uvicorn.run("backend.app:app", host=host, port=port, reload=reload)
