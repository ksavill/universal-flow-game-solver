from __future__ import annotations

from pathlib import Path

import pytest

from backend.file_lock import FileLockTimeout, InterProcessFileLock


def test_advisory_file_lock_excludes_another_handle(tmp_path: Path) -> None:
    path = tmp_path / "record.lock"
    with InterProcessFileLock(path):
        with pytest.raises(FileLockTimeout):
            with InterProcessFileLock(path, timeout=0.05, poll_interval=0.01):
                pass

    with InterProcessFileLock(path, timeout=0.05):
        assert path.is_file()
