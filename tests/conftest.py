from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_mutable_image_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let API tests append records to a developer's real upload archive."""

    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "image_imports"))
    monkeypatch.setenv("FLOW_IMAGE_JOBS_DIR", str(tmp_path / "image_jobs"))
