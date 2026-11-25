import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pango_prediction.config import SERVICE_TOKEN_ENV
from pango_prediction.main import create_app

ROOT = Path(__file__).parents[2]
TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_forecast_selection_does_not_load_a_policy_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload: dict[str, Any] = json.loads(
        (ROOT / "tests/fixtures/forecast_request_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    payload["contract_version"] = "1.2"
    monkeypatch.setenv(SERVICE_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(
        "PANGO_PREDICTION_POLICY_PATH",
        str(tmp_path / "missing-policy.json"),
    )

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/forecasts",
            json=payload,
            headers=AUTH,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1.2"
    assert body["policy_version"] is None
    result = body["results"][0]
    assert result["status"] == "ok"
    assert result["model_name"] in {
        "zero",
        "naive",
        "moving_average",
        "sba",
    }
    assert result["model_version"] == "rolling-origin-average-rank-v1"
    assert len(result["periods"]) == 4
