from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import scripts.backup_data as backup_data


def test_backup_contains_mutable_data_and_integrity_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    puzzles = tmp_path / "puzzles"
    imports = tmp_path / "data" / "image_imports"
    jobs = tmp_path / "data" / "image_jobs"
    puzzles.mkdir(parents=True)
    imports.mkdir(parents=True)
    jobs.mkdir(parents=True)
    puzzle_bytes = b"A.A\n"
    (puzzles / "sample.flow").write_bytes(puzzle_bytes)
    (imports / "record.json").write_text('{"status":"processed"}', encoding="utf-8")
    (jobs / "job.json").write_text('{"status":"queued"}', encoding="utf-8")
    output = tmp_path / "backups"

    monkeypatch.setattr(backup_data, "ROOT", tmp_path)
    monkeypatch.setattr(backup_data, "BACKUP_SOURCES", (puzzles, imports, jobs))
    monkeypatch.setattr(
        sys,
        "argv",
        ["backup_data.py", "--output-dir", str(output), "--retain", "1"],
    )

    assert backup_data.main() == 0
    archives = list(output.glob("flow-solver-*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert {
            "puzzles/sample.flow",
            "data/image_imports/record.json",
            "data/image_jobs/job.json",
            "BACKUP_MANIFEST.json",
        } <= set(archive.namelist())
        manifest = json.loads(archive.read("BACKUP_MANIFEST.json"))
        assert manifest["files"]["puzzles/sample.flow"]["sha256"] == hashlib.sha256(
            puzzle_bytes
        ).hexdigest()
