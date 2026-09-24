"""Short-lived, content-addressed store for images the browser processes in stages.

One import calls classify, OCR, grid, terminal, and generate endpoints with the
same image.  The browser uploads it once, receives its SHA-256 as an
``upload_id``, and sends only that id to the later stages.  Files live on disk
so every worker process on the host shares them, and expire after a TTL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


UPLOAD_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StoredUpload:
    upload_id: str
    data: bytes
    filename: str
    content_type: str


class UploadStore:
    def __init__(self, root: Path, *, ttl_seconds: float, max_total_bytes: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_total_bytes = max_total_bytes

    def _paths(self, upload_id: str) -> tuple[Path, Path]:
        if not UPLOAD_ID_PATTERN.match(upload_id):
            raise ValueError("upload_id must be a 64-character lowercase SHA-256 hex digest")
        return self.root / f"{upload_id}.bin", self.root / f"{upload_id}.json"

    def put(self, data: bytes, *, filename: str, content_type: str) -> StoredUpload:
        upload_id = hashlib.sha256(data).hexdigest()
        data_path, meta_path = self._paths(upload_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if not data_path.exists():
            # Write-then-rename so concurrent readers never see partial files.
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False, suffix=".tmp") as handle:
                handle.write(data)
                temporary = Path(handle.name)
            temporary.replace(data_path)
        meta = {"filename": filename, "content_type": content_type, "byte_size": len(data)}
        with tempfile.NamedTemporaryFile(
            "w", dir=self.root, delete=False, suffix=".tmp", encoding="utf-8"
        ) as handle:
            json.dump(meta, handle)
            temporary = Path(handle.name)
        temporary.replace(meta_path)
        self.prune()
        return StoredUpload(upload_id, data, filename, content_type)

    def get(self, upload_id: str) -> Optional[StoredUpload]:
        data_path, meta_path = self._paths(upload_id)
        try:
            if time.time() - data_path.stat().st_mtime > self.ttl_seconds:
                self._remove(upload_id)
                return None
            data = data_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if hashlib.sha256(data).hexdigest() != upload_id:
            self._remove(upload_id)
            return None
        # Refresh the TTL while an import is still using the image.
        os.utime(data_path, None)
        return StoredUpload(
            upload_id,
            data,
            str(meta.get("filename") or "image"),
            str(meta.get("content_type") or "application/octet-stream"),
        )

    def _remove(self, upload_id: str) -> None:
        for path in self._paths(upload_id):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def prune(self) -> None:
        if not self.root.is_dir():
            return
        now = time.time()
        entries: list[tuple[float, int, str]] = []
        for path in self.root.glob("*.bin"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if now - stat.st_mtime > self.ttl_seconds:
                self._remove(path.stem)
                continue
            entries.append((stat.st_mtime, stat.st_size, path.stem))
        for path in self.root.glob("*.tmp"):
            try:
                if now - path.stat().st_mtime > 300:
                    path.unlink()
            except FileNotFoundError:
                pass
        total = sum(size for _mtime, size, _id in entries)
        for _mtime, size, upload_id in sorted(entries):
            if total <= self.max_total_bytes:
                break
            self._remove(upload_id)
            total -= size
