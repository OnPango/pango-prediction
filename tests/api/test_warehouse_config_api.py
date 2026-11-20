"""The `warehouses[]` block and the `history_only` flag over HTTP.

Both are request fields added in contract 1.2 and both are optional, so a
client that sends neither is served exactly as before. What they decline comes
back the way every declined series does: `no_forecast` for a client that
declares 1.2, and the error it already knows for an older one.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pango_prediction.config import Settings
from pango_prediction.main import create_app

FIXTURES = Path(__file__).parents[1] / "fixtures"

TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(service_token=TOKEN, max_series_per_request=500)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def request(contract_version: str = "1.2") -> dict[str, Any]:
    """The minimal fixture: one stock-managed series in warehouse 1."""
    payload: dict[str, Any] = json.loads(
        (FIXTURES / "forecast_request_minimal.json").read_text(encoding="utf-8")
    )
    payload["contract_version"] = contract_version
    assert payload["series"][0]["warehouse_id"] == 1

    return payload


def post(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/v1/forecasts", json=payload, headers=AUTH)


def test_a_series_in_a_non_stock_warehouse_is_not_forecast(client: TestClient) -> None:
    payload = request() | {"warehouses": [{"warehouse_id": 1, "holds_stock": False}]}

    response = post(client, payload)

    assert response.status_code == 200

    result = response.json()["results"][0]

    assert result["status"] == "no_forecast"
    assert result["reason"] == "non_stock_warehouse"
    assert result["periods"] == []
    assert result["profile"] is not None


def test_a_series_in_a_stock_warehouse_is_forecast(client: TestClient) -> None:
    payload = request() | {"warehouses": [{"warehouse_id": 1, "holds_stock": True}]}

    result = post(client, payload).json()["results"][0]

    assert result["status"] == "ok"
    assert not any("holds stock" in warning for warning in result["warnings"])


def test_without_the_block_the_series_is_forecast_and_says_what_was_assumed(
    client: TestClient,
) -> None:
    result = post(client, request()).json()["results"][0]

    assert result["status"] == "ok"
    assert any("holds stock" in warning for warning in result["warnings"])


def test_a_history_only_series_is_not_forecast(client: TestClient) -> None:
    payload = request()
    payload["series"][0]["history_only"] = True

    response = post(client, payload)

    assert response.status_code == 200

    body = response.json()
    result = body["results"][0]

    assert result["status"] == "no_forecast"
    assert result["reason"] == "history_only"
    assert body["summary"] == {"total": 1, "ok": 0, "error": 0, "no_forecast": 1}


@pytest.mark.parametrize("contract_version", ["1.0", "1.1"])
def test_an_older_client_may_send_the_fields_and_gets_the_error_it_knows(
    client: TestClient, contract_version: str
) -> None:
    """The fields are additive; only the status is tied to the version."""
    payload = request(contract_version) | {
        "warehouses": [{"warehouse_id": 1, "holds_stock": False}]
    }

    response = post(client, payload)

    assert response.status_code == 200

    body = response.json()
    result = body["results"][0]

    assert body["contract_version"] == "1.1"
    assert result["status"] == "error"
    assert result["error"]["code"] == "MODEL_NOT_AVAILABLE"
    assert "non_stock_warehouse" in result["error"]["message"]
    assert result["reason"] is None


def test_a_warehouse_described_twice_is_a_bad_request(client: TestClient) -> None:
    """Two answers to one question: refused rather than resolved by order."""
    payload = request() | {
        "warehouses": [
            {"warehouse_id": 1, "holds_stock": True},
            {"warehouse_id": 1, "holds_stock": False},
        ]
    }

    response = post(client, payload)

    assert response.status_code == 422
    assert "warehouse" in response.text


def test_an_unknown_field_in_a_warehouse_is_rejected(client: TestClient) -> None:
    payload = request() | {
        "warehouses": [{"warehouse_id": 1, "holds_stock": True, "allocatable": True}]
    }

    assert post(client, payload).status_code == 422


def test_a_warehouse_with_no_series_in_the_request_is_ignored(client: TestClient) -> None:
    """Core may send its whole configuration with every batch."""
    payload = request() | {
        "warehouses": [
            {"warehouse_id": 1, "holds_stock": True},
            {"warehouse_id": 99, "holds_stock": False},
        ]
    }

    response = post(client, payload)

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "ok"
