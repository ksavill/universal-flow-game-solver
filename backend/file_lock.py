from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Optional, Type


class FileLockTimeout(TimeoutError):
    """Raised when a cross-process lock cannot be acquired before its deadline."""


class InterProcessFileLock:
    """Small standard-library advisory lock that works on Windows and POSIX.

    Lock files intentionally live outside the records they protect.  A record
    directory can therefore be atomically deleted while another process still
    has the stable lock file open.
    """

    def __init__(self, path: Path, *, timeout: float = 30.0, poll_interval: float = 0.05) -> None:
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self.poll_interval = max(0.005, float(poll_interval))
        self._handle: Optional[BinaryIO] = None

    def __enter__(self) -> "InterProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._acquire(handle)
                self._handle = handle
                return self
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise FileLockTimeout(f"Timed out waiting for lock {self.path}") from exc
                time.sleep(self.poll_interval)

    @staticmethod
    def _acquire(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._release(handle)
        finally:
            handle.close()


__all__ = ["FileLockTimeout", "InterProcessFileLock"]
