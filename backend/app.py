from __future__ import annotations

import base64
import asyncio
from collections import OrderedDict
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Sequence, Tuple

# Allow running `python backend/app.py` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, UploadFile as StarletteUploadFile

from flow_solver.puzzle import Puzzle
from flow_solver.migration import puzzle_to_spec
from flow_solver.schema_v2 import CatalogSpec, DisplaySizeSpec, parse_v2_dict
from flow_solver.solver import check_uniqueness_with_z3, solve_puzzle
from flow_solver.spaces.square import build_square_space_from_tokens
from flow_solver.topologies import build_grid_topology, build_hex_topology, build_ring_topology
from flow_solver.validation import validate_puzzle
from backend.acceleration import acceleration_capabilities
from backend.file_lock import FileLockTimeout, InterProcessFileLock
from backend.region_seams import infer_bounded_region_seams
from backend.upload_store import UploadStore
from backend.photo_preprocess import (
    AMBIGUOUS_DISPLAY_MARGIN,
    MIN_RELIABLE_CELL_PX,
    BLUR_CELL_RATIO_REVIEW,
    UnsupportedPhotoFormat,
    _quad_tilt_degrees,
    _spacing_regularity,
    load_camera_image,
    prepare_camera_photo,
    strip_photo_metadata,
)
from backend.image_utils import (
    CropBox,
    apply_crop,
    auto_crop,
    auto_perspective,
    build_graph_terminals_from_node_placements,
    build_flow_text,
    build_graph_json,
    build_grid,
    build_region_crossover_channels,
    classify_level_type,
    detect_bridge_cells,
    detect_circle_grid,
    detect_circle_terminals,
    detect_grid,
    detect_region_topology,
    detect_terminals_on_nodes,
    detect_wall_edges,
    detect_warp_edges,
    detect_terminals,
    load_image,
    parse_expected_flow_count,
    prune_nonterminal_region_leaves,
    repair_nonterminal_region_leaves,
)

MAX_TIMEOUT_MS = 1_000_000
SUPPORTED_LEVEL_GEOMETRIES = {"square", "hex", "circle", "graph", "cube", "star", "figure8"}
SUPPORTED_LEVEL_MODIFIERS = {"bridges", "warps", "walls"}
FLOW_LEVEL_GEOMETRIES = {"square", "hex", "circle"}
TOPOLOGY_LEVEL_GEOMETRIES = {"cube", "star", "figure8"}
SUPPORTED_IMAGE_SOURCE_MODES = {"screenshot", "camera", "auto"}


def _normalize_image_source_mode(raw: Any) -> str:
    mode = str(raw or "screenshot").strip().lower()
    if mode not in SUPPORTED_IMAGE_SOURCE_MODES:
        raise ValueError("source_mode must be screenshot, camera, or auto")
    return mode


def _request_image_source_mode(raw: Any) -> str:
    try:
        return _normalize_image_source_mode(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


def _image_imports_dir() -> Path:
    configured = os.environ.get("FLOW_IMAGE_IMPORTS_DIR", "").strip()
    if not configured:
        return _repo_root() / "data" / "image_imports"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else _repo_root() / path


def _image_upload_store() -> UploadStore:
    configured = os.environ.get("FLOW_IMAGE_UPLOADS_DIR", "").strip()
    root = _repo_root() / "data" / ".image_uploads"
    if configured:
        path = Path(configured).expanduser()
        root = path if path.is_absolute() else _repo_root() / path
    return UploadStore(
        root,
        ttl_seconds=float(os.environ.get("FLOW_IMAGE_UPLOAD_TTL_SECONDS", "1800")),
        max_total_bytes=int(os.environ.get("FLOW_IMAGE_UPLOAD_MAX_BYTES", str(1024 * 1024 * 1024))),
    )


def _image_jobs_dir() -> Path:
    configured = os.environ.get("FLOW_IMAGE_JOBS_DIR", "").strip()
    if not configured:
        return _repo_root() / "data" / "image_jobs"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else _repo_root() / path


def _image_job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="Invalid image job id")
    return _image_jobs_dir() / job_id


