from __future__ import annotations

from fastapi.testclient import TestClient

from backend.acceleration import acceleration_capabilities
from backend.app import app


def test_cpu_override_is_deterministic_and_reported(monkeypatch) -> None:
    monkeypatch.setenv("FLOW_ACCELERATOR", "cpu")
    acceleration_capabilities.cache_clear()
    try:
        capabilities = acceleration_capabilities()
        assert capabilities["requested"] == "cpu"
        assert capabilities["cuda_enabled"] is False
        assert capabilities["image_backend"] == "cpu"
        assert capabilities["features"]["exact_solving"] == "native-cpu-sat"

        response = TestClient(app).get("/capabilities")
        assert response.status_code == 200
        assert response.json()["acceleration"]["image_backend"] == "cpu"
    finally:
        acceleration_capabilities.cache_clear()
