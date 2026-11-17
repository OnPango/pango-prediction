"""Exercise the delivered policy through the production application assembly."""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pango_prediction.config import POLICY_PATH_ENV, SERVICE_TOKEN_ENV, load_settings
from pango_prediction.domain.policy import PolicyError
from pango_prediction.main import create_app

ROOT = Path(__file__).parents[2]
POLICY = ROOT / "config" / "model_policy.json"
POLICY_SHA = "326e0b3586477c9a00529e305b87267f1afbdedb14595981a63fbfb9c7503842"


def test_delivered_policy_bytes_match_route_a() -> None:
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA


@pytest.mark.parametrize(
    ("values", "inactive", "expected_status"),
    [([1, 2, 3, 4, 5], False, "ok"), ([5], True, "no_forecast"),
     ([1, 2, 3, 4], False, "error")],
)
def test_delivered_policy_over_http(
    values: list[int], inactive: bool, expected_status: str,
) -> None:
    payload: dict[str, Any] = json.loads(
        (ROOT / "tests/fixtures/forecast_request_minimal.json").read_text(encoding="utf-8")
    )
    payload["contract_version"] = "1.2"
    cutoff = date(2026, 11, 1)
    start = cutoff - timedelta(weeks=40 if inactive else len(values) - 1)
    payload["series"][0]["demand"] = [
        {"date": (start + timedelta(weeks=index)).isoformat(), "quantity": value}
        for index, value in enumerate(values)
    ]
    payload["series"][0]["stock"] = []
    settings = load_settings({SERVICE_TOKEN_ENV: "test-token", POLICY_PATH_ENV: str(POLICY)})
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/forecasts", json=payload, headers={"Authorization": "Bearer test-token"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["policy_version"] == "ruta-a-baseline-v1-inactive"
    result = body["results"][0]
    assert result["status"] == expected_status
    if expected_status == "ok":
        assert result["model_name"] == "moving_average"
        assert [period["estimate"] for period in result["periods"]] == [3.5] * 4
        assert result["total"]["estimate"] == 14.0
    elif inactive:
        assert result["reason"] == "inactive"
        assert result["periods"] == []
        assert result["total"] is None
    else:
        assert result["error"]["code"] == "MODEL_FAILED"
        assert result["periods"] == []


def test_an_explicit_missing_policy_refuses_startup(tmp_path: Path) -> None:
    settings = load_settings(
        {SERVICE_TOKEN_ENV: "test-token", POLICY_PATH_ENV: str(tmp_path / "missing.json")}
    )
    with pytest.raises(PolicyError, match="could not read policy"):
        create_app(settings)
