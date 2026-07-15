from __future__ import annotations

import json

from scripts.replay_image_imports import _generation_data


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
