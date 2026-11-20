"""The no-forecast status over HTTP, and which clients get to see it.

A status is not an additive field. A client written against contract 1.0 or
1.1 knows two of them, `ok` and `error`, and may well branch on exactly those,
so a third would break it without warning. The status is therefore served only
to a client that declares 1.2; an older one keeps receiving the error it
already handles. Everything else in the response is identical for both.
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
ROUTES_LOGGER = "pango_prediction.adapters.inbound.api.routes"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(service_token=TOKEN, max_series_per_request=500)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def load_fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return payload


def declined_request(contract_version: str) -> dict[str, Any]:
    """The minimal fixture with its one series turned dormant.

    A single sale almost two years before the cutoff makes the series inactive,
    which the development policy declines with the reason `inactive`.
    """
    payload = load_fixture("forecast_request_minimal.json")
    payload["contract_version"] = contract_version
    series = payload["series"][0]
    series["demand"] = [{"date": "2025-01-08", "quantity": 5}]
    series["stock"] = []

    return payload


def test_a_declined_series_has_its_own_status_from_contract_1_2(
    client: TestClient,
) -> None:
    response = client.post("/v1/forecasts", json=declined_request("1.2"), headers=AUTH)

    assert response.status_code == 200

    body = response.json()
    result = body["results"][0]

    assert body["contract_version"] == "1.2"
    assert result["status"] == "no_forecast"
    assert result["reason"] == "inactive"
    assert result["error"] is None
    assert result["periods"] == []
    assert result["total"] is None
    assert result["model_name"] is None
    assert body["summary"] == {"total": 1, "ok": 0, "error": 0, "no_forecast": 1}


def test_a_declined_series_still_carries_its_profile(client: TestClient) -> None:
    response = client.post("/v1/forecasts", json=declined_request("1.2"), headers=AUTH)

    result = response.json()["results"][0]

    assert result["profile"]["inactive"] is True
    assert result["warnings"]
    assert len(result["horizon_business_days"]) == 4


@pytest.mark.parametrize("contract_version", ["1.0", "1.1"])
def test_an_older_client_keeps_receiving_the_error_it_knows(
    client: TestClient, contract_version: str
) -> None:
    response = client.post(
        "/v1/forecasts", json=declined_request(contract_version), headers=AUTH
    )

    assert response.status_code == 200

    body = response.json()
    result = body["results"][0]

    assert body["contract_version"] == "1.1"
    assert result["status"] == "error"
    assert result["error"]["code"] == "MODEL_NOT_AVAILABLE"
    assert "inactive" in result["error"]["message"]
    assert result["reason"] is None
    assert body["summary"] == {"total": 1, "ok": 0, "error": 1, "no_forecast": 0}


def test_what_was_not_declined_reads_the_same_in_every_version(
    client: TestClient,
) -> None:
    """The version changes one status and nothing else.

    None of the five fixture series is declined, so the two responses may
    differ only in the version they declare.
    """
    payload = load_fixture("forecast_request.json")

    older = client.post(
        "/v1/forecasts", json=payload | {"contract_version": "1.1"}, headers=AUTH
    ).json()
    newer = client.post(
        "/v1/forecasts", json=payload | {"contract_version": "1.2"}, headers=AUTH
    ).json()

    assert older["results"] == newer["results"]
    assert older["summary"] == newer["summary"]
    assert (older["contract_version"], newer["contract_version"]) == ("1.1", "1.2")


@pytest.mark.parametrize(
    ("contract_version", "expected_status"),
    [("1.2", "ok"), ("1.1", "error")],
)
def test_the_request_log_reports_what_the_client_was_told(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    contract_version: str,
    expected_status: str,
) -> None:
    """A request whose only series was declined did not fail.

    The log describes the response, so it follows the rendering: nothing went
    wrong for the 1.2 client, while the older one was told about an error.
    """
    with caplog.at_level("INFO", logger=ROUTES_LOGGER):
        client.post(
            "/v1/forecasts", json=declined_request(contract_version), headers=AUTH
        )

    assert caplog.records[0].__dict__["status"] == expected_status


def test_an_evaluation_request_may_declare_1_2(client: TestClient) -> None:
    """Nothing in evaluations changed in 1.2, but a 1.2 client calls it too."""
    payload = {
        "contract_version": "1.2",
        "request_id": "evaluation-1",
        "forecast_run_id": 41,
        "frequency": "WEEKLY",
        "horizon": 1,
        "series": [
            {
                "series_id": 1198,
                "periods": [
                    {
                        "period_start": "2026-11-02",
                        "estimate": 3,
                        "lower": 2,
                        "upper": 5,
                        "actual": 5,
                        "observable": True,
                    }
                ],
                "total": {"estimate": 3, "lower": 2, "upper": 5},
            }
        ],
    }

    response = client.post("/v1/forecast-evaluations", json=payload, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["contract_version"] == "1.2"