def _image_import_dir(import_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", import_id):
        raise HTTPException(status_code=400, detail="Invalid image import id")
    return _image_imports_dir() / import_id


def _read_image_import(import_id: str) -> Dict[str, Any]:
    record_path = _image_import_dir(import_id) / "record.json"
    if not record_path.exists():
        raise HTTPException(status_code=404, detail="Image import not found")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Stored image import is unreadable") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=500, detail="Stored image import is invalid")
    record = _restore_archived_terminal_colors(record)
    record["flagged"] = bool(record.get("flagged", False))
    return record


def _image_import_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    summary = {
        key: value
        for key, value in record.items()
        if key not in {"image_file", "processing", "result", "solve", "runs"}
    }
    # Legacy archive records predate the review queue. Expose an explicit false
    # value so API clients do not need to distinguish old and new records.
    summary["flagged"] = bool(record.get("flagged", False))
    solve = record.get("solve") if isinstance(record.get("solve"), dict) else None
    if solve is not None:
        summary["solve_status"] = solve.get("status")
        summary["solve_error"] = solve.get("error")
        result = solve.get("result") if isinstance(solve.get("result"), dict) else {}
        stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        summary["solve_ms"] = stats.get("total_ms")
        summary["solver"] = stats.get("solver")
    runs = record.get("runs") if isinstance(record.get("runs"), list) else []
    summary["run_count"] = len(runs) + 1
    processing = record.get("processing") if isinstance(record.get("processing"), dict) else {}
    summary["source_mode"] = _normalize_image_source_mode(processing.get("source_mode"))
    summary["source_mode_effective"] = str(
        processing.get("source_mode_effective") or summary["source_mode"]
    )
    return summary


_IMAGE_IMPORT_INDEX_SCHEMA_VERSION = "1"
_IMAGE_IMPORT_INDEX_INIT_LOCK = threading.Lock()
_IMAGE_IMPORT_INDEX_INITIALIZED: set[str] = set()


def _image_import_index_path() -> Path:
    configured = os.environ.get("FLOW_IMAGE_IMPORT_INDEX_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else _repo_root() / path
    imports_dir = _image_imports_dir()
    return imports_dir.parent / f".{imports_dir.name}.sqlite3"


def _image_import_index_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


@contextmanager
def _image_import_index_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Commit/rollback and explicitly close the short-lived index connection."""

    connection = _image_import_index_connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _ensure_image_import_index_schema() -> Path:
    path = _image_import_index_path()
    key = str(path.absolute())
    if key in _IMAGE_IMPORT_INDEX_INITIALIZED:
        return path
    with _IMAGE_IMPORT_INDEX_INIT_LOCK:
        if key in _IMAGE_IMPORT_INDEX_INITIALIZED:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        with _image_import_index_connection(path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS image_import_index (
                    id TEXT PRIMARY KEY,
                    record_status TEXT NOT NULL,
                    solve_status TEXT,
                    failed INTEGER NOT NULL,
                    solved INTEGER NOT NULL,
                    flagged INTEGER NOT NULL,
                    searchable TEXT NOT NULL,
                    sort_time REAL NOT NULL,
                    summary_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_image_import_sort
                    ON image_import_index(sort_time);
                CREATE INDEX IF NOT EXISTS idx_image_import_status
                    ON image_import_index(record_status, solve_status, failed, solved);
                CREATE INDEX IF NOT EXISTS idx_image_import_flagged
                    ON image_import_index(flagged, sort_time);
                CREATE TABLE IF NOT EXISTS image_import_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        _IMAGE_IMPORT_INDEX_INITIALIZED.add(key)
    return path


def _image_import_index_row(record: Dict[str, Any]) -> Tuple[Any, ...]:
    summary = _image_import_summary(record)
    failed = summary.get("status") == "failed" or summary.get("solve_status") == "failed"
    solved = summary.get("solve_status") == "solved"
    searchable = " ".join(
        str(summary.get(key) or "")
        for key in ("original_name", "generated_name", "geometry", "flag_reason")
    ).casefold()
    return (
        str(summary.get("id") or ""),
        str(summary.get("status") or ""),
        str(summary.get("solve_status")) if summary.get("solve_status") is not None else None,
        int(failed),
        int(solved),
        int(bool(summary.get("flagged", False))),
        searchable,
        float(summary.get("updated_at", summary.get("created_at", 0)) or 0),
        json.dumps(summary, separators=(",", ":"), sort_keys=True),
    )


_IMAGE_IMPORT_INDEX_UPSERT = """
    INSERT INTO image_import_index (
        id, record_status, solve_status, failed, solved, flagged,
        searchable, sort_time, summary_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        record_status=excluded.record_status,
        solve_status=excluded.solve_status,
        failed=excluded.failed,
        solved=excluded.solved,
        flagged=excluded.flagged,
        searchable=excluded.searchable,
        sort_time=excluded.sort_time,
        summary_json=excluded.summary_json
"""


def _upsert_image_import_index(record: Dict[str, Any]) -> None:
    path = _ensure_image_import_index_schema()
    with _image_import_index_connection(path) as connection:
        connection.execute(_IMAGE_IMPORT_INDEX_UPSERT, _image_import_index_row(record))


def _delete_image_import_index(import_ids: Sequence[str]) -> None:
    if not import_ids:
        return
    path = _ensure_image_import_index_schema()
    with _image_import_index_connection(path) as connection:
        connection.executemany(
            "DELETE FROM image_import_index WHERE id = ?",
            ((str(import_id),) for import_id in import_ids),
        )


def _invalidate_image_import_index_backfill() -> None:
    path = _image_import_index_path()
    if not path.exists():
        return
    try:
        with _image_import_index_connection(path) as connection:
            connection.execute(
                "DELETE FROM image_import_index_meta WHERE key = 'archive_backfill'"
            )
    except Exception:
        pass


def _backfill_image_import_index() -> Path:
    path = _ensure_image_import_index_schema()
    with _image_import_index_connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute(
            "SELECT value FROM image_import_index_meta WHERE key = 'archive_backfill'"
        ).fetchone()
        if marker and marker[0] == _IMAGE_IMPORT_INDEX_SCHEMA_VERSION:
            connection.commit()
            return path

        rows: List[Tuple[Any, ...]] = []
        base = _image_imports_dir()
        if base.exists():
            for record_path in base.glob("*/record.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if isinstance(record, dict):
                        rows.append(_image_import_index_row(record))
                except Exception:
                    continue
        connection.execute("DELETE FROM image_import_index")
        if rows:
            connection.executemany(_IMAGE_IMPORT_INDEX_UPSERT, rows)
        connection.execute(
            """
            INSERT INTO image_import_index_meta(key, value)
            VALUES ('archive_backfill', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (_IMAGE_IMPORT_INDEX_SCHEMA_VERSION,),
        )
        connection.commit()
    return path


def _query_image_import_index(
    *,
    limit: int,
    offset: int,
    status: str,
    flagged: Optional[bool],
    query: str,
    order: str,
) -> Tuple[List[Dict[str, Any]], int]:
    path = _backfill_image_import_index()
    conditions: List[str] = []
    parameters: List[Any] = []
    if status == "processed":
        conditions.append("record_status = ?")
        parameters.append("processed")
    elif status == "solved":
        conditions.append("solved = 1")
    elif status == "failed":
        conditions.append("failed = 1")
    elif status == "unknown":
        conditions.append("failed = 0 AND solved = 0")
    if flagged is not None:
        conditions.append("flagged = ?")
        parameters.append(int(flagged))
    if query:
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("searchable LIKE ? ESCAPE '\\'")
        parameters.append(f"%{escaped_query}%")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    direction = "DESC" if order == "newest" else "ASC"
    with _image_import_index_connection(path) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM image_import_index{where}",
                parameters,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT summary_json
            FROM image_import_index
            {where}
            ORDER BY sort_time {direction}, id {direction}
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()
    entries = [json.loads(row[0]) for row in rows]
    return entries, total


def _image_import_run_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    solve = record.get("solve") if isinstance(record.get("solve"), dict) else {}
    solve_result = solve.get("result") if isinstance(solve.get("result"), dict) else {}
    solve_stats = solve_result.get("stats") if isinstance(solve_result.get("stats"), dict) else {}
    return {
        "completed_at": record.get("updated_at", record.get("created_at")),
        "status": record.get("status", "processed"),
        "geometry": record.get("geometry"),
        "grid": record.get("grid"),
        "terminal_count": record.get("terminal_count", 0),
        "processing": record.get("processing", {}),
        "solve_status": solve.get("status"),
        "solve_error": solve.get("error"),
        "solve_ms": solve_stats.get("total_ms"),
        "solver": solve_stats.get("solver"),
        "error": record.get("error"),
    }


# Record updates are read-modify-write on a per-import record.json; concurrent
# requests for the same import (parallel batch reprocess, solve attach) must
# serialize or one update silently overwrites the other. The thread lock avoids
# local contention and the stable advisory file lock covers uvicorn workers.
_IMPORT_LOCKS: Dict[str, threading.Lock] = {}
_IMPORT_LOCKS_GUARD = threading.Lock()


def _import_lock(import_id: str) -> threading.Lock:
    with _IMPORT_LOCKS_GUARD:
        lock = _IMPORT_LOCKS.get(import_id)
        if lock is None:
            lock = threading.Lock()
            _IMPORT_LOCKS[import_id] = lock
        return lock


@contextmanager
def _locked_import(import_id: str) -> Iterator[None]:
    """Serialize a record update across threads and uvicorn worker processes."""

    imports_dir = _image_imports_dir()
    lock_path = imports_dir.parent / f".{imports_dir.name}.locks" / f"{import_id}.lock"
    with _import_lock(import_id):
        with InterProcessFileLock(lock_path):
            yield


def _write_image_import_record(import_id: str, record: Dict[str, Any]) -> None:
    record_path = _image_import_dir(import_id) / "record.json"
    temporary_record = record_path.with_suffix(".json.tmp")
    temporary_record.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    temporary_record.replace(record_path)
    try:
        _upsert_image_import_index(record)
    except Exception:
        # The JSON record remains authoritative. A missing/corrupt index can be
        # rebuilt without losing an import update.
        _invalidate_image_import_index_backfill()


def _set_image_import_flag(
    import_id: str,
    *,
    flagged: bool,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist or clear the human/agent review marker on an archived import."""

    with _locked_import(import_id):
        record = _read_image_import(import_id)
        updated_at = time.time()
        record["flagged"] = flagged
        record["updated_at"] = updated_at
        if flagged:
            record["flagged_at"] = updated_at
            record.pop("reviewed_at", None)
            normalized_reason = (reason or "").strip()
            if normalized_reason:
                record["flag_reason"] = normalized_reason
        else:
            record.pop("flagged_at", None)
            record.pop("flag_reason", None)
            # A person cleared the review, so the archived result is a
            # human-confirmed label (used to build the real camera corpus).
            record["reviewed_at"] = updated_at
        _write_image_import_record(import_id, record)
        return record


def _auto_flag_image_import(import_id: str, *, reason: str) -> Dict[str, Any]:
    """Add an automated review reason without discarding a human reason."""

    normalized_reason = reason.strip()[:1000]
    with _locked_import(import_id):
        record = _read_image_import(import_id)
        existing = str(record.get("flag_reason") or "").strip()
        if existing and normalized_reason and normalized_reason not in existing:
            combined_reason = f"{existing}; {normalized_reason}"
        else:
            combined_reason = existing or normalized_reason
        updated_at = time.time()
        record["flagged"] = True
        record["flagged_at"] = record.get("flagged_at") or updated_at
        record["updated_at"] = updated_at
        if combined_reason:
            record["flag_reason"] = combined_reason[:2000]
        _write_image_import_record(import_id, record)
        return record


def _archived_generation_completeness_error(
    import_id: str,
    *,
    name: str,
    text: str,
) -> Optional[str]:
    """Return a blocking review message only for the archive's exact generated text."""

    record = _read_image_import(import_id)
    generated = record.get("result") if isinstance(record.get("result"), dict) else {}
    if generated.get("name") != name or generated.get("text") != text:
        return None
    detection = generated.get("detection") if isinstance(generated.get("detection"), dict) else {}
    completeness = (
        detection.get("terminal_completeness")
        if isinstance(detection.get("terminal_completeness"), dict)
        else {}
    )
    if completeness.get("status") != "incomplete":
        return None
    detected = completeness.get("detected_pairs")
    expected = completeness.get("expected_pairs")
    return (
        "Terminal detection is incomplete "
        f"(detected {detected} of {expected} expected flow pairs); review the screenshot before solving."
    )


def _replace_image_import(
    import_id: str,
    *,
    data: bytes,
    result: Dict[str, Any],
    processing: Dict[str, Any],
) -> Dict[str, Any]:
    with _locked_import(import_id):
        return _replace_image_import_locked(import_id, data=data, result=result, processing=processing)


def _replace_image_import_locked(
    import_id: str,
    *,
    data: bytes,
    result: Dict[str, Any],
    processing: Dict[str, Any],
) -> Dict[str, Any]:
    record = _read_image_import(import_id)
    image_file = str(record.get("image_file", ""))
    if Path(image_file).name != image_file:
        raise HTTPException(status_code=500, detail="Stored image import has an invalid image path")
    image_path = _image_import_dir(import_id) / image_file
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Stored screenshot not found")
    stored = image_path.read_bytes()
    # Camera archives hold metadata-stripped bytes; accept either the stored
    # file itself or an original upload that sanitizes to it.
    if stored != data and stored != _archive_bytes_for_processing(data, processing)[0]:
        raise HTTPException(status_code=400, detail="Reprocessed image does not match the archived screenshot")

    runs = record.get("runs") if isinstance(record.get("runs"), list) else []
    runs.append(_image_import_run_summary(record))
    detection = result.get("detection") if isinstance(result.get("detection"), dict) else {}
    grid = detection.get("grid") if isinstance(detection.get("grid"), dict) else None
    level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
    terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    record.update(
        {
            "updated_at": time.time(),
            "status": "processed",
            "generated_name": str(result.get("name") or record.get("generated_name") or "image"),
            "geometry": level_type.get("geometry") or detection.get("target_type_used"),
            "grid": grid,
            "terminal_count": len(terminals),
            "processing": processing,
            "result": result,
            "runs": runs,
        }
    )
    record.pop("error", None)
    record.pop("solve", None)
    _write_image_import_record(import_id, record)
    return record


def _record_image_import_reprocess_failure(import_id: str, *, error: str, stage: str) -> Dict[str, Any]:
    with _locked_import(import_id):
        record = _read_image_import(import_id)
        runs = record.get("runs") if isinstance(record.get("runs"), list) else []
        runs.append(_image_import_run_summary(record))
        record.update(
            {
                "updated_at": time.time(),
                "status": "failed",
                "processing": {"stage": stage, "reprocessed": True},
                "error": error[:4000],
                "runs": runs,
            }
        )
        record.pop("result", None)
        record.pop("solve", None)
        _write_image_import_record(import_id, record)
        return record


def _store_image_import_solve(
    import_id: str,
    *,
    name: str,
    text: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> bool:
    """Attach an immediate solve outcome to its exact archived generation."""

    with _locked_import(import_id):
        record_path = _image_import_dir(import_id) / "record.json"
        if not record_path.exists():
            return False
        record = json.loads(record_path.read_text(encoding="utf-8"))
        generated = record.get("result") if isinstance(record.get("result"), dict) else {}
        if generated.get("name") != name or generated.get("text") != text:
            return False
        solve_record: Dict[str, Any] = {
            "status": "solved" if result is not None else "failed",
            "updated_at": time.time(),
        }
        if result is not None:
            solve_record["result"] = result
        if error:
            solve_record["error"] = error[:4000]
        record["solve"] = solve_record
        record["updated_at"] = solve_record["updated_at"]
        _write_image_import_record(import_id, record)
        return True


_ARCHIVE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


def _strips_photo_metadata(source_mode: Any) -> bool:
    return str(source_mode or "screenshot").strip().lower() in {"camera", "auto"}


def _archive_bytes_for_processing(data: bytes, processing: Dict[str, Any]) -> Tuple[bytes, Optional[str]]:
    """Drop GPS/device metadata from camera uploads before they are persisted.

    Screenshot-mode bytes are archived unchanged so existing replay baselines
    stay byte-identical.
    """

    if not (
        _strips_photo_metadata(processing.get("source_mode"))
        or processing.get("source_mode_effective") == "camera"
    ):
        return data, None
    sanitized = strip_photo_metadata(data)
    return sanitized.data, sanitized.suffix


def _store_image_import(
    *,
    data: bytes,
    original_name: str,
    content_type: Optional[str],
    image_size: Tuple[int, int],
    result: Optional[Dict[str, Any]],
    processing: Dict[str, Any],
    status: str = "processed",
    error: Optional[str] = None,
) -> Dict[str, Any]:
    import_id = uuid.uuid4().hex
    created_at = time.time()
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ARCHIVE_IMAGE_SUFFIXES:
        suffix = ".bin"
    data, sanitized_suffix = _archive_bytes_for_processing(data, processing)
    if sanitized_suffix:
        suffix = sanitized_suffix
    image_file = f"source{suffix}"
    result_payload = result or {}
    detection = result_payload.get("detection") if isinstance(result_payload.get("detection"), dict) else {}
    grid = detection.get("grid") if isinstance(detection.get("grid"), dict) else None
    level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
    terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    record: Dict[str, Any] = {
        "id": import_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": status,
        "original_name": Path(original_name).name or "image",
        "content_type": content_type or "application/octet-stream",
        "byte_size": len(data),
        "image_size": {"width": image_size[0], "height": image_size[1]},
        "image_file": image_file,
        "generated_name": str(result_payload.get("name") or Path(original_name).stem),
        "geometry": level_type.get("geometry") or detection.get("target_type_used"),
        "grid": grid,
        "terminal_count": len(terminals),
        "processing": processing,
        "flagged": False,
    }
    if result is not None:
        record["result"] = result
    if error:
        record["error"] = error
    record_dir = _image_imports_dir() / import_id
    try:
        record_dir.mkdir(parents=True, exist_ok=False)
        (record_dir / image_file).write_bytes(data)
        temporary_record = record_dir / "record.json.tmp"
        temporary_record.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        temporary_record.replace(record_dir / "record.json")
        try:
            _upsert_image_import_index(record)
        except Exception:
            _invalidate_image_import_index_backfill()
    except Exception:
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    return record


_IMAGE_JOB_OPTION_DEFAULTS: Dict[str, Any] = {
    "source_mode": "screenshot",
    "target_type": "auto",
    "grid_width": None,
    "grid_height": None,
    "graph_layout": "grid",
    "graph_nodes": 10,
    "output_schema_version": 2,
    "auto_terminals": True,
    "auto_classify": True,
    "expected_flow_count": None,
    "photo_corners_json": None,
    "level_type_json": None,
    "edge_overrides_json": None,
    "metadata_json": None,
    "crop_x": None,
    "crop_y": None,
    "crop_width": None,
    "crop_height": None,
    "threshold": 230,
    "line_threshold": 0.6,
    "invert": False,
    "sat_threshold": 30.0,
    "brightness_min": 30.0,
    "brightness_max": 230.0,
    "margin_ratio": 0.15,
    "cluster_threshold": 60.0,
    "bg_threshold": 40.0,
    "perspective": False,
}


def _normalize_image_job_options(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("options_json must be a JSON object")
    unknown = sorted(set(raw) - set(_IMAGE_JOB_OPTION_DEFAULTS))
    if unknown:
        raise ValueError(f"Unknown image job option(s): {', '.join(unknown)}")
    options = {**_IMAGE_JOB_OPTION_DEFAULTS, **raw}
    options["source_mode"] = _normalize_image_source_mode(options["source_mode"])
    for key in ("photo_corners_json", "level_type_json", "edge_overrides_json", "metadata_json"):
        if options[key] is not None and not isinstance(options[key], str):
            options[key] = json.dumps(options[key], separators=(",", ":"))
    if options["expected_flow_count"] is not None:
        options["expected_flow_count"] = int(options["expected_flow_count"])
        if not 1 <= options["expected_flow_count"] <= 26:
            raise ValueError("expected_flow_count must be between 1 and 26")
    return options


def _image_job_lock_path(job_id: str) -> Path:
    return _image_jobs_dir() / ".locks" / f"{job_id}.lock"


def _read_image_job(job_id: str) -> Dict[str, Any]:
    path = _image_job_dir(job_id) / "job.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image job not found")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Stored image job is unreadable") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=500, detail="Stored image job is invalid")
    return record


def _write_image_job(job_id: str, record: Dict[str, Any]) -> None:
    path = _image_job_dir(job_id) / "job.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _update_image_job(job_id: str, mutate: Any) -> Dict[str, Any]:
    with InterProcessFileLock(_image_job_lock_path(job_id)):
        record = _read_image_job(job_id)
        mutate(record)
        record["updated_at"] = time.time()
        _write_image_job(job_id, record)
        return record


async def _run_image_job(job_id: str) -> None:
    claimed = False

    def claim(record: Dict[str, Any]) -> None:
        nonlocal claimed
        if record.get("status") != "queued":
            return
        claimed = True
        record["status"] = "running"
        record["started_at"] = time.time()

    _update_image_job(job_id, claim)
    if not claimed:
        return

    record = _read_image_job(job_id)
    options = _normalize_image_job_options(record.get("options", {}))
    job_dir = _image_job_dir(job_id)
    items = record.get("items") if isinstance(record.get("items"), list) else []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("status") == "processed":
            continue
        stored_name = str(item.get("stored_name", ""))
        source_path = job_dir / stored_name
        try:
            data = source_path.read_bytes()
            upload = UploadFile(
                file=io.BytesIO(data),
                filename=str(item.get("original_name") or source_path.name),
                headers=Headers({"content-type": str(item.get("content_type") or "application/octet-stream")}),
            )
            async with _image_pipeline_permit():
                generated = await image_generate(
                    file=upload,
                    replace_import_id=None,
                    _permit=None,
                    **options,
                )
            outcome = {
                "status": "processed",
                "import_id": generated.get("import_id"),
                "generated_name": generated.get("name"),
            }
            try:
                solved = solve(
                    SolveRequest(
                        name=str(generated.get("name") or "image.json"),
                        text=str(generated.get("text") or ""),
                        solver="z3",
                        timeout_ms=30_000,
                        import_id=str(generated.get("import_id") or "") or None,
                    )
                )
                outcome.update(
                    solve_status="solved",
                    solve_ms=solved.get("stats", {}).get("total_ms"),
                )
            except Exception as solve_exc:
                detail = solve_exc.detail if isinstance(solve_exc, HTTPException) else str(solve_exc)
                outcome.update(solve_status="failed", solve_error=str(detail)[:4000])
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            outcome = {"status": "failed", "error": str(detail)[:4000]}

        def finish_item(current: Dict[str, Any], *, item_index: int = index, result: Dict[str, Any] = outcome) -> None:
            current_items = current.get("items")
            if not isinstance(current_items, list) or item_index >= len(current_items):
                return
            current_items[item_index].update(result)
            current["completed"] = sum(
                isinstance(value, dict) and value.get("status") in {"processed", "failed"}
                for value in current_items
            )
            current["failed"] = sum(
                isinstance(value, dict) and value.get("status") == "failed"
                for value in current_items
            )

        _update_image_job(job_id, finish_item)

    def complete(current: Dict[str, Any]) -> None:
        current["status"] = "completed"
        current["finished_at"] = time.time()

    _update_image_job(job_id, complete)


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
    if isinstance(obj, dict) and ("format" in obj or "schema_version" in obj):
        from flow_solver.schema_v2 import parse_v2_dict

        spec = parse_v2_dict(obj)
        template_id = spec.topology.template.id.lower() if spec.topology.template else "graph"
        if template_id in SUPPORTED_LEVEL_GEOMETRIES:
            kind = template_id
        elif "cube" in template_id:
            kind = "cube"
        elif "star" in template_id:
            kind = "star"
        elif "figure8" in template_id or "figure-8" in template_id:
            kind = "figure8"
        elif "circle" in template_id or "ring" in template_id:
            kind = "circle"
        elif "hex" in template_id:
            kind = "hex"
        elif "square" in template_id or "grid" in template_id:
            kind = "square"
        else:
            kind = "graph"

        meta = _normalize_meta(spec.meta)
        catalog = spec.catalog
        if catalog.app:
            meta.setdefault("app", catalog.app)
        if catalog.variant:
            meta.setdefault("variant", catalog.variant)
        if catalog.pack:
            if catalog.pack.name:
                meta.setdefault("pack", catalog.pack.name)
            elif catalog.pack.id:
                meta.setdefault("pack", catalog.pack.id)
        if catalog.level:
            if catalog.level.name:
                meta.setdefault("level", catalog.level.name)
            elif catalog.level.number is not None:
                meta.setdefault("level", str(catalog.level.number))
            elif catalog.level.id:
                meta.setdefault("level", catalog.level.id)
        if catalog.mechanics:
            meta.setdefault("mechanics", ", ".join(catalog.mechanics))

        metrics: Dict[str, int] = {
            "cells": len(spec.topology.cells),
            "nodes": len(spec.topology.channels),
            "edges": sum(1 for edge in spec.topology.adjacencies if edge.state == "open"),
        }
        if catalog.display_size:
            if catalog.display_size.width is not None:
                metrics["width"] = catalog.display_size.width
            if catalog.display_size.height is not None:
                metrics["height"] = catalog.display_size.height
            if catalog.display_size.label:
                meta.setdefault("display_size", catalog.display_size.label)
        return kind, meta, metrics

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
        if "width" in metrics and "height" in metrics:
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
    if metrics.get("width") and metrics.get("height"):
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
    payload = {
        "nodes": nodes,
        "edges": edges,
        "terminals": terminals,
        "tiles": puzzle.tiles,
        "meta": dict(puzzle.meta),
    }
    terminal_colors = {
        key: value
        for key, value in _terminal_color_map_from_meta(puzzle.meta).items()
        if key in terminals
    }
    if terminal_colors:
        payload["terminal_colors"] = terminal_colors
    if puzzle.source_spec is not None:
        source = puzzle.source_spec.to_dict()
        payload["schema_version"] = source["schema_version"]
        payload["topology"] = {
            "template": source["topology"].get("template"),
            "data": source["topology"].get("data", {}),
        }
        payload["adjacencies"] = source["topology"]["adjacencies"]
        payload["display"] = source["display"]
        payload["catalog"] = source["catalog"]
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


def _terminal_color_map_from_placements(placements: Sequence[Any]) -> Dict[str, str]:
    """Average each detected endpoint pair into one stable display color."""

    samples: Dict[str, List[Tuple[float, float, float]]] = {}
    for placement in placements:
        if isinstance(placement, dict):
            letter_raw = placement.get("letter")
            color_raw = placement.get("color")
        else:
            letter_raw = getattr(placement, "letter", None)
            color_raw = getattr(placement, "color", None)
        letter = str(letter_raw or "").strip().upper()
        if not letter:
            continue
        rgb: Optional[Tuple[float, float, float]] = None
        if isinstance(color_raw, (list, tuple)) and len(color_raw) >= 3:
            try:
                rgb = (float(color_raw[0]), float(color_raw[1]), float(color_raw[2]))
            except (TypeError, ValueError):
                rgb = None
        else:
            normalized = _normalize_hex_color(color_raw)
            if normalized is not None:
                rgb = tuple(float(int(normalized[index : index + 2], 16)) for index in (1, 3, 5))
        if rgb is not None:
            samples.setdefault(letter, []).append(rgb)

    colors: Dict[str, str] = {}
    for letter, values in samples.items():
        count = float(len(values))
        averaged = tuple(sum(value[channel] for value in values) / count for channel in range(3))
        normalized = _normalize_hex_color(averaged)
        if normalized is not None:
            colors[letter] = normalized
    return colors


def _restore_archived_terminal_colors(record: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill old archived results from their retained detector RGB samples."""

    result = record.get("result") if isinstance(record.get("result"), dict) else None
    if result is None:
        return record
    detection = result.get("detection") if isinstance(result.get("detection"), dict) else {}
    placements = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    recovered = _terminal_color_map_from_placements(placements)
    text = result.get("text")
    if not recovered or not isinstance(text, str) or not text.strip():
        return record

    if text.lstrip().startswith("{"):
        try:
            puzzle_data = json.loads(text)
        except Exception:
            return record
        if not isinstance(puzzle_data, dict):
            return record
        meta = puzzle_data.get("meta") if isinstance(puzzle_data.get("meta"), dict) else {}
        existing = _terminal_color_map_from_meta(meta)
        terminals = puzzle_data.get("terminals")
        declared: Dict[str, str] = {}
        if isinstance(terminals, dict):
            for letter, terminal in terminals.items():
                if not isinstance(terminal, dict):
                    continue
                color = _normalize_hex_color(terminal.get("color"))
                if color is not None:
                    declared[str(letter).strip().upper()] = color
        # Explicit schema/meta colors are user intent and take precedence over
        # recovered detector samples; recovery only fills historical gaps.
        merged = {**recovered, **declared, **existing}
        meta["terminal_colors"] = merged
        puzzle_data["meta"] = meta
        if isinstance(terminals, dict):
            for letter, color in merged.items():
                terminal = terminals.get(letter)
                if isinstance(terminal, dict) and not _normalize_hex_color(terminal.get("color")):
                    terminal["color"] = color
        result["text"] = json.dumps(puzzle_data, indent=2)
        return record

    if not _terminal_color_map_from_meta(
        {"terminal_colors": next(
            (
                line.split(":", 1)[1].strip()
                for line in text.splitlines()
                if line.lstrip("# ").lower().startswith("terminal_colors:")
            ),
            "",
        )}
    ):
        lines = text.splitlines()
        insert_at = next(
            (index + 1 for index, line in enumerate(lines) if line.lower().startswith("# fill:")),
            0,
        )
        lines.insert(insert_at, f"# terminal_colors: {json.dumps(recovered, separators=(',', ':'))}")
        result["text"] = "\n".join(lines).rstrip() + "\n"
    return record


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


# Auto mode without camera EXIF compares what the two pipelines produce.  On
# the 219-screenshot corpus no screenshot fails the screenshot grid check and
# none yields a confident board through a corroborated, tilted outline, so a
# screenshot needs both independent checks to go wrong before it is misrouted.
AUTO_SCREENSHOT_REGULARITY_MAX = 0.85
AUTO_CAMERA_BOARD_SCORE_MIN = 0.70
_AUTO_DECISION_LOCK = threading.Lock()
_AUTO_DECISIONS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _auto_source_decision(data: bytes, image: Image.Image) -> Dict[str, Any]:
    """Choose the camera path for an EXIF-less upload only on pipeline evidence."""

    key = _photo_cache_key(data)
    with _AUTO_DECISION_LOCK:
        cached = _AUTO_DECISIONS.get(key)
        if cached is not None:
            _AUTO_DECISIONS.move_to_end(key)
            return dict(cached)

    crop = auto_crop(image, threshold=230, invert=False, padding=max(8, int(min(image.size) * 0.01)))
    grid = detect_grid(apply_crop(image, crop), threshold=230, line_threshold=0.6, invert=False)
    regularity = (
        min(_spacing_regularity(grid.x_lines), _spacing_regularity(grid.y_lines))
        if grid is not None and len(grid.x_lines) >= 3 and len(grid.y_lines) >= 3
        else 0.0
    )
    decision: Dict[str, Any] = {
        "camera": False,
        "screenshot_grid": grid is not None,
        "screenshot_regularity": round(regularity, 4),
    }
    if regularity < AUTO_SCREENSHOT_REGULARITY_MAX:
        prepared = prepare_camera_photo(image, detect_board=True, cache_key=key)
        info = prepared.info
        selected = info.get("selected") if isinstance(info.get("selected"), dict) else None
        board = info.get("board") if isinstance(info.get("board"), dict) else {}
        margin = info.get("selection_margin")
        tilt = _quad_tilt_degrees([(point["x"], point["y"]) for point in selected["corners"]]) if selected else 0.0
        ratio = float((selected or {}).get("metrics", {}).get("opposite_side_ratio", 1.0))
        board_score = float(board.get("combined_score") or 0.0)
        decision.update(
            camera=bool(
                selected
                and board.get("selected") is not None
                and int(info.get("selected_corroboration") or 0) >= 1
                and (margin is None or float(margin) >= AMBIGUOUS_DISPLAY_MARGIN)
                and board_score >= AUTO_CAMERA_BOARD_SCORE_MIN
                and (tilt >= 2.0 or ratio <= 0.96)
            ),
            camera_board_score=round(board_score, 4),
            display_tilt_degrees=round(tilt, 3),
            display_opposite_side_ratio=round(ratio, 4),
        )
    with _AUTO_DECISION_LOCK:
        _AUTO_DECISIONS[key] = decision
        while len(_AUTO_DECISIONS) > 64:
            _AUTO_DECISIONS.popitem(last=False)
    return dict(decision)


def _load_image_for_source_mode(
    data: bytes,
    source_mode: str,
) -> Tuple[Image.Image, Dict[str, Any], str]:
    if source_mode in {"camera", "auto"} and len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Camera image exceeds the 50 MB upload limit")
    if source_mode == "camera":
        try:
            image, info = load_camera_image(data)
        except UnsupportedPhotoFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Could not decode camera image: {exc}") from exc
        info["effective_source_mode"] = "camera"
        return image, info, "camera"
    auto_evidence: Optional[Dict[str, Any]] = None
    if source_mode == "auto":
        try:
            camera_image, camera_info = load_camera_image(data)
        except UnsupportedPhotoFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Could not decode image: {exc}") from exc
        if float(camera_info.get("camera_likelihood", 0.0)) >= 0.65:
            camera_info["effective_source_mode"] = "camera"
            camera_info["auto_selection_reason"] = "camera_metadata"
            return camera_image, camera_info, "camera"
        auto_evidence = _auto_source_decision(data, camera_image)
        if auto_evidence["camera"]:
            camera_info["effective_source_mode"] = "camera"
            camera_info["auto_selection_reason"] = "pipeline_evidence"
            camera_info["auto_evidence"] = auto_evidence
            return camera_image, camera_info, "camera"
    image = load_image(data)
    info: Dict[str, Any] = {
        "format": "legacy",
        "original_size": {"width": image.width, "height": image.height},
        "oriented_size": {"width": image.width, "height": image.height},
        "orientation_applied": False,
        "effective_source_mode": "screenshot",
        "auto_selection_reason": "screenshot_safe_default" if source_mode == "auto" else None,
    }
    if auto_evidence is not None:
        info["auto_evidence"] = auto_evidence
    return image, info, "screenshot"


def _parse_photo_corners_json(
    raw: Optional[str],
    *,
    image_size: Tuple[int, int],
) -> Optional[List[List[float]]]:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid photo_corners_json: {exc}") from exc
    if not isinstance(decoded, list) or len(decoded) != 4:
        raise HTTPException(status_code=400, detail="photo_corners_json must contain four corners")
    width, height = image_size
    corners: List[List[float]] = []
    for item in decoded:
        if isinstance(item, dict):
            values = (item.get("x"), item.get("y"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            values = (item[0], item[1])
        else:
            raise HTTPException(status_code=400, detail="Each photo corner must contain x and y")
        try:
            x, y = float(values[0]), float(values[1])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Photo corner coordinates must be numbers") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise HTTPException(status_code=400, detail="Photo corner coordinates must be finite")
        if not (-1.0 <= x <= width and -1.0 <= y <= height):
            raise HTTPException(status_code=400, detail="Photo corner lies outside the source image")
        corners.append([x, y])
    return corners


def _request_photo_corners(
    raw: Optional[str],
    *,
    source_mode: str,
    image_size: Tuple[int, int],
) -> Optional[List[List[float]]]:
    corners = _parse_photo_corners_json(raw, image_size=image_size)
    if corners is not None and source_mode != "camera":
        raise HTTPException(status_code=400, detail="Manual photo corners require source_mode=camera")
    return corners


@dataclass
class CameraPipelineImages:
    color: Image.Image
    geometry: Image.Image
    # Glare mask aligned with ``color``; terminal detectors skip masked pixels.
    invalid_mask: Image.Image
    # Straightened display before board correction, used for OCR.
    display: Image.Image
    perspective_info: Dict[str, Any]
    auto_crop_info: Dict[str, Any]
    photo_info: Dict[str, Any]
    # Board with half a cell of surrounding pixels (warp detection needs
    # context outside the outer grid lines); None when no board warp ran.
    warp_context: Optional[Image.Image] = None


def _board_outline_in_photo(
    photo_info: Dict[str, Any],
    board_crop: Optional[CropBox],
) -> Optional[List[Dict[str, float]]]:
    """Map the prepared board's corners back into photo coordinates.

    Lets the UI draw what was actually used as the board next to the screen
    outline, so a wrong board choice is visible before processing.
    """

    inverse = photo_info.get("inverse_homography")
    size = photo_info.get("rectified_size") if isinstance(photo_info.get("rectified_size"), dict) else None
    if not isinstance(inverse, list) or not size:
        return None
    left, top = (board_crop.x, board_crop.y) if board_crop is not None else (0, 0)
    right = left + (board_crop.width if board_crop is not None else int(size["width"]))
    bottom = top + (board_crop.height if board_crop is not None else int(size["height"]))
    outline: List[Dict[str, float]] = []
    for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)):
        px = inverse[0][0] * x + inverse[0][1] * y + inverse[0][2]
        py = inverse[1][0] * x + inverse[1][1] * y + inverse[1][2]
        pw = inverse[2][0] * x + inverse[2][1] * y + inverse[2][2]
        if abs(pw) < 1e-9:
            return None
        outline.append({"x": round(px / pw, 2), "y": round(py / pw, 2)})
    return outline


def _photo_cache_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prepare_camera_pipeline_images(
    image: Image.Image,
    *,
    crop: Optional[CropBox],
    threshold: int,
    invert: bool,
    manual_corners: Optional[Sequence[Sequence[float]]],
    auto_board_crop: bool = True,
    expected_grid: Optional[Tuple[int, int]] = None,
    cache_key: Optional[str] = None,
    board_warp: bool = True,
    board_mesh_crop: bool = False,
) -> CameraPipelineImages:
    roi = (
        (crop.x, crop.y, crop.width, crop.height)
        if crop is not None and manual_corners is None
        else None
    )
    try:
        prepared = prepare_camera_photo(
            image,
            roi=roi,
            manual_corners=manual_corners,
            # The board warp assumes a square lattice; other geometries keep
            # the straightened display and are cropped to their cell cluster
            # (or auto-crop) below.
            detect_board=auto_board_crop,
            square_board=board_warp,
            expected_grid=expected_grid,
            cache_key=cache_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not prepare camera photo: {exc}") from exc

    color_image = prepared.color_image
    geometry_image = prepared.geometry_image
    glare_mask = prepared.glare_mask
    detected_board = (
        prepared.info.get("board")
        if isinstance(prepared.info.get("board"), dict)
        else None
    )
    board_warp_applied = bool(detected_board and detected_board.get("selected") is not None)
    padding = max(8, int(min(color_image.width, color_image.height) * 0.01))
    board_mesh = prepared.info.get("board_mesh") if isinstance(prepared.info.get("board_mesh"), dict) else None
    if not board_mesh_crop:
        board_mesh = None
    board_crop: Optional[CropBox] = None
    if auto_board_crop and not board_warp_applied and board_mesh is not None:
        # Crop to the detected cell cluster plus a margin that keeps the
        # outer cell walls and some background inside the board image.  Only
        # lattices of equal cells (hex) qualify: circle rings and free-form
        # regions vary in size, so their cluster is a partial board.
        margin = max(padding, int(round(0.3 * float(board_mesh["pitch"]))))
        left = max(0, int(math.floor(float(board_mesh["x"]))) - margin)
        top = max(0, int(math.floor(float(board_mesh["y"]))) - margin)
        right = min(color_image.width, int(math.ceil(float(board_mesh["x"]) + float(board_mesh["width"]))) + margin)
        bottom = min(color_image.height, int(math.ceil(float(board_mesh["y"]) + float(board_mesh["height"]))) + margin)
        if right > left and bottom > top:
            board_crop = CropBox(left, top, right - left, bottom - top)
    if board_crop is None and auto_board_crop and not board_warp_applied:
        board_crop = auto_crop(
            color_image,
            threshold=threshold,
            invert=invert,
            padding=padding,
        )
    if board_crop is not None:
        color_image = apply_crop(color_image, board_crop)
        geometry_image = apply_crop(geometry_image, board_crop)
        glare_mask = apply_crop(glare_mask, board_crop)

    photo_info = dict(prepared.info)
    photo_info["board_crop"] = (
        {
            "x": board_crop.x,
            "y": board_crop.y,
            "width": board_crop.width,
            "height": board_crop.height,
        }
        if board_crop is not None
        else None
    )
    photo_info["prepared_board_size"] = {
        "width": color_image.width,
        "height": color_image.height,
    }
    photo_info["board_outline"] = _board_outline_in_photo(photo_info, board_crop)
    glare_histogram = glare_mask.histogram()
    photo_info["glare_fraction_on_prepared_board"] = round(
        (glare_mask.width * glare_mask.height - glare_histogram[0])
        / float(max(1, glare_mask.width * glare_mask.height)),
        6,
    )
    selected = photo_info.get("selected") if isinstance(photo_info.get("selected"), dict) else None
    perspective_info = {
        "mode": "camera-photo",
        "selected": selected,
        "width": color_image.width,
        "height": color_image.height,
    }
    auto_crop_info = {
        "applied": board_crop is not None or board_warp_applied,
        "source": (
            "camera-board-warp"
            if board_warp_applied
            else "camera-board-mesh" if board_mesh is not None else "camera-board"
        ),
        "x": board_crop.x if board_crop is not None else 0,
        "y": board_crop.y if board_crop is not None else 0,
        "width": board_crop.width if board_crop is not None else color_image.width,
        "height": board_crop.height if board_crop is not None else color_image.height,
    }
    return CameraPipelineImages(
        color=color_image,
        geometry=geometry_image,
        invalid_mask=glare_mask,
        display=prepared.display_image if prepared.display_image is not None else prepared.color_image,
        perspective_info=perspective_info,
        auto_crop_info=auto_crop_info,
        photo_info=photo_info,
        warp_context=prepared.board_context_image if board_warp_applied else None,
    )


# Review thresholds in one place so scripts/calibrate_camera_review.py can
# sweep them against a labeled corpus without re-implementing the rules.
CAMERA_REVIEW_THRESHOLDS: Dict[str, float] = {
    "min_display_score": 0.45,
    "ambiguous_display_margin": AMBIGUOUS_DISPLAY_MARGIN,
    "blur_cell_ratio": BLUR_CELL_RATIO_REVIEW,
    "min_cell_px": MIN_RELIABLE_CELL_PX,
    "severe_glare_fraction": 0.10,
    "glare_fraction": 0.02,
    "min_board_side_px": 400.0,
    # Closest RGB distance allowed between terminals of different pairs; on
    # 347 synthetic replays, < 40 caught 29 of 176 wrong imports and 2 of 171
    # correct ones.
    "min_pair_color_separation": 40.0,
    # Camera preparation normalizes Flow terminals to a bright strongest
    # channel (about 240).  A terminal whose strongest channel stays below
    # this is a shadow or a finger over the screen, not a dot: on 990
    # synthetic replays no correct import had one below 100.
    "min_terminal_peak_channel": 90.0,
}


# Measured on the synthetic scene corpora: square boards are supported from
# photos; warps (border markers are lost), hex boards, and region-layout
# boards (region detection fails) are not yet, so their results always go to
# review.
CAMERA_UNSUPPORTED_GEOMETRIES = {"hex", "graph", "cube", "star", "figure8"}
CAMERA_UNSUPPORTED_MODIFIERS = {"warps", "walls"}
# Normalized local contrast a photographed wall must exceed above the median
# cell boundary (image_utils.detect_wall_edges local_contrast_margin).
CAMERA_WALL_LOCAL_CONTRAST_MARGIN = 0.20


def _camera_board_is_not_square(level_type: Dict[str, Any], graph_layout: Optional[str]) -> bool:
    geometry = str(level_type.get("geometry") or "").lower()
    signals = level_type.get("signals") if isinstance(level_type.get("signals"), dict) else {}
    return (
        geometry in {"hex", "circle", "graph", "cube", "star", "figure8"}
        or str(graph_layout or "").lower() == "regions"
        or signals.get("recommended_graph_layout") == "regions"
    )


def _camera_unsupported_features(level_type: Dict[str, Any], graph_layout: Optional[str] = None) -> Optional[str]:
    features: List[str] = []
    geometry = str(level_type.get("geometry") or "").lower()
    if geometry in CAMERA_UNSUPPORTED_GEOMETRIES:
        features.append(f"{geometry} geometry")
    signals = level_type.get("signals") if isinstance(level_type.get("signals"), dict) else {}
    if str(graph_layout or "").lower() == "regions" or signals.get("recommended_graph_layout") == "regions":
        features.append("region layout")
    modifiers = level_type.get("modifiers") if isinstance(level_type.get("modifiers"), list) else []
    features.extend(sorted(str(item) for item in modifiers if str(item) in CAMERA_UNSUPPORTED_MODIFIERS))
    return ", ".join(features) or None


def _camera_terminal_review_reasons(detection: Dict[str, Any], limits: Dict[str, float]) -> List[str]:
    """Terminal evidence that a photographed board was misread.

    Every Flow color appears exactly twice.  Under a camera color cast two
    similar colors can merge into one four-terminal cluster (a pair is then
    silently dropped) or one color can split into two single terminals.
    Different pairs whose colors are nearly equal can also be paired with the
    wrong partner even when the pair count looks complete.
    """

    reasons: List[str] = []
    info = detection.get("terminal_info") if isinstance(detection.get("terminal_info"), dict) else {}
    clusters = info.get("clusters") if isinstance(info.get("clusters"), list) else []
    counts = [int(cluster.get("count") or 0) for cluster in clusters if isinstance(cluster, dict)]
    if any(count != 2 for count in counts):
        reasons.append("Some terminal colors did not form exactly one pair; similar colors may have been merged or split.")
    terminals = detection.get("terminals") if isinstance(detection.get("terminals"), list) else []
    by_letter: Dict[str, List[Sequence[float]]] = {}
    for terminal in terminals:
        if isinstance(terminal, dict) and isinstance(terminal.get("color"), (list, tuple)):
            by_letter.setdefault(str(terminal.get("letter")), []).append(terminal["color"])
    closest = math.inf
    for letter, colors in by_letter.items():
        for color in colors:
            for other_letter, other_colors in by_letter.items():
                if other_letter == letter:
                    continue
                for other in other_colors:
                    closest = min(closest, math.dist(color, other))
    if closest < limits["min_pair_color_separation"]:
        reasons.append("Two different terminal colors look nearly identical in the photo; check that pairs are matched correctly.")
    peaks = [max(float(value) for value in color) for colors in by_letter.values() for color in colors if len(color)]
    if peaks and min(peaks) < limits["min_terminal_peak_channel"]:
        reasons.append("A terminal is much darker than any Flow color; a shadow or finger may have been read as a dot.")
    return reasons


def _camera_photo_review(
    photo_info: Optional[Dict[str, Any]],
    terminal_completeness: Optional[Dict[str, Any]] = None,
    *,
    thresholds: Optional[Dict[str, float]] = None,
    detection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(photo_info, dict):
        return {"required": False, "reasons": [], "confidence": 1.0}
    limits = {**CAMERA_REVIEW_THRESHOLDS, **(thresholds or {})}
    quality = photo_info.get("quality") if isinstance(photo_info.get("quality"), dict) else {}
    rectified = (
        photo_info.get("prepared_board_size")
        if isinstance(photo_info.get("prepared_board_size"), dict)
        else {}
    )
    selected = photo_info.get("selected") if isinstance(photo_info.get("selected"), dict) else None
    reasons: List[str] = []
    confidence = 1.0
    if selected is None:
        reasons.append("No confident display quadrilateral was detected.")
        confidence -= 0.45
    elif float(selected.get("score", 0.0)) < limits["min_display_score"]:
        reasons.append("The detected display boundary has weak geometric evidence.")
        confidence -= 0.25
    elif photo_info.get("consensus_votes") is not None:
        if int(photo_info["consensus_votes"]) == 0:
            reasons.append("The detected screen outlines disagree about where the board is; confirm the four corners.")
            confidence -= 0.25
    else:
        margin = photo_info.get("selection_margin")
        if margin is not None and float(margin) < limits["ambiguous_display_margin"]:
            reasons.append("Two different screen outlines fit almost equally well; confirm the four corners.")
            confidence -= 0.25
        elif photo_info.get("selected_corroboration") == 0:
            reasons.append("Only one detector found this screen outline; confirm the four corners.")
            confidence -= 0.20
    blur_cell_ratio = quality.get("blur_cell_ratio")
    if blur_cell_ratio is None and "blur_sigma_px" in quality:
        reasons.append("Board sharpness could not be measured; no clear edges were found.")
        confidence -= 0.35
    elif blur_cell_ratio is not None and float(blur_cell_ratio) > limits["blur_cell_ratio"]:
        reasons.append("The rectified board is too blurred for high-confidence color detection.")
        confidence -= 0.35
    elif blur_cell_ratio is None and float(quality.get("laplacian_variance", 999.0)) < 35.0:
        # Records prepared before blur was measured relative to cell size.
        reasons.append("The rectified board is too blurred for high-confidence color detection.")
        confidence -= 0.35
    cell_size = quality.get("cell_size_px")
    if cell_size is not None and float(cell_size) < limits["min_cell_px"]:
        reasons.append("Board cells are too small in the photo for reliable terminal colors.")
        confidence -= 0.25
    glare_fraction = float(quality.get("glare_fraction", 0.0))
    if glare_fraction > limits["severe_glare_fraction"]:
        reasons.append("Glare obscures more than 10% of the rectified image.")
        confidence -= 0.30
    elif glare_fraction > limits["glare_fraction"]:
        reasons.append("Glare overlaps enough of the board to require terminal review.")
        confidence -= 0.20
    shortest_side = min(
        int(rectified.get("width", 10_000) or 0),
        int(rectified.get("height", 10_000) or 0),
    )
    if shortest_side < limits["min_board_side_px"]:
        reasons.append("The prepared board is below the minimum reliable resolution.")
        confidence -= 0.25
    # Downstream evidence: without an advertised flow count, rejected
    # near-terminal cells are the main sign that a photographed dot was lost.
    if isinstance(terminal_completeness, dict):
        if (
            terminal_completeness.get("status") == "plausible"
            and int(terminal_completeness.get("near_miss_count") or 0) > 0
        ):
            reasons.append("Some cells looked almost like terminals; check for missing endpoints.")
            confidence -= 0.20
    if isinstance(detection, dict):
        for reason in _camera_terminal_review_reasons(detection, limits):
            reasons.append(reason)
            confidence -= 0.25
        level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
        unsupported = _camera_unsupported_features(level_type, detection.get("graph_layout"))
        if unsupported:
            reasons.append(
                f"Photos of boards with {unsupported} are not reliable yet; check the imported puzzle against the photo."
            )
            confidence -= 0.40
    return {
        "required": bool(reasons),
        "reasons": reasons,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
    }


def _ocr_image_text(image: Image.Image) -> Tuple[str, Optional[str]]:
    """Run optional OCR without making image generation depend on Tesseract."""

    try:
        import pytesseract  # type: ignore
    except Exception:
        return "", "pytesseract not installed"

    tesseract_cmd = os.environ.get("TESSERACT_CMD")
    if tesseract_cmd:
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        except Exception:
            pass
    try:
        text = pytesseract.image_to_string(image)
    except Exception as exc:
        return "", f"OCR failed: {exc}"
    return " ".join(str(text).split()), None


def _terminal_completeness_payload(
    terminal_info: Dict[str, Any],
    *,
    detected_endpoints: int,
    expected_pairs: Optional[int],
) -> Dict[str, Any]:
    detected_pairs = detected_endpoints // 2
    pairing_complete = detected_endpoints > 0 and detected_endpoints % 2 == 0
    near_misses = terminal_info.get("near_misses")
    near_miss_count = len(near_misses) if isinstance(near_misses, list) else 0
    recovered = terminal_info.get("recovered_pairs")
    recovered_count = len(recovered) if isinstance(recovered, list) else 0

    if expected_pairs is not None and detected_pairs != expected_pairs:
        status = "incomplete"
        confidence = 0.1
        reason = f"Detected {detected_pairs} of {expected_pairs} expected flow pairs."
    elif expected_pairs is not None and pairing_complete:
        status = "verified"
        confidence = 0.98 if not recovered_count else 0.92
        reason = f"Detected all {expected_pairs} expected flow pairs."
    elif pairing_complete:
        status = "plausible"
        confidence = 0.82 if not near_miss_count else 0.68
        reason = "All detected terminal colors are paired; no advertised flow count was available."
    else:
        status = "uncertain"
        confidence = 0.2
        reason = "Terminal detection did not produce complete endpoint pairs."

    return {
        "status": status,
        "expected_pairs": expected_pairs,
        "detected_pairs": detected_pairs,
        "detected_endpoints": detected_endpoints,
        "pairing_complete": pairing_complete,
        "confidence": confidence,
        "near_miss_count": near_miss_count,
        "recovered_pair_count": recovered_count,
        "review_required": status in {"incomplete", "uncertain"},
        "reason": reason,
    }


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


def _import_schema_extension(
    detection: Dict[str, Any],
    edge_corrections: Optional[Dict[str, List[Tuple[str, str]]]] = None,
) -> Dict[str, Any]:
    level_type = detection.get("level_type") if isinstance(detection.get("level_type"), dict) else {}
    candidates = level_type.get("candidates") if isinstance(level_type.get("candidates"), list) else []
    extension: Dict[str, Any] = {
        "version": 1,
        "classifier": {
            "confidence": _candidate_confidence(level_type),
            "source": str(level_type.get("source", "unknown")),
            "geometry": str(level_type.get("geometry", "unknown")),
            "modifiers": _normalize_level_modifiers(level_type.get("modifiers")),
            "candidates": candidates,
        },
        "target_type_requested": detection.get("target_type_requested"),
        "target_type_used": detection.get("target_type_used"),
    }
    corrections = edge_corrections or {}
    if any(corrections.values()):
        extension["manual_edge_corrections"] = {
            key: [[left, right] for left, right in corrections.get(key, [])]
            for key in ("add", "remove", "warps", "walls")
        }
    return extension


def _image_graph_schema_v2(
    obj: Dict[str, Any],
    *,
    target_type: str,
    graph_layout: str,
    width: Optional[int],
    height: Optional[int],
    level_modifiers: Sequence[str],
    import_detection: Optional[Dict[str, Any]] = None,
    edge_corrections: Optional[Dict[str, List[Tuple[str, str]]]] = None,
) -> Dict[str, Any]:
    """Upgrade a generated legacy graph to the canonical, typed v2 schema."""

    space = obj.get("space") if isinstance(obj.get("space"), dict) else {}
    nodes = space.get("nodes") if isinstance(space.get("nodes"), dict) else {}
    base_edges = _parse_edge_pairs(space.get("edges"), field="space.edges")
    warps = _parse_edge_pairs(space.get("warps"), field="space.warps")
    seams = _parse_edge_pairs(space.get("seams"), field="space.seams")
    raw_crossovers = space.get("crossovers")
    crossovers = (
        [dict(item) for item in raw_crossovers if isinstance(item, dict)]
        if isinstance(raw_crossovers, list)
        else []
    )
    walls = _parse_edge_pairs(space.get("walls"), field="space.walls")
    overrides = space.get("edge_overrides") if isinstance(space.get("edge_overrides"), dict) else {}
    additions = _parse_edge_pairs(overrides.get("add"), field="space.edge_overrides.add")
    removals = _parse_edge_pairs(overrides.get("remove"), field="space.edge_overrides.remove")

    def edge_key(pair: Tuple[str, str]) -> Tuple[str, str]:
        u, v = pair
        return (u, v) if u < v else (v, u)

    warp_keys = {edge_key(pair) for pair in warps}
    edge_kinds: Dict[Tuple[str, str], str] = {}
    if target_type in {"cube", "star"}:
        for pair in base_edges:
            left = nodes.get(pair[0]) if isinstance(nodes.get(pair[0]), dict) else {}
            right = nodes.get(pair[1]) if isinstance(nodes.get(pair[1]), dict) else {}
            left_data = left.get("data") if isinstance(left.get("data"), dict) else {}
            right_data = right.get("data") if isinstance(right.get("data"), dict) else {}
            if left_data.get("face") != right_data.get("face"):
                edge_kinds[edge_key(pair)] = "seam"
    edge_kinds.update({
        edge_key(pair): ("warp" if edge_key(pair) in warp_keys else "custom")
        for pair in additions
    })
    edge_kinds.update({edge_key(pair): "warp" for pair in warps})
    edge_kinds.update({edge_key(pair): "seam" for pair in seams})
    wall_keys = {edge_key(pair) for pair in walls}
    blocked_edges = {
        edge_key(pair): ("custom" if edge_key(pair) not in wall_keys else "local")
        for pair in removals
    }
    blocked_edges.update({edge_key(pair): "local" for pair in walls})

    puzzle = Puzzle.from_json(json.dumps(obj))
    template_id = target_type if target_type != "graph" else graph_layout
    if template_id == "star":
        template_id = "radial_star"
    parameters: Dict[str, Any] = {}
    if width is not None and width > 0:
        parameters["width"] = int(width)
    if height is not None and height > 0:
        parameters["height"] = int(height)
    parameters["cells"] = len(puzzle.tiles)

    mechanics = set(str(item) for item in level_modifiers if str(item))
    mechanics.update(kind for kind in edge_kinds.values() if kind != "local")
    if crossovers:
        mechanics.add("crossovers")
    if blocked_edges:
        mechanics.add("walls")
    if graph_layout == "regions":
        mechanics.add("irregular-regions")
    variant = "shapes" if target_type in {"cube", "star", "figure8"} or graph_layout == "regions" else "custom"
    display_size = None
    if width is not None or height is not None:
        display_size = DisplaySizeSpec(
            label=(f"{width}x{height}" if width is not None and height is not None else None),
            width=width,
            height=height,
            unit="template",
        )
    catalog = CatalogSpec(
        variant=variant,
        display_size=display_size,
        mechanics=tuple(sorted(mechanics)),
    )
    spec = puzzle_to_spec(
        puzzle,
        template_id=template_id,
        template_parameters=parameters,
        edge_kinds=edge_kinds,
        blocked_edges=blocked_edges,
        catalog=catalog,
    )
    payload = spec.to_dict()
    if crossovers:
        crossover_by_edge: Dict[Tuple[str, str], Dict[str, Any]] = {}
        normalized_crossovers: List[Dict[str, Any]] = []
        for index, item in enumerate(crossovers):
            try:
                pair = _parse_edge_pairs([item.get("under")], field="space.crossovers.under")[0]
            except (IndexError, ValueError):
                continue
            key = edge_key(pair)
            crossover_id = str(item.get("id") or f"crossover-{index + 1:02d}")
            normalized = {
                "id": crossover_id,
                "under": [key[0], key[1]],
                "under_path": [
                    str(node_id) for node_id in item.get("under_path", [])
                ]
                if isinstance(item.get("under_path"), list)
                else [],
                "surface_channel": (
                    str(item["surface_channel"])
                    if item.get("surface_channel") is not None
                    else None
                ),
                "under_channel": (
                    str(item["under_channel"])
                    if item.get("under_channel") is not None
                    else None
                ),
                "continuation": (
                    str(item["continuation"])
                    if item.get("continuation") is not None
                    else None
                ),
                "surface_neighbors": [
                    str(node_id) for node_id in item.get("surface_neighbors", [])
                ]
                if isinstance(item.get("surface_neighbors"), list)
                else [],
                "center": item.get("center"),
                "under_vector": item.get("under_vector"),
                "over_vector": item.get("over_vector"),
                "source": str(item.get("source") or "region-overlap"),
            }
            crossover_by_edge[key] = normalized
            normalized_crossovers.append(normalized)

        if normalized_crossovers:
            payload["topology"].setdefault("data", {})["crossovers"] = normalized_crossovers
            for adjacency in payload["topology"]["adjacencies"]:
                key = edge_key(
                    (
                        str(adjacency["a"]["channel"]),
                        str(adjacency["b"]["channel"]),
                    )
                )
                crossover = crossover_by_edge.get(key)
                if crossover is None:
                    continue
                adjacency["kind"] = "seam"
                adjacency["group"] = crossover["id"]
                adjacency["data"] = {
                    **(adjacency.get("data") or {}),
                    "mechanic": "crossover",
                    "crossover_id": crossover["id"],
                    "layer": "under",
                    "continuation": crossover["under_path"] or crossover["under"],
                    "surface_channel": crossover["surface_channel"],
                    "under_channel": crossover["under_channel"],
                    "center": crossover["center"],
                    "under_vector": crossover["under_vector"],
                    "over_vector": crossover["over_vector"],
                    "source": crossover["source"],
                }
    if import_detection is not None:
        payload.setdefault("extensions", {})["flow-solver/import"] = _import_schema_extension(
            import_detection,
            edge_corrections,
        )

    if any(
        isinstance(node, dict)
        and isinstance(node.get("pos"), (list, tuple))
        and len(node["pos"]) >= 3
        and abs(float(node["pos"][2])) > 1e-9
        for node in nodes.values()
    ):
        payload["display"]["dimension"] = 3
    if target_type in {"cube", "star"}:
        for node_id, node in nodes.items():
            data = node.get("data") if isinstance(node, dict) else None
            face = data.get("face") if isinstance(data, dict) else None
            if face is not None:
                payload["display"]["cells"].setdefault(str(node_id), {})["face"] = str(face)
    if graph_layout == "regions":
        for node_id, node in nodes.items():
            data = node.get("data") if isinstance(node, dict) else None
            polygon = data.get("polygon") if isinstance(data, dict) else None
            if not isinstance(polygon, list) or len(polygon) < 3:
                continue
            payload["display"]["cells"].setdefault(str(node_id), {})["polygon"] = [
                [float(point[0]), -float(point[1]), 0.0]
                for point in polygon
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
    return parse_v2_dict(payload).to_dict()


def _image_grid_schema_v2(
    *,
    target_type: str,
    grid: Sequence[Sequence[str]],
    width: int,
    height: int,
    meta: Dict[str, str],
    terminal_placements: Sequence[Any],
    level_modifiers: Sequence[str],
    import_detection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Encode square, hex, and circular image imports in canonical v2 form."""

    if target_type == "square":
        topology = build_grid_topology(width=width, height=height)
        template_id = "grid"
        parameters: Dict[str, Any] = {"width": width, "height": height}
    elif target_type == "hex":
        topology = build_hex_topology(width=width, height=height)
        template_id = "hex_grid"
        parameters = {"width": width, "height": height, "offset": "odd-r"}
    elif target_type == "circle":
        topology = build_ring_topology(rings=height, sectors=width, core=False)
        template_id = "ring"
        parameters = {"rings": height, "sectors": width, "core": False}
    else:  # pragma: no cover - guarded by the image endpoint
        raise ValueError(f"Unsupported grid target for schema v2: {target_type!r}")

    terminal_lists: Dict[str, List[str]] = {}
    for row, values in enumerate(grid):
        for column, raw_token in enumerate(values):
            token = str(raw_token)
            if len(token) == 1 and token.isalpha() and token.upper() == token:
                terminal_lists.setdefault(token, []).append(f"{column},{row}")
    terminals = {
        color: (nodes[0], nodes[1])
        for color, nodes in terminal_lists.items()
        if len(nodes) >= 2
    }

    canonical_meta: Dict[str, Any] = dict(meta)
    terminal_colors = _terminal_color_map_from_placements(terminal_placements)
    if terminal_colors:
        canonical_meta["terminal_colors"] = terminal_colors

    if target_type == "square":
        # Reuse the square token compiler so holes and bridge '+' cells become
        # real physical cells with independent horizontal/vertical channels.
        graph, tiles, parsed_terminals = build_square_space_from_tokens(
            [list(row) for row in grid],
            require_terminals=False,
        )
        puzzle = Puzzle(
            graph=graph,
            tiles=tiles,
            terminals=parsed_terminals,
            fill=True,
            meta=canonical_meta,
        )
    else:
        puzzle = Puzzle(
            graph=topology.to_graph(),
            tiles={node.id: [node.id] for node in topology.nodes},
            terminals=terminals,
            fill=True,
            meta=canonical_meta,
        )
    edge_kinds: Dict[Tuple[str, str], str] = {}
    mechanics = {str(item) for item in level_modifiers if str(item)}
    if target_type == "circle":
        for u, v in topology.edges:
            left = u.split(",")
            right = v.split(",")
            if (
                len(left) == 2
                and len(right) == 2
                and left[1] == right[1]
                and abs(int(left[0]) - int(right[0])) == width - 1
            ):
                edge_kinds[(u, v)] = "seam"
        mechanics.add("seam")

    catalog = CatalogSpec(
        variant={"square": "classic", "hex": "hexes", "circle": "shapes"}[target_type],
        display_size=DisplaySizeSpec(
            label=f"{width}x{height}",
            width=width,
            height=height,
            unit="cells",
        ),
        mechanics=tuple(sorted(mechanics)),
    )
    payload = puzzle_to_spec(
        puzzle,
        template_id=template_id,
        template_parameters=parameters,
        edge_kinds=edge_kinds,
        catalog=catalog,
    ).to_dict()
    if import_detection is not None:
        payload.setdefault("extensions", {})["flow-solver/import"] = _import_schema_extension(
            import_detection
        )
    return parse_v2_dict(payload).to_dict()


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
    check_unique: bool = False
    import_id: Optional[str] = None


class ValidateRequest(ParseRequest):
    check_solvable: bool = False
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


class ImageImportBulkDeleteRequest(BaseModel):
    ids: List[str] = Field(default_factory=list, max_length=1000)


class ImageImportReprocessFailureRequest(BaseModel):
    error: str = Field(min_length=1, max_length=4000)
    stage: str = Field(default="screenshot-library", min_length=1, max_length=100)


class ImageImportFlagRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)


_RECOVERED_JOB_TASKS: set[asyncio.Task[None]] = set()
_IMAGE_JOB_LEASE_SECONDS = 15 * 60


def _track_recovered_job(coroutine: Any) -> None:
    task = asyncio.create_task(coroutine)
    _RECOVERED_JOB_TASKS.add(task)
    task.add_done_callback(_RECOVERED_JOB_TASKS.discard)


async def _recover_running_image_job(job_id: str) -> None:
    """Requeue an orphaned running job after its persisted progress lease expires."""

    while True:
        try:
            record = _read_image_job(job_id)
            if record.get("status") != "running":
                return
            observed_updated_at = float(record.get("updated_at", 0))
            delay = max(0.0, observed_updated_at + _IMAGE_JOB_LEASE_SECONDS - time.time())
            if delay:
                await asyncio.sleep(delay)
            requeued = False

            def requeue_if_unchanged(current: Dict[str, Any]) -> None:
                nonlocal requeued
                if (
                    current.get("status") == "running"
                    and float(current.get("updated_at", 0)) <= observed_updated_at
                ):
                    current["status"] = "queued"
                    requeued = True

            _update_image_job(job_id, requeue_if_unchanged)
            if requeued:
                await _run_image_job(job_id)
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            return


async def _resume_queued_image_jobs() -> None:
    base = _image_jobs_dir()
    if not base.exists():
        return
    for path in base.glob("*/job.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            job_id = str(record.get("id", ""))
            status = record.get("status")
            if status == "queued":
                _track_recovered_job(_run_image_job(job_id))
            elif status == "running":
                _track_recovered_job(_recover_running_image_job(job_id))
        except Exception:
            continue


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _resume_queued_image_jobs()
    try:
        yield
    finally:
        tasks = tuple(_RECOVERED_JOB_TASKS)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Universal Flow Game Solver API", version="0.1.0", lifespan=_lifespan)

try:
    _IMAGE_PIPELINE_CONCURRENCY = max(
        1,
        min(32, int(os.environ.get("FLOW_IMAGE_PIPELINE_CONCURRENCY", "2"))),
    )
except ValueError:
    _IMAGE_PIPELINE_CONCURRENCY = 2
_IMAGE_PIPELINE_SEMAPHORE = asyncio.Semaphore(_IMAGE_PIPELINE_CONCURRENCY)


@asynccontextmanager
async def _image_pipeline_permit() -> AsyncIterator[None]:
    """Apply one process-local and one node-wide heavy-image concurrency slot."""

    async with _IMAGE_PIPELINE_SEMAPHORE:
        acquired: Optional[InterProcessFileLock] = None
        lock_dir = _image_imports_dir().parent / ".image_pipeline.locks"
        while acquired is None:
            for slot in range(_IMAGE_PIPELINE_CONCURRENCY):
                candidate = InterProcessFileLock(lock_dir / f"slot-{slot}.lock", timeout=0)
                try:
                    candidate.__enter__()
                except FileLockTimeout:
                    continue
                acquired = candidate
                break
            if acquired is None:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            acquired.__exit__(None, None, None)


async def _heavy_image_permit() -> AsyncIterator[None]:
    """Bound CPU-heavy image work so interactive requests retain capacity."""

    async with _image_pipeline_permit():
        yield


def _offload_image_endpoint(function: Any) -> Any:
    """Run an async UploadFile endpoint on a worker thread, including CPU work."""

    @wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        def run() -> Any:
            return asyncio.run(function(*args, **kwargs))

        return await asyncio.to_thread(run)

    return wrapper

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
def health() -> Dict[str, Any]:
    return {"status": "ok", "acceleration": acceleration_capabilities()}


@app.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    return {
        "acceleration": acceleration_capabilities(),
        "image_pipeline_concurrency": _IMAGE_PIPELINE_CONCURRENCY,
    }


@app.get("/puzzles")
def list_puzzles() -> Dict[str, Any]:
    entries = [_build_entry(path, source) for source, path in _list_puzzle_files()]
    return {"entries": entries}


# Repository documentation surfaced in the frontend's Docs view. A whitelist
# (rather than a path parameter into docs/) keeps this from ever serving
# arbitrary repository files.
DOC_PAGES: Dict[str, Tuple[str, str]] = {
    "architecture": ("Architecture", "ARCHITECTURE.md"),
    "variants": ("Flow variants & research", "FLOW_VARIANTS_AND_ARCHITECTURE.md"),
    "production-readiness": ("Production readiness", "PRODUCTION_READINESS.md"),
}


@app.get("/docs-pages")
def list_doc_pages() -> Dict[str, Any]:
    return {
        "pages": [
            {"id": page_id, "title": title}
            for page_id, (title, _file) in DOC_PAGES.items()
        ]
    }


@app.get("/docs-pages/{page_id}")
def get_doc_page(page_id: str) -> Dict[str, Any]:
    entry = DOC_PAGES.get(page_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown documentation page")
    title, file_name = entry
    path = _repo_root() / "docs" / file_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Documentation file is missing")
    return {"id": page_id, "title": title, "markdown": path.read_text(encoding="utf-8")}


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
    # Validate both syntax and inexpensive topology/coverage invariants.
    try:
        puzzle = _parse_puzzle(final_text, name=safe_name)
        validate_puzzle(puzzle).require_valid()
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
        validation = validate_puzzle(puzzle)
        size_label = _format_size_label(kind, metrics, counts["nodes"])
        return {
            "kind": kind,
            "type_label": _type_label(kind),
            "size_label": size_label,
            "metrics": metrics,
            "counts": counts,
            "meta": meta,
            "terminals": {c: [a, b] for c, (a, b) in puzzle.terminals.items()},
            "validation": validation.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/validate")
def validate(req: ValidateRequest) -> Dict[str, Any]:
    try:
        puzzle = _parse_puzzle(req.text, name=req.name)
        if req.fill is not None:
            puzzle = replace(puzzle, fill=req.fill)
        report = validate_puzzle(puzzle)
        payload = report.to_dict()
        if req.check_solvable and report.valid:
            try:
                result = solve_puzzle(puzzle, solver=req.solver, timeout_ms=req.timeout_ms)
                payload["solvable"] = True
                payload["solution"] = {
                    "path_lengths": {color: len(path) for color, path in result.paths.items()},
                    "stats": dict(getattr(result, "stats", {}) or {}),
                    "unique": getattr(result, "unique", None),
                }
            except Exception as exc:
                payload["solvable"] = False
                payload["solve_error"] = str(exc)
        return payload
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/solve")
def solve(req: SolveRequest) -> Dict[str, Any]:
    try:
        if req.import_id:
            completeness_error = _archived_generation_completeness_error(
                req.import_id,
                name=req.name,
                text=req.text,
            )
            if completeness_error:
                raise ValueError(completeness_error)
        puzzle = _parse_puzzle(req.text, name=req.name)
        if req.fill is not None:
            puzzle = replace(puzzle, fill=req.fill)
        if req.check_unique:
            if req.solver != "z3":
                raise ValueError("Uniqueness checking is currently available only with the Z3 solver.")
            res = check_uniqueness_with_z3(puzzle, timeout_ms=req.timeout_ms)
        else:
            res = solve_puzzle(puzzle, solver=req.solver, timeout_ms=req.timeout_ms)
        node_color = {k: v for k, v in res.node_color.items()}
        solve_stats = dict(res.stats)
        acceleration = acceleration_capabilities()
        solve_stats["cuda_available"] = bool(acceleration.get("cuda_available"))
        solve_stats["image_acceleration"] = str(acceleration.get("image_backend", "cpu"))
        solve_stats["exact_solver_acceleration"] = "native-cpu-sat"
        payload = {
            "node_color": node_color,
            "paths": {c: path for c, path in res.paths.items()},
            "path_edges": {
                c: [[u, v] for u, v in edges]
                for c, edges in res.path_edges.items()
            },
            "stats": solve_stats,
            "unique": res.unique,
            "graph": _graph_payload(puzzle),
        }
        if req.import_id:
            _store_image_import_solve(
                req.import_id,
                name=req.name,
                text=req.text,
                result=payload,
            )
        return payload
    except Exception as e:
        if req.import_id:
            try:
                attached = _store_image_import_solve(
                    req.import_id,
                    name=req.name,
                    text=req.text,
                    error=str(e),
                )
                if attached:
                    _auto_flag_image_import(
                        req.import_id,
                        reason=f"Automated review: solve failed: {str(e)[:600]}",
                    )
            except Exception:
                pass
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


@app.get("/image-imports")
def list_image_imports(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status: str = Query(default="all"),
    flagged: Optional[bool] = Query(default=None),
    search: str = Query(default="", max_length=200),
    order: str = Query(default="newest"),
) -> Dict[str, Any]:
    normalized_status = status.strip().lower()
    if normalized_status not in {"all", "processed", "solved", "unknown", "failed"}:
        raise HTTPException(status_code=400, detail="Unknown image-import status filter")
    normalized_order = order.strip().lower()
    if normalized_order not in {"newest", "oldest"}:
        raise HTTPException(status_code=400, detail="order must be newest or oldest")
    query = search.strip().casefold()
    try:
        entries, total = _query_image_import_index(
            limit=limit,
            offset=offset,
            status=normalized_status,
            flagged=flagged,
            query=query,
            order=normalized_order,
        )
    except Exception:
        # Keep archive browsing available if SQLite is unavailable on an
        # unusual filesystem. JSON remains the source of truth.
        records: List[Dict[str, Any]] = []
        base = _image_imports_dir()
        if base.exists():
            for record_path in base.glob("*/record.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if isinstance(record, dict):
                        summary = _image_import_summary(record)
                        failed = summary.get("status") == "failed" or summary.get("solve_status") == "failed"
                        solved = summary.get("solve_status") == "solved"
                        matches_status = (
                            normalized_status == "all"
                            or (normalized_status == "failed" and failed)
                            or (normalized_status == "solved" and solved)
                            or (normalized_status == "processed" and summary.get("status") == "processed")
                            or (normalized_status == "unknown" and not failed and not solved)
                        )
                        searchable = " ".join(
                            str(summary.get(key) or "")
                            for key in ("original_name", "generated_name", "geometry", "flag_reason")
                        ).casefold()
                        matches_flagged = flagged is None or summary["flagged"] is flagged
                        if matches_status and matches_flagged and (not query or query in searchable):
                            records.append(summary)
                except Exception:
                    continue
        records.sort(
            key=lambda item: float(item.get("updated_at", item.get("created_at", 0))),
            reverse=normalized_order == "newest",
        )
        total = len(records)
        entries = records[offset : offset + limit]
    return {
        "entries": entries,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(entries) < total,
    }


@app.post("/image/jobs")
async def create_image_job(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    options_json: str = Form("{}"),
) -> Dict[str, Any]:
    if not files or len(files) > 200:
        raise HTTPException(status_code=400, detail="An image job requires 1 to 200 files")
    try:
        options = _normalize_image_job_options(json.loads(options_json))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options_json: {exc}") from exc

    job_id = uuid.uuid4().hex
    job_dir = _image_job_dir(job_id)
    items: List[Dict[str, Any]] = []
    total_bytes = 0
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
        for index, upload in enumerate(files):
            data = await upload.read()
            total_bytes += len(data)
            if len(data) > 50 * 1024 * 1024 or total_bytes > 250 * 1024 * 1024:
                raise ValueError("Image job exceeds the 50 MB/file or 250 MB total limit")
            suffix = Path(upload.filename or "image").suffix.lower()
            if suffix not in _ARCHIVE_IMAGE_SUFFIXES:
                suffix = ".bin"
            data, sanitized_suffix = _archive_bytes_for_processing(data, options)
            if sanitized_suffix:
                suffix = sanitized_suffix
            stored_name = f"{index:04d}{suffix}"
            (job_dir / stored_name).write_bytes(data)
            items.append(
                {
                    "index": index,
                    "original_name": Path(upload.filename or f"image-{index}").name,
                    "content_type": upload.content_type or "application/octet-stream",
                    "byte_size": len(data),
                    "stored_name": stored_name,
                    "status": "queued",
                }
            )
        created_at = time.time()
        record = {
            "id": job_id,
            "status": "queued",
            "created_at": created_at,
            "updated_at": created_at,
            "total": len(items),
            "completed": 0,
            "failed": 0,
            "options": options,
            "items": items,
        }
        _write_image_job(job_id, record)
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Could not create image job: {exc}") from exc

    background_tasks.add_task(_run_image_job, job_id)
    return record


@app.get("/image/jobs")
def list_image_jobs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    jobs: List[Dict[str, Any]] = []
    base = _image_jobs_dir()
    if base.exists():
        for path in base.glob("*/job.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    summary = {key: value for key, value in record.items() if key not in {"items", "options"}}
                    jobs.append(summary)
            except Exception:
                continue
    jobs.sort(key=lambda item: float(item.get("updated_at", 0)), reverse=True)
    return {
        "entries": jobs[offset : offset + limit],
        "total": len(jobs),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(jobs),
    }


@app.get("/image/jobs/{job_id}")
def get_image_job(job_id: str) -> Dict[str, Any]:
    return _read_image_job(job_id)


@app.get("/image/jobs/{job_id}/items/{item_index}/image")
def get_image_job_source(job_id: str, item_index: int) -> FileResponse:
    record = _read_image_job(job_id)
    items = record.get("items") if isinstance(record.get("items"), list) else []
    if item_index < 0 or item_index >= len(items) or not isinstance(items[item_index], dict):
        raise HTTPException(status_code=404, detail="Image job item not found")
    item = items[item_index]
    stored_name = str(item.get("stored_name", ""))
    if not stored_name or Path(stored_name).name != stored_name:
        raise HTTPException(status_code=500, detail="Stored image job item is invalid")
    source_path = _image_job_dir(job_id) / stored_name
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Image job source is missing")
    return FileResponse(
        source_path,
        media_type=str(item.get("content_type") or "application/octet-stream"),
        filename=str(item.get("original_name") or stored_name),
    )


@app.post("/image/jobs/{job_id}/retry")
def retry_image_job(job_id: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    def requeue(record: Dict[str, Any]) -> None:
        if record.get("status") not in {"completed", "failed", "interrupted"}:
            raise HTTPException(status_code=409, detail="Image job is already queued or running")
        items = record.get("items") if isinstance(record.get("items"), list) else []
        for item in items:
            if isinstance(item, dict) and item.get("status") == "failed":
                item["status"] = "queued"
                item.pop("error", None)
        record["completed"] = sum(
            isinstance(item, dict) and item.get("status") == "processed" for item in items
        )
        record["failed"] = 0
        record["status"] = "queued"
        record.pop("finished_at", None)

    record = _update_image_job(job_id, requeue)
    background_tasks.add_task(_run_image_job, job_id)
    return record


MAX_IMAGE_UPLOAD_BYTES = 50 * 1024 * 1024


@app.post("/image/uploads")
async def create_image_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Store an image once so later pipeline stages can reference it by id."""

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(data) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 50 MB upload limit")
    store = _image_upload_store()
    stored = await asyncio.to_thread(
        store.put,
        data,
        filename=Path(file.filename or "image").name,
        content_type=file.content_type or "application/octet-stream",
    )
    return {
        "upload_id": stored.upload_id,
        "byte_size": len(data),
        "expires_in_seconds": int(store.ttl_seconds),
    }


async def _resolve_image_input(file: Any, upload_id: Any) -> Tuple[Any, bytes]:
    """Return (file-like with filename/content_type, bytes) from a file or an upload id.

    Endpoints are also called directly (server jobs), where omitted parameters
    arrive as FastAPI parameter objects rather than None.
    """

    if isinstance(upload_id, str) and upload_id.strip():
        try:
            stored = await asyncio.to_thread(_image_upload_store().get, upload_id.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if stored is None:
            # 410 tells the browser to upload the image again and retry.
            raise HTTPException(status_code=410, detail="upload_expired: upload the image again")
        return stored, stored.data
    if isinstance(file, StarletteUploadFile):
        return file, await file.read()
    raise HTTPException(status_code=400, detail="Provide an image file or an upload_id")


@app.post("/image-imports/failed")
async def archive_failed_image_import(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    error: str = Form(...),
    stage: str = Form("processing"),
    source_mode: str = Form("screenshot"),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    try:
        image, _input_info, effective_source_mode = _load_image_for_source_mode(data, source_mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid screenshot: {exc}") from exc
    try:
        record = _store_image_import(
            data=data,
            original_name=file.filename or "image",
            content_type=file.content_type,
            image_size=(image.width, image.height),
            result=None,
            processing={
                "stage": stage,
                "source_mode": source_mode,
                "source_mode_effective": effective_source_mode,
            },
            status="failed",
            error=error[:4000],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not archive failed screenshot: {exc}") from exc
    return _image_import_summary(record)


@app.post("/image-imports/bulk-delete")
def bulk_delete_image_imports(request: ImageImportBulkDeleteRequest) -> Dict[str, Any]:
    unique_ids = list(dict.fromkeys(request.ids))
    record_dirs = [(import_id, _image_import_dir(import_id)) for import_id in unique_ids]
    deleted: List[str] = []
    missing: List[str] = []
    for import_id, record_dir in record_dirs:
        with _locked_import(import_id):
            if not (record_dir / "record.json").exists():
                missing.append(import_id)
                continue
            shutil.rmtree(record_dir)
            deleted.append(import_id)
    try:
        _delete_image_import_index(deleted)
    except Exception:
        _invalidate_image_import_index_backfill()
    return {"deleted": deleted, "missing": missing}


@app.post("/image-imports/{import_id}/failure")
def record_image_import_reprocess_failure(
    import_id: str,
    request: ImageImportReprocessFailureRequest,
) -> Dict[str, Any]:
    record = _record_image_import_reprocess_failure(
        import_id,
        error=request.error,
        stage=request.stage,
    )
    return _image_import_summary(record)


@app.post("/image-imports/{import_id}/flag")
def flag_image_import(
    import_id: str,
    request: ImageImportFlagRequest,
) -> Dict[str, Any]:
    record = _set_image_import_flag(import_id, flagged=True, reason=request.reason)
    return _image_import_summary(record)


@app.delete("/image-imports/{import_id}/flag")
def unflag_image_import(import_id: str) -> Dict[str, Any]:
    record = _set_image_import_flag(import_id, flagged=False)
    return _image_import_summary(record)


@app.get("/image-imports/{import_id}/image")
def get_image_import_image(import_id: str):
    record = _read_image_import(import_id)
    image_file = str(record.get("image_file", ""))
    if Path(image_file).name != image_file:
        raise HTTPException(status_code=500, detail="Stored image import has an invalid image path")
    image_path = _image_import_dir(import_id) / image_file
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Stored screenshot not found")
    return FileResponse(image_path, media_type=str(record.get("content_type") or "application/octet-stream"))


@app.get("/image-imports/{import_id}")
def get_image_import(import_id: str) -> Dict[str, Any]:
    return _read_image_import(import_id)


@app.delete("/image-imports/{import_id}")
def delete_image_import(import_id: str) -> Dict[str, Any]:
    record_dir = _image_import_dir(import_id)
    with _locked_import(import_id):
        if not (record_dir / "record.json").exists():
            raise HTTPException(status_code=404, detail="Image import not found")
        shutil.rmtree(record_dir)
    try:
        _delete_image_import_index([import_id])
    except Exception:
        _invalidate_image_import_index_backfill()
    return {"deleted": True, "id": import_id}


@app.post("/image/crop/auto")
@_offload_image_endpoint
async def image_auto_crop(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    invert: bool = Form(False),
    padding: int = Form(6),
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    file, data = await _resolve_image_input(file, upload_id)
    image = load_image(data)
    seed_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    image_for_crop = apply_crop(image, seed_crop)
    crop = auto_crop(image_for_crop, threshold=threshold, invert=invert, padding=padding)
    message: Optional[str] = None
    if crop is not None and seed_crop is not None:
        seed_area = float(max(1, seed_crop.width * seed_crop.height))
        refined_area = float(max(1, crop.width * crop.height))
        if refined_area < seed_area * 0.38:
            # Prevent over-aggressive refinement (e.g. snapping onto a single terminal).
            crop = seed_crop
            message = "Auto-crop refinement was too aggressive; kept the seed crop."
        else:
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
            message = "No refined crop found; kept the seed crop."
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
    payload: Dict[str, Any] = {
        "crop": {"x": crop.x, "y": crop.y, "width": crop.width, "height": crop.height},
        "image_size": {"width": image.width, "height": image.height},
        "seed_crop": (
            {"x": seed_crop.x, "y": seed_crop.y, "width": seed_crop.width, "height": seed_crop.height}
            if seed_crop
            else None
        ),
    }
    if message:
        payload["message"] = message
    return payload


@app.post("/image/photo/prepare")
@_offload_image_endpoint
async def image_photo_prepare(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    photo_corners_json: Optional[str] = Form(None),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    invert: bool = Form(False),
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    file, data = await _resolve_image_input(file, upload_id)
    image, input_info, _effective_source_mode = _load_image_for_source_mode(data, "camera")
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode="camera",
        image_size=image.size,
    )
    crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    camera = _prepare_camera_pipeline_images(
        image,
        crop=crop,
        threshold=threshold,
        invert=invert,
        manual_corners=photo_corners,
        cache_key=_photo_cache_key(data),
    )
    perspective_info = camera.perspective_info
    auto_crop_info = camera.auto_crop_info
    photo_info = camera.photo_info
    preview = camera.color.copy()
    preview.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=88, optimize=True)
    return {
        "source_mode": "camera",
        "input": input_info,
        "photo": photo_info,
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
        "preview_data_url": f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode('ascii')}",
    }


@app.post("/image/classify")
@_offload_image_endpoint
async def image_classify(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    source_mode: str = Form("screenshot"),
    photo_corners_json: Optional[str] = Form(None),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    line_threshold: float = Form(0.6),
    invert: bool = Form(False),
    perspective: bool = Form(False),
    level_hint: Optional[str] = Form(None),
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    image, input_info, effective_source_mode = _load_image_for_source_mode(data, source_mode)
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode=source_mode,
        image_size=image.size,
    )
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    photo_info: Optional[Dict[str, Any]] = None
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if effective_source_mode == "camera":
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=threshold,
            invert=invert,
            manual_corners=photo_corners,
            cache_key=_photo_cache_key(data),
        )
        warped = camera.color
        perspective_info = camera.perspective_info
        auto_crop_info = camera.auto_crop_info
        photo_info = camera.photo_info
    elif crop is None:
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
    if effective_source_mode != "camera":
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
        "source_mode": source_mode,
        "source_mode_effective": effective_source_mode,
        "input": input_info,
        "photo": photo_info,
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/grid/detect")
@_offload_image_endpoint
async def image_grid_detect(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    source_mode: str = Form("screenshot"),
    photo_corners_json: Optional[str] = Form(None),
    target_type: str = Form("square"),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    threshold: int = Form(230),
    line_threshold: float = Form(0.6),
    invert: bool = Form(False),
    perspective: bool = Form(False),
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    image, input_info, effective_source_mode = _load_image_for_source_mode(data, source_mode)
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode=source_mode,
        image_size=image.size,
    )
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    photo_info: Optional[Dict[str, Any]] = None
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if effective_source_mode == "camera":
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=threshold,
            invert=invert,
            manual_corners=photo_corners,
            cache_key=_photo_cache_key(data),
        )
        warped = camera.color
        geometry_warped = camera.geometry
        perspective_info = camera.perspective_info
        auto_crop_info = camera.auto_crop_info
        photo_info = camera.photo_info
    elif crop is None:
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
    if effective_source_mode != "camera":
        cropped = apply_crop(image, crop)
        warped, perspective_info = _maybe_perspective(cropped, perspective)
        geometry_warped = warped
    raw_target = str(target_type or "square").strip().lower()
    normalized_target = _normalize_level_geometry(raw_target, default="square")

    if normalized_target == "circle":
        circle_grid, circle_info = detect_circle_grid(geometry_warped, min_sectors=3, max_sectors=32)
        if circle_grid is None:
            return {
                "grid": None,
                "image_size": {"width": image.width, "height": image.height},
                "source_mode": source_mode,
                "source_mode_effective": effective_source_mode,
                "input": input_info,
                "photo": photo_info,
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
            "source_mode": source_mode,
            "source_mode_effective": effective_source_mode,
            "input": input_info,
            "photo": photo_info,
            "perspective": perspective_info,
            "auto_crop": auto_crop_info,
        }

    grid = detect_grid(geometry_warped, threshold=threshold, line_threshold=line_threshold, invert=invert)
    if grid is None and raw_target in {"auto", ""}:
        circle_grid, circle_info = detect_circle_grid(geometry_warped, min_sectors=3, max_sectors=32)
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
                "source_mode": source_mode,
                "source_mode_effective": effective_source_mode,
                "input": input_info,
                "photo": photo_info,
                "perspective": perspective_info,
                "auto_crop": auto_crop_info,
            }
    if grid is None:
        return {
            "grid": None,
            "image_size": {"width": image.width, "height": image.height},
            "source_mode": source_mode,
            "source_mode_effective": effective_source_mode,
            "input": input_info,
            "photo": photo_info,
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
        "source_mode": source_mode,
        "source_mode_effective": effective_source_mode,
        "input": input_info,
        "photo": photo_info,
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/terminals/detect")
@_offload_image_endpoint
async def image_terminals_detect(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    source_mode: str = Form("screenshot"),
    photo_corners_json: Optional[str] = Form(None),
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
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    image, input_info, effective_source_mode = _load_image_for_source_mode(data, source_mode)
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode=source_mode,
        image_size=image.size,
    )
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    photo_info: Optional[Dict[str, Any]] = None
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    invalid_mask: Optional[Image.Image] = None
    if effective_source_mode == "camera":
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=230,
            invert=False,
            manual_corners=photo_corners,
            expected_grid=(rows, cols),
            cache_key=_photo_cache_key(data),
        )
        warped = camera.color
        invalid_mask = camera.invalid_mask
        perspective_info = camera.perspective_info
        auto_crop_info = camera.auto_crop_info
        photo_info = camera.photo_info
    elif crop is None:
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
    if effective_source_mode != "camera":
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
            invalid_mask=invalid_mask,
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
            invalid_mask=invalid_mask,
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
        "source_mode": source_mode,
        "source_mode_effective": effective_source_mode,
        "input": input_info,
        "photo": photo_info,
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
    }


@app.post("/image/generate")
@_offload_image_endpoint
async def image_generate(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    source_mode: str = Form("screenshot"),
    photo_corners_json: Optional[str] = Form(None),
    replace_import_id: Optional[str] = Form(None),
    target_type: str = Form("auto"),
    grid_width: Optional[int] = Form(None),
    grid_height: Optional[int] = Form(None),
    graph_layout: str = Form("grid"),
    graph_nodes: int = Form(10),
    output_schema_version: int = Form(1),
    auto_terminals: bool = Form(True),
    auto_classify: bool = Form(True),
    expected_flow_count: Optional[int] = Form(None),
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
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    if output_schema_version not in {1, 2}:
        raise HTTPException(status_code=400, detail="output_schema_version must be 1 or 2")
    if expected_flow_count is not None and not 1 <= expected_flow_count <= 26:
        raise HTTPException(status_code=400, detail="expected_flow_count must be between 1 and 26")
    image, input_info, effective_source_mode = _load_image_for_source_mode(
        data,
        source_mode,
    )
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode=source_mode,
        image_size=image.size,
    )
    expected_flow_source: Optional[str] = "request" if expected_flow_count is not None else None
    expected_flow_ocr_message: Optional[str] = None

    def apply_flow_count_ocr(ocr_source: Image.Image) -> None:
        nonlocal expected_flow_count, expected_flow_source, expected_flow_ocr_message
        ocr_text, expected_flow_ocr_message = _ocr_image_text(ocr_source)
        inferred_flow_count = parse_expected_flow_count(ocr_text)
        if inferred_flow_count is not None:
            expected_flow_count = inferred_flow_count
            expected_flow_source = "ocr"

    if auto_terminals and expected_flow_count is None and effective_source_mode != "camera":
        apply_flow_count_ocr(image)
    manual_crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    crop = manual_crop
    photo_info: Optional[Dict[str, Any]] = None
    invalid_mask: Optional[Image.Image] = None
    warp_context: Optional[Image.Image] = None
    auto_crop_info: Dict[str, Any] = {
        "applied": False,
        "source": "manual" if manual_crop is not None else "auto",
    }
    if effective_source_mode == "camera":
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=threshold,
            invert=invert,
            manual_corners=photo_corners,
            expected_grid=(grid_height, grid_width) if grid_height and grid_width else None,
            cache_key=_photo_cache_key(data),
        )
        warped = camera.color
        geometry_warped = camera.geometry
        invalid_mask = camera.invalid_mask
        warp_context = camera.warp_context
        perspective_info = camera.perspective_info
        auto_crop_info = camera.auto_crop_info
        photo_info = camera.photo_info
        if auto_terminals and expected_flow_count is None:
            # The level header is readable once the display is straightened;
            # the raw photo is skewed and includes the surroundings.
            apply_flow_count_ocr(camera.display)
    elif crop is None:
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
    if effective_source_mode != "camera":
        cropped = apply_crop(image, crop)
        warped, perspective_info = _maybe_perspective(cropped, perspective)
        geometry_warped = warped

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
    if source_mode != "screenshot":
        meta["source_mode"] = source_mode

    detection_info: Dict[str, Any] = {
        "perspective": perspective_info,
        "auto_crop": auto_crop_info,
        "expected_flow_count": expected_flow_count,
        "expected_flow_count_source": expected_flow_source,
    }
    if source_mode != "screenshot":
        detection_info.update(
            source_mode=source_mode,
            source_mode_effective=effective_source_mode,
            input=input_info,
            photo=photo_info,
        )
    if effective_source_mode == "camera":
        detection_info["photo_review"] = _camera_photo_review(photo_info)
    if expected_flow_ocr_message and expected_flow_count is None:
        detection_info["expected_flow_count_message"] = expected_flow_ocr_message
    requested_target = str(target_type or "auto").strip().lower() or "auto"
    classification_warnings: List[str] = []
    if photo_info is not None:
        quality = photo_info.get("quality") if isinstance(photo_info.get("quality"), dict) else {}
        classification_warnings.extend(str(item) for item in quality.get("warnings", []) if str(item))

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

    auto_level_signals = level_type.get("signals") if isinstance(level_type.get("signals"), dict) else {}
    if requested_target == "auto" and auto_level_signals.get("recommended_graph_layout") == "regions":
        target_used = "graph"

    if effective_source_mode == "camera" and _camera_board_is_not_square(level_type, graph_layout):
        # The square-lattice board warp crops or stretches hex, circle, and
        # region boards, and the square-lattice display choice cannot see
        # them; redo preparation (display candidates are cached) without it.
        board_was_warped = bool(((photo_info or {}).get("board") or {}).get("selected"))
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=threshold,
            invert=invert,
            manual_corners=photo_corners,
            cache_key=_photo_cache_key(data),
            board_warp=False,
            board_mesh_crop=str(level_type.get("geometry") or "").lower() == "hex",
        )
        warped = camera.color
        geometry_warped = camera.geometry
        invalid_mask = camera.invalid_mask
        warp_context = camera.warp_context
        perspective_info = camera.perspective_info
        auto_crop_info = camera.auto_crop_info
        photo_info = camera.photo_info
        if board_was_warped:
            photo_info["board_warp_skipped"] = "non-square board"
        detection_info.update(perspective=perspective_info, auto_crop=auto_crop_info, photo=photo_info)

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

    level_signals = level_type.get("signals") if isinstance(level_type.get("signals"), dict) else {}
    if (
        target_used == "graph"
        and graph_layout == "grid"
        and level_signals.get("recommended_graph_layout") == "regions"
    ):
        graph_layout = "regions"
        detection_info["graph_layout_auto_selected"] = "regions"

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

    def finalize_generation(payload: Dict[str, Any]) -> Dict[str, Any]:
        acceleration = acceleration_capabilities()
        payload.setdefault("detection", {}).setdefault("acceleration", acceleration)
        if effective_source_mode == "camera":
            # Re-evaluate with terminal evidence, which exists only now.
            payload["detection"]["photo_review"] = _camera_photo_review(
                photo_info,
                payload["detection"].get("terminal_completeness"),
                detection={**payload["detection"], "graph_layout": graph_layout},
            )
        processing = {
            "source_mode": source_mode,
            "source_mode_effective": effective_source_mode,
            "photo_corners": photo_corners,
            "photo_preprocessing_version": (
                photo_info.get("version") if isinstance(photo_info, dict) else None
            ),
            "target_type": requested_target,
            "target_type_used": target_used,
            "output_schema_version": output_schema_version,
            "graph_layout": graph_layout,
            "graph_nodes": graph_nodes,
            "grid_width": grid_width,
            "grid_height": grid_height,
            "auto_terminals": auto_terminals,
            "auto_classify": auto_classify,
            "expected_flow_count": expected_flow_count,
            "expected_flow_count_source": expected_flow_source,
            "crop": (
                {"x": crop.x, "y": crop.y, "width": crop.width, "height": crop.height}
                if crop is not None
                else None
            ),
            "threshold": threshold,
            "line_threshold": line_threshold,
            "invert": invert,
            "perspective": perspective,
            "sat_threshold": sat_threshold,
            "brightness_min": brightness_min,
            "brightness_max": brightness_max,
            "margin_ratio": margin_ratio,
            "cluster_threshold": cluster_threshold,
            "bg_threshold": bg_threshold,
            "acceleration": acceleration,
            "reprocessed": replace_import_id is not None,
        }
        try:
            if replace_import_id is not None:
                record = _replace_image_import(
                    replace_import_id,
                    data=data,
                    result=payload,
                    processing=processing,
                )
            else:
                record = _store_image_import(
                    data=data,
                    original_name=file.filename or "image",
                    content_type=file.content_type,
                    image_size=(image.width, image.height),
                    result=payload,
                    processing=processing,
                )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not archive processed screenshot: {exc}") from exc
        payload["import_id"] = record["id"]
        payload["archived_at"] = record["created_at"]
        completeness = payload.get("detection", {}).get("terminal_completeness")
        if isinstance(completeness, dict) and completeness.get("review_required"):
            flagged_record = _auto_flag_image_import(
                str(record["id"]),
                reason=f"Automated review: {str(completeness.get('reason') or 'terminal detection needs review')}",
            )
            payload["review_flagged"] = True
            payload["flag_reason"] = flagged_record.get("flag_reason")
        photo_review = payload.get("detection", {}).get("photo_review")
        if (
            not payload.get("review_flagged")
            and isinstance(photo_review, dict)
            and photo_review.get("required")
        ):
            reasons = photo_review.get("reasons") if isinstance(photo_review.get("reasons"), list) else []
            flagged_record = _auto_flag_image_import(
                str(record["id"]),
                reason=f"Automated camera review: {str(reasons[0] if reasons else 'photo quality needs review')}",
            )
            payload["review_flagged"] = True
            payload["flag_reason"] = flagged_record.get("flag_reason")
        return payload

    if target_used in {"square", "hex", "circle"}:
        if any(manual_edge_overrides.values()):
            classification_warnings.append("Manual edge overrides are only applied for graph target output.")
        circle_grid = None
        circle_grid_info: Dict[str, Any] = {}
        if grid_width is None or grid_height is None:
            if target_used == "circle":
                circle_grid, circle_grid_info = detect_circle_grid(
                    geometry_warped,
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
                grid = detect_grid(
                    geometry_warped,
                    threshold=threshold,
                    line_threshold=line_threshold,
                    invert=invert,
                )
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
                    geometry_warped,
                    min_sectors=max(3, min(int(grid_width), 12)),
                    max_sectors=max(int(grid_width) + 6, 24),
                )
                if circle_grid_info:
                    detection_info["circle_grid"] = circle_grid_info

        if grid_width <= 0 or grid_height <= 0:
            raise HTTPException(status_code=400, detail="Invalid grid size.")

        bridge_cells: List[Tuple[int, int]] = []
        bridge_info: Dict[str, Any] = {}
        if target_used == "square" and "bridges" in level_modifiers:
            bridge_cells, bridge_info = detect_bridge_cells(
                warped,
                rows=grid_height,
                cols=grid_width,
            )
            detection_info["bridge_info"] = bridge_info
            classification_warnings.extend(str(item) for item in bridge_info.get("warnings", []))
        bridge_cell_set = set(bridge_cells)

        terminals_payload: List[Dict[str, Any]] = []
        terminal_warnings: List[str] = []
        terminal_info: Dict[str, Any] = {}
        placements: List[Any] = []
        if auto_terminals:
            if target_used == "circle":
                placements, info = detect_circle_terminals(
                    warped,
                    invalid_mask=invalid_mask,
                    rings=grid_height,
                    sectors=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    circle_grid=circle_grid,
                    expected_pairs=expected_flow_count,
                )
            else:
                placements, info = detect_terminals(
                    warped,
                    invalid_mask=invalid_mask,
                    rows=grid_height,
                    cols=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    expected_pairs=expected_flow_count,
                    enforce_square_parity=target_used == "square" and not bridge_cell_set,
                )
            if bridge_cell_set:
                placements = [
                    placement
                    for placement in placements
                    if (placement.row, placement.col) not in bridge_cell_set
                ]
            grid_tokens, grid_warnings = build_grid(
                rows=grid_height,
                cols=grid_width,
                terminals=placements,
                fallback=False,
            )
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
            grid_tokens, grid_warnings = build_grid(
                rows=grid_height,
                cols=grid_width,
                terminals=[],
                fallback=False,
            )
            terminal_warnings = grid_warnings

        terminal_completeness = (
            _terminal_completeness_payload(
                terminal_info,
                detected_endpoints=len(placements),
                expected_pairs=expected_flow_count,
            )
            if auto_terminals
            else {
                "status": "not_applicable",
                "expected_pairs": expected_flow_count,
                "detected_pairs": 0,
                "detected_endpoints": 0,
                "pairing_complete": False,
                "confidence": 0.0,
                "near_miss_count": 0,
                "recovered_pair_count": 0,
                "review_required": False,
                "reason": "Automatic terminal detection was disabled.",
            }
        )
        terminal_info.update(
            {
                "detected_endpoints": terminal_completeness["detected_endpoints"],
                "detected_pairs": terminal_completeness["detected_pairs"],
                "expected_pairs": terminal_completeness["expected_pairs"],
                "completeness_status": terminal_completeness["status"],
            }
        )
        if terminal_completeness["review_required"]:
            terminal_warnings.append(str(terminal_completeness["reason"]))
        detection_info["terminals"] = terminals_payload
        detection_info["terminal_info"] = terminal_info
        detection_info["terminal_completeness"] = terminal_completeness

        for bridge_row, bridge_col in bridge_cells:
            if 0 <= bridge_row < grid_height and 0 <= bridge_col < grid_width:
                if grid_tokens[bridge_row][bridge_col] == ".":
                    grid_tokens[bridge_row][bridge_col] = "+"
                else:
                    terminal_warnings.append(
                        f"Bridge marker at ({bridge_col},{bridge_row}) overlaps a terminal and was ignored."
                    )

        flow_meta = dict(meta)
        detected_terminal_colors = _terminal_color_map_from_placements(placements)
        if detected_terminal_colors:
            flow_meta["terminal_colors"] = json.dumps(
                detected_terminal_colors,
                separators=(",", ":"),
            )
        flow_text = build_flow_text(target_used, grid_tokens, flow_meta)
        name = f"{Path(meta.get('source_image', 'image')).stem}_{target_used}_{grid_width}x{grid_height}.flow"
        output_text = flow_text
        if output_schema_version == 2:
            try:
                output_obj = _image_grid_schema_v2(
                    target_type=target_used,
                    grid=grid_tokens,
                    width=grid_width,
                    height=grid_height,
                    meta=meta,
                    terminal_placements=placements,
                    level_modifiers=level_modifiers,
                    import_detection=detection_info,
                )
                output_text = json.dumps(output_obj, indent=2)
                name = str(Path(name).with_suffix(".json"))
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not encode generated grid as schema v2: {exc}",
                ) from exc
        detection_info["warnings"] = classification_warnings + terminal_warnings
        return finalize_generation(
            {"name": name, "text": output_text, "metadata": meta, "detection": detection_info}
        )

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
                    invalid_mask=invalid_mask,
                    nodes=obj.get("space", {}).get("nodes", {}),
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    expected_pairs=expected_flow_count,
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
                    obj["terminals"] = {}
                    graph_terminal_warnings.append("No topology terminals were confidently detected.")
            else:
                obj["terminals"] = {}
            name = f"{Path(meta.get('source_image', 'image')).stem}_{target_used}_{topo_width}x{topo_height}.json"
        elif graph_layout == "regions":
            nodes_obj, region_edges, region_info = detect_region_topology(
                # Camera photos lift the black background and blur the thin
                # cell outlines, so the global threshold on the color image
                # merges cells; the contrast-enhanced geometry view keeps them.
                # For screenshots geometry_warped is the same image as warped.
                geometry_warped,
                prefer_hex=level_geometry == "hex",
            )
            if len(nodes_obj) < 2:
                warnings = region_info.get("warnings", [])
                suffix = f" ({'; '.join(str(item) for item in warnings)})" if warnings else ""
                raise HTTPException(
                    status_code=400,
                    detail=f"Region graph detection found fewer than two cells{suffix}.",
                )
            if bool(region_info.get("regular_hex_lattice")) and level_geometry != "hex":
                previous_geometry = level_geometry
                level_geometry = "hex"
                meta["level_type_geometry"] = "hex"
                level_type["id"] = _level_type_id("hex", level_modifiers)
                level_type["geometry"] = "hex"
                # A clipped/irregular hex silhouette still needs an explicit
                # region graph even though its local cell lattice is hexagonal.
                level_type["can_emit_flow"] = False
                level_type["recommended_target_type"] = "graph"
                level_type["recommended_output_format"] = "json"
                level_signals = (
                    dict(level_type.get("signals", {}))
                    if isinstance(level_type.get("signals"), dict)
                    else {}
                )
                level_signals["regular_hex_lattice"] = True
                level_signals["recommended_graph_layout"] = "regions"
                level_type["signals"] = level_signals
                detection_info["region_geometry_override"] = {
                    "from": previous_geometry,
                    "to": "hex",
                    "reason": "regular_hex_lattice",
                }
            space: Dict[str, Any] = {
                "type": "graph",
                "topology": "regions",
                "nodes": nodes_obj,
                "edges": region_edges,
            }
            add_pairs = add_edges + list(manual_edge_overrides["warps"])
            remove_pairs = remove_edges + list(manual_edge_overrides["walls"])
            if add_pairs or remove_pairs:
                space["edge_overrides"] = {}
                if add_pairs:
                    space["edge_overrides"]["add"] = [[u, v] for u, v in add_pairs]
                if remove_pairs:
                    space["edge_overrides"]["remove"] = [[u, v] for u, v in remove_pairs]
            if manual_edge_overrides["warps"]:
                space["warps"] = [[u, v] for u, v in manual_edge_overrides["warps"]]
            if manual_edge_overrides["walls"]:
                space["walls"] = [[u, v] for u, v in manual_edge_overrides["walls"]]
            obj = {"space": space, "terminals": {}, "meta": meta}
            modifier_info["regions"] = region_info
            graph_terminal_warnings.extend([str(w) for w in region_info.get("warnings", [])])
            if auto_terminals:
                node_placements, node_info = detect_terminals_on_nodes(
                    warped,
                    invalid_mask=invalid_mask,
                    nodes=nodes_obj,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    expected_pairs=expected_flow_count,
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
                    obj["terminals"] = {}
                    graph_terminal_warnings.append("No region terminals were confidently detected.")
                protected_nodes = {
                    str(node_id)
                    for pairs in manual_edge_overrides.values()
                    for pair in pairs
                    for node_id in pair
                }
                region_edges, repaired_region_seams = repair_nonterminal_region_leaves(
                    nodes_obj,
                    region_edges,
                    terminal_nodes=(
                        node_id
                        for node_ids in inferred_terminals.values()
                        for node_id in node_ids
                    ),
                    protected_nodes=protected_nodes,
                )
                (
                    nodes_obj,
                    region_edges,
                    region_tiles,
                    region_crossovers,
                    repaired_region_seams,
                    forbidden_region_seams,
                    crossover_under_channels,
                ) = build_region_crossover_channels(
                    nodes_obj,
                    region_edges,
                    repaired_region_seams,
                )
                space["nodes"] = nodes_obj
                space["edges"] = region_edges
                if region_crossovers:
                    obj["tiles"] = region_tiles
                    space["crossovers"] = region_crossovers
                inferred_region_seams: List[Tuple[str, str]] = []
                seam_inference_info: Optional[Dict[str, Any]] = None
                if repaired_region_seams and inferred_terminals:
                    inferred_region_seams, seam_inference_info = infer_bounded_region_seams(
                        nodes_obj,
                        region_edges,
                        inferred_terminals,
                        tiles=region_tiles if region_crossovers else None,
                        excluded_candidate_nodes=crossover_under_channels,
                        forbidden_edges=forbidden_region_seams,
                    )
                    if inferred_region_seams:
                        region_edges = sorted(
                            {
                                tuple(sorted((str(left), str(right))))
                                for left, right in region_edges + inferred_region_seams
                            }
                        )
                nodes_obj, region_edges, pruned_region_leaves = prune_nonterminal_region_leaves(
                    nodes_obj,
                    region_edges,
                    terminal_nodes=(
                        node_id
                        for node_ids in inferred_terminals.values()
                        for node_id in node_ids
                    ),
                    protected_nodes=protected_nodes,
                )
                if region_crossovers:
                    kept_nodes = set(nodes_obj)
                    region_tiles = {
                        tile_id: [
                            channel_id
                            for channel_id in channels
                            if channel_id in kept_nodes
                        ]
                        for tile_id, channels in region_tiles.items()
                        if any(channel_id in kept_nodes for channel_id in channels)
                    }
                    region_crossovers = [
                        crossover
                        for crossover in region_crossovers
                        if all(
                            str(node_id) in kept_nodes
                            for node_id in crossover.get("under_path", [])
                        )
                        and str(crossover.get("surface_channel")) in kept_nodes
                    ]
                    obj["tiles"] = region_tiles
                    if region_crossovers:
                        space["crossovers"] = region_crossovers
                    else:
                        space.pop("crossovers", None)
                if pruned_region_leaves:
                    space["nodes"] = nodes_obj
                    space["edges"] = region_edges
                    region_info["pruned_nonterminal_leaves"] = pruned_region_leaves
                    region_info["regions"] = len(region_tiles) if region_crossovers else len(nodes_obj)
                    region_info["edges"] = len(region_edges)
                    graph_terminal_info["pruned_nonterminal_leaves"] = pruned_region_leaves
                elif repaired_region_seams or inferred_region_seams:
                    space["edges"] = region_edges
                all_region_seams = sorted(
                    {
                        tuple(sorted((left, right)))
                        for left, right in repaired_region_seams + inferred_region_seams
                    }
                )
                if all_region_seams:
                    space["seams"] = [
                        [left, right] for left, right in all_region_seams
                    ]
                    region_info["repaired_seams"] = [
                        [left, right] for left, right in all_region_seams
                    ]
                    region_info["edges"] = len(region_edges)
                    graph_terminal_info["repaired_seams"] = [
                        [left, right] for left, right in all_region_seams
                    ]
                if region_crossovers:
                    space["crossovers"] = region_crossovers
                    region_info["crossovers"] = region_crossovers
                    graph_terminal_info["crossovers"] = region_crossovers
                if seam_inference_info is not None:
                    region_info["seam_inference"] = seam_inference_info
                    graph_terminal_info["seam_inference"] = seam_inference_info
            name = f"{Path(meta.get('source_image', 'image')).stem}_regions_{len(nodes_obj)}.json"
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
                    invalid_mask=invalid_mask,
                    nodes=obj.get("space", {}).get("nodes", {}),
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    expected_pairs=expected_flow_count,
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
                    obj["terminals"] = {}
                    graph_terminal_warnings.append("No line-graph terminals were confidently detected.")
            else:
                obj["terminals"] = {}
            name = f"{Path(meta.get('source_image', 'image')).stem}_line_{graph_nodes}.json"
        else:
            if grid_width is None or grid_height is None:
                grid = detect_grid(
                    geometry_warped,
                    threshold=threshold,
                    line_threshold=line_threshold,
                    invert=invert,
                )
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
                warp_edges, warp_info = detect_warp_edges(
                    # Warp markers are gaps in the outer border; the camera
                    # board is cropped at that border, so use the padded view.
                    warp_context if warp_context is not None else warped,
                    rows=grid_height,
                    cols=grid_width,
                )
                hint_tokens = level_signals.get("hint_tokens")
                is_boundless = isinstance(hint_tokens, list) and any(
                    str(token).strip().lower() == "boundless" for token in hint_tokens
                )
                if not warp_edges and is_boundless:
                    warp_edges = _grid_wrap_edges(grid_width, grid_height)
                    warp_info = {
                        **warp_info,
                        "count": len(warp_edges),
                        "mode": "full-boundless-wrap",
                        "warnings": [],
                    }
                modifier_info["warps"] = warp_info
                if not warp_edges:
                    classification_warnings.append(
                        "Warp modifier detected, but no paired boundary ports were confidently inferred."
                    )
            warp_edges.extend(manual_edge_overrides["warps"])
            if "walls" in level_modifiers:
                wall_edges, wall_info = detect_wall_edges(
                    warped,
                    rows=grid_height,
                    cols=grid_width,
                    # Glare lifts whole regions of a photographed board above
                    # the global threshold; require local wall contrast too.
                    local_contrast_margin=(
                        CAMERA_WALL_LOCAL_CONTRAST_MARGIN if effective_source_mode == "camera" else None
                    ),
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
                    invalid_mask=invalid_mask,
                    rows=grid_height,
                    cols=grid_width,
                    sat_threshold=sat_threshold,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    margin_ratio=margin_ratio,
                    cluster_threshold=cluster_threshold,
                    bg_threshold=bg_threshold,
                    expected_pairs=expected_flow_count,
                    enforce_square_parity=not warp_edges and not wall_edges,
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
                    obj["terminals"] = {}
                    graph_terminal_warnings.append("No graph-grid terminals were confidently detected.")
            else:
                obj["terminals"] = {}
            name = f"{Path(meta.get('source_image', 'image')).stem}_graph_{grid_width}x{grid_height}.json"
        manual_applied = len(add_edges) + len(remove_edges) + len(manual_edge_overrides["warps"]) + len(manual_edge_overrides["walls"])
        if manual_applied:
            modifier_info["manual_edge_overrides"] = {
                "add": len(add_edges),
                "remove": len(remove_edges),
                "warps": len(manual_edge_overrides["warps"]),
                "walls": len(manual_edge_overrides["walls"]),
            }
        graph_terminal_completeness = (
            _terminal_completeness_payload(
                graph_terminal_info,
                detected_endpoints=len(graph_terminal_payload),
                expected_pairs=expected_flow_count,
            )
            if auto_terminals
            else {
                "status": "not_applicable",
                "expected_pairs": expected_flow_count,
                "detected_pairs": 0,
                "detected_endpoints": 0,
                "pairing_complete": False,
                "confidence": 0.0,
                "near_miss_count": 0,
                "recovered_pair_count": 0,
                "review_required": False,
                "reason": "Automatic terminal detection was disabled.",
            }
        )
        graph_terminal_info.update(
            {
                "detected_endpoints": graph_terminal_completeness["detected_endpoints"],
                "detected_pairs": graph_terminal_completeness["detected_pairs"],
                "expected_pairs": graph_terminal_completeness["expected_pairs"],
                "completeness_status": graph_terminal_completeness["status"],
            }
        )
        seam_inference = graph_terminal_info.get("seam_inference") if isinstance(graph_terminal_info, dict) else None
        if isinstance(seam_inference, dict) and seam_inference.get("incomplete"):
            # Without inferred seams the board is usually unsolvable; never let
            # a budget, timeout, or solver failure pass as a confident import.
            graph_terminal_warnings.extend(str(item) for item in seam_inference.get("warnings", []))
            graph_terminal_completeness["review_required"] = True
            graph_terminal_completeness["reason"] = (
                f"{graph_terminal_completeness.get('reason') or ''} Region seam inference did not complete.".strip()
            )
            detection_info["region_seam_inference_incomplete"] = True
        if graph_terminal_completeness["review_required"]:
            graph_terminal_warnings.append(str(graph_terminal_completeness["reason"]))
        if modifier_info:
            detection_info["modifier_info"] = modifier_info
        if graph_terminal_payload:
            detection_info["terminals"] = graph_terminal_payload
        if graph_terminal_info:
            detection_info["terminal_info"] = graph_terminal_info
        detection_info["terminal_completeness"] = graph_terminal_completeness
        detected_terminal_colors = _terminal_color_map_from_placements(graph_terminal_payload)
        if detected_terminal_colors:
            graph_meta = dict(obj.get("meta") if isinstance(obj.get("meta"), dict) else {})
            graph_meta["terminal_colors"] = detected_terminal_colors
            obj["meta"] = graph_meta
        detection_info["warnings"] = classification_warnings + graph_terminal_warnings
        output_obj = obj
        if output_schema_version == 2:
            if target_used in {"cube", "star", "figure8"}:
                canonical_width = topo_width
                canonical_height = topo_height
            elif graph_layout in {"line", "regions"}:
                canonical_width = None
                canonical_height = None
            else:
                canonical_width = grid_width
                canonical_height = grid_height
            try:
                output_obj = _image_graph_schema_v2(
                    obj,
                    target_type=target_used,
                    graph_layout=graph_layout,
                    width=canonical_width,
                    height=canonical_height,
                    level_modifiers=level_modifiers,
                    import_detection=detection_info,
                    edge_corrections=manual_edge_overrides,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not encode generated graph as schema v2: {exc}",
                ) from exc
        return finalize_generation(
            {
                "name": name,
                "text": json.dumps(output_obj, indent=2),
                "metadata": meta,
                "detection": detection_info,
            }
        )

    raise HTTPException(status_code=400, detail=f"Unknown target_type after classification: {target_used}")


@app.post("/image/ocr")
@_offload_image_endpoint
async def image_ocr(
    file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    source_mode: str = Form("screenshot"),
    photo_corners_json: Optional[str] = Form(None),
    crop_x: Optional[int] = Form(None),
    crop_y: Optional[int] = Form(None),
    crop_width: Optional[int] = Form(None),
    crop_height: Optional[int] = Form(None),
    perspective: bool = Form(False),
    _permit: None = Depends(_heavy_image_permit),
) -> Dict[str, Any]:
    source_mode = _request_image_source_mode(source_mode)
    file, data = await _resolve_image_input(file, upload_id)
    image, input_info, effective_source_mode = _load_image_for_source_mode(data, source_mode)
    photo_corners = _request_photo_corners(
        photo_corners_json,
        source_mode=source_mode,
        image_size=image.size,
    )
    crop = _parse_crop_box(crop_x, crop_y, crop_width, crop_height)
    photo_info: Optional[Dict[str, Any]] = None
    if effective_source_mode == "camera":
        # Reuses the cached preparation from the other pipeline stages; the
        # level header lives on the straightened display, outside the board.
        camera = _prepare_camera_pipeline_images(
            image,
            crop=crop,
            threshold=230,
            invert=False,
            manual_corners=photo_corners,
            cache_key=_photo_cache_key(data),
        )
        warped = camera.display
        photo_info = camera.photo_info
    else:
        cropped = apply_crop(image, crop)
        warped, _perspective = _maybe_perspective(cropped, perspective)
    text_clean, message = _ocr_image_text(warped)
    expected_flow_count = parse_expected_flow_count(text_clean)
    if message:
        return {
            "text": "",
            "source_mode": source_mode,
            "source_mode_effective": effective_source_mode,
            "input": input_info,
            "photo": photo_info,
            "suggested_name": None,
            "expected_flow_count": None,
            "message": message,
        }
    level_num = None

    m = re.search(r"(?:level|lvl)\s*([0-9]{1,5})", text_clean, re.IGNORECASE)
    if m:
        level_num = int(m.group(1))
    else:
        m2 = re.search(r"([0-9]{1,5})", text_clean)
        if m2:
            level_num = int(m2.group(1))

    suggested = f"classic_level_{level_num}.flow" if level_num is not None else None
    return {
        "source_mode": source_mode,
        "source_mode_effective": effective_source_mode,
        "input": input_info,
        "photo": photo_info,
        "text": text_clean,
        "suggested_name": suggested,
        "expected_flow_count": expected_flow_count,
    }


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1").lower() in {"1", "true", "yes", "y", "on"}
    reload_dirs = [
        str(path)
        for path in (_ROOT / "backend", _ROOT / "flow_solver")
        if path.is_dir()
    ]
    uvicorn.run(
        "backend.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=reload_dirs if reload else None,
    )
