from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.replay_image_imports import _generation_data, _replay_one


def test_replay_preserves_classifier_decision_and_manual_corrections() -> None:
    level_type = {
        "id": "square:warps",
        "geometry": "square",
        "modifiers": ["warps"],
        "confidence": 0.91,
        "source": "classifier",
    }
    corrections = {
        "add": [["0,0", "1,1"]],
        "remove": [],
        "warps": [["0,1", "2,1"]],
        "walls": [],
    }
    record = {
        "processing": {
            "target_type": "graph",
            "auto_classify": False,
            "auto_terminals": True,
            "grid_width": 3,
            "grid_height": 2,
        },
        "result": {
            "detection": {"level_type": level_type},
            "text": json.dumps(
                {
                    "extensions": {
                        "flow-solver/import": {"manual_edge_corrections": corrections}
                    }
                }
            ),
        },
    }

    data = _generation_data(record)

    assert json.loads(data["level_type_json"]) == level_type
    assert json.loads(data["edge_overrides_json"]) == corrections


def test_replay_carries_recorded_or_golden_expected_flow_count() -> None:
    recorded = _generation_data({"processing": {"expected_flow_count": 9}})
    overridden = _generation_data(
        {"processing": {"expected_flow_count": 9}},
        expected_flow_count=13,
    )

    assert recorded["expected_flow_count"] == "9"
    assert overridden["expected_flow_count"] == "13"


class _ReplayResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _ReplayClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, **kwargs: Any) -> _ReplayResponse:
        self.calls.append((path, kwargs))
        if path == "/image/generate":
            return _ReplayResponse(
                200,
                {
                    "name": "fixture.json",
                    "text": "{}",
                    "import_id": "archive-id",
                    "detection": {
                        "level_type": {"geometry": "square", "modifiers": []},
                        "target_type_used": "grid",
                        "grid": {"width": 2, "height": 2},
                        "terminals": [],
                    },
                },
            )
        return _ReplayResponse(200, {"stats": {"solver": "test"}})


def test_update_archive_replay_replaces_the_selected_record(tmp_path: Path) -> None:
    record_dir = tmp_path / "archive-id"
    record_dir.mkdir()
    (record_dir / "source.png").write_bytes(b"image")
    client = _ReplayClient()

    entry = _replay_one(
        client,
        record_dir / "record.json",
        {
            "id": "archive-id",
            "image_file": "source.png",
            "original_name": "fixture.png",
            "content_type": "image/png",
        },
        timeout_ms=1_000,
        mode="auto",
        update_archive=True,
    )

    assert entry["updated_archive"] is True
    assert client.calls[0][0] == "/image/generate"
    assert client.calls[0][1]["data"]["replace_import_id"] == "archive-id"
    assert client.calls[1][1]["json"]["import_id"] == "archive-id"
