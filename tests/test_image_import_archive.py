from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import app


def _png_bytes() -> bytes:
    image = Image.new("RGB", (40, 40), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_processed_screenshot_is_archived_and_reopenable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes()

    generated = client.post(
        "/image/generate",
        files={"file": ("level-42.png", source, "image/png")},
        data={
            "target_type": "square",
            "grid_width": "2",
            "grid_height": "2",
            "auto_terminals": "false",
            "auto_classify": "false",
            "output_schema_version": "2",
            "crop_x": "0",
            "crop_y": "0",
            "crop_width": "40",
            "crop_height": "40",
        },
    )
    assert generated.status_code == 200, generated.text
    generated_body = generated.json()
    import_id = generated_body["import_id"]

    listed = client.get("/image-imports")
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()["entries"]] == [import_id]
    assert listed.json()["entries"][0]["original_name"] == "level-42.png"

    reopened = client.get(f"/image-imports/{import_id}")
    assert reopened.status_code == 200
    record = reopened.json()
    assert record["result"]["name"] == generated_body["name"]
    assert record["result"]["text"] == generated_body["text"]
    assert record["processing"]["grid_width"] == 2

    solve = client.post(
        "/solve",
        json={
            "name": generated_body["name"],
            "text": generated_body["text"],
            "timeout_ms": 2_000,
            "import_id": import_id,
        },
    )
    assert solve.status_code == 400
    reopened_after_solve = client.get(f"/image-imports/{import_id}").json()
    assert reopened_after_solve["solve"]["status"] == "failed"
    assert "terminal" in reopened_after_solve["solve"]["error"].lower()
    listed_after_solve = client.get("/image-imports").json()["entries"]
    assert listed_after_solve[0]["solve_status"] == "failed"

    archived_image = client.get(f"/image-imports/{import_id}/image")
    assert archived_image.status_code == 200
    assert archived_image.headers["content-type"] == "image/png"
    assert archived_image.content == source

    deleted = client.delete(f"/image-imports/{import_id}")
    assert deleted.status_code == 200
    assert client.get(f"/image-imports/{import_id}").status_code == 404


def test_failed_pipeline_preserves_source_and_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes()

    failed = client.post(
        "/image-imports/failed",
        files={"file": ("freeform.png", source, "image/png")},
        data={"error": "Region detection found no connected cells", "stage": "bulk-import"},
    )

    assert failed.status_code == 200, failed.text
    summary = failed.json()
    assert summary["status"] == "failed"
    assert summary["error"] == "Region detection found no connected cells"
    record = client.get(f"/image-imports/{summary['id']}").json()
    assert record["processing"]["stage"] == "bulk-import"
    assert "result" not in record
    assert client.get(f"/image-imports/{summary['id']}/image").content == source


def test_reprocessing_reuses_archive_and_retains_run_history(tmp_path: Path, monkeypatch) -> None:
    imports = tmp_path / "imports"
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(imports))
    client = TestClient(app)
    source = _png_bytes()
    request_data = {
        "target_type": "square",
        "grid_width": "2",
        "grid_height": "2",
        "auto_terminals": "false",
        "auto_classify": "false",
        "output_schema_version": "2",
        "crop_x": "0",
        "crop_y": "0",
        "crop_width": "40",
        "crop_height": "40",
    }

    first = client.post(
        "/image/generate",
        files={"file": ("corpus.png", source, "image/png")},
        data=request_data,
    )
    assert first.status_code == 200, first.text
    import_id = first.json()["import_id"]
    original_created_at = client.get(f"/image-imports/{import_id}").json()["created_at"]

    second = client.post(
        "/image/generate",
        files={"file": ("corpus.png", source, "image/png")},
        data={**request_data, "replace_import_id": import_id},
    )

    assert second.status_code == 200, second.text
    assert second.json()["import_id"] == import_id
    assert [path.name for path in imports.iterdir()] == [import_id]
    record = client.get(f"/image-imports/{import_id}").json()
    assert record["created_at"] == original_created_at
    assert record["processing"]["reprocessed"] is True
    assert len(record["runs"]) == 1
    assert record["runs"][0]["status"] == "processed"
    listed_entry = client.get("/image-imports?limit=1000").json()["entries"][0]
    assert listed_entry["run_count"] == 2
    assert "runs" not in listed_entry
    assert client.get(f"/image-imports/{import_id}/image").content == source


def test_reprocess_failure_updates_same_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes()
    failed = client.post(
        "/image-imports/failed",
        files={"file": ("sample.png", source, "image/png")},
        data={"error": "old failure", "stage": "initial"},
    ).json()

    updated = client.post(
        f"/image-imports/{failed['id']}/failure",
        json={"error": "new detector failure", "stage": "screenshot-library"},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == failed["id"]
    assert updated.json()["run_count"] == 2
    record = client.get(f"/image-imports/{failed['id']}").json()
    assert record["error"] == "new detector failure"
    assert record["processing"] == {"stage": "screenshot-library", "reprocessed": True}
    assert record["runs"][0]["error"] == "old failure"
    assert client.get(f"/image-imports/{failed['id']}/image").content == source


def test_bulk_delete_image_imports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(tmp_path / "imports"))
    client = TestClient(app)
    source = _png_bytes()
    ids = []
    for index in range(3):
        response = client.post(
            "/image-imports/failed",
            files={"file": (f"sample-{index}.png", source, "image/png")},
            data={"error": "fixture", "stage": "test"},
        )
        ids.append(response.json()["id"])

    deleted = client.post(
        "/image-imports/bulk-delete",
        json={"ids": [ids[0], ids[1], ids[1], "f" * 32]},
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == ids[:2]
    assert deleted.json()["missing"] == ["f" * 32]
    remaining = client.get("/image-imports?limit=1000").json()
    assert remaining["total"] == 1
    assert [entry["id"] for entry in remaining["entries"]] == [ids[2]]


def test_reopening_legacy_archive_recovers_retained_terminal_colors(tmp_path: Path, monkeypatch) -> None:
    imports = tmp_path / "imports"
    monkeypatch.setenv("FLOW_IMAGE_IMPORTS_DIR", str(imports))
    import_id = "a" * 32
    record_dir = imports / import_id
    record_dir.mkdir(parents=True)
    puzzle = {
        "space": {
            "type": "graph",
            "nodes": {"left": {"pos": [0, 0, 0]}, "right": {"pos": [1, 0, 0]}},
            "edges": [["left", "right"]],
        },
        "terminals": {"A": ["left", "right"]},
        "meta": {},
    }
    record = {
        "id": import_id,
        "created_at": 1,
        "status": "processed",
        "original_name": "legacy.png",
        "content_type": "image/png",
        "byte_size": 0,
        "image_size": {"width": 10, "height": 10},
        "image_file": "source.png",
        "generated_name": "legacy.json",
        "geometry": "graph",
        "grid": None,
        "terminal_count": 2,
        "processing": {},
        "result": {
            "name": "legacy.json",
            "text": json.dumps(puzzle),
            "metadata": {},
            "detection": {
                "terminals": [
                    {"node_id": "left", "letter": "A", "color": [10, 40, 250]},
                    {"node_id": "right", "letter": "A", "color": [14, 40, 250]},
                ]
            },
        },
    }
    (record_dir / "record.json").write_text(json.dumps(record), encoding="utf-8")

    response = TestClient(app).get(f"/image-imports/{import_id}")

    assert response.status_code == 200, response.text
    reopened = json.loads(response.json()["result"]["text"])
    assert reopened["meta"]["terminal_colors"] == {"A": "#0c28fa"}
