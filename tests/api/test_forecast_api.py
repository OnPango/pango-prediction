"""API tests for contract v1 (plan B28).

They drive the real application through the real domain: the only thing faked is
the service credential.
"""

import json
from collections.abc import Iterator
from datetime import date
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
def settings() -> Settings:
    return Settings(service_token=TOKEN, max_series_per_request=500)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def load_fixture(name: str = "forecast_request.json") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (FIXTURES / name).read_text(encoding="utf-8")
    )

    return payload


# --- health ---


def test_health_needs_no_credential(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "pango-prediction",
        "service_version": "0.1.0",
    }


# --- service-to-service authentication (plan B16) ---


def test_forecasts_without_a_credential_is_unauthorized(
    client: TestClient,
) -> None:
    response = client.post("/v1/forecasts", json=load_fixture())

    assert response.status_code == 401


def test_forecasts_with_a_wrong_credential_is_unauthorized(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/forecasts",
        json=load_fixture(),
        headers={"Authorization": "Bearer not-the-token"},
    )

    assert response.status_code == 401


def test_a_credential_without_the_bearer_scheme_is_unauthorized(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/forecasts",
        json=load_fixture(),
        headers={"Authorization": TOKEN},
    )

    assert response.status_code == 401


# --- the batch envelope and partial failure (plan B17) ---


def test_the_mock_request_is_accepted(client: TestClient) -> None:
    """The fixtures Pango Core will send must validate against the schema."""
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)

    assert response.status_code == 200


def test_the_envelope_echoes_the_request_identifiers(client: TestClient) -> None:
    payload = load_fixture()

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)
    body = response.json()

    assert body["contract_version"] == "1.0"
    assert body["service_version"] == "0.1.0"
    assert body["request_id"] == payload["request_id"]
    assert body["forecast_run_id"] == payload["forecast_run_id"]


def test_no_policy_version_is_reported_while_no_policy_is_loaded(
    client: TestClient,
) -> None:
    """Null rather than a placeholder: a made-up version would read as real."""
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)

    assert response.json()["policy_version"] is None


def test_every_series_gets_a_result_in_request_order(client: TestClient) -> None:
    payload = load_fixture()

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)
    body = response.json()

    assert body["summary"]["total"] == len(payload["series"])
    assert [result["series_id"] for result in body["results"]] == [
        series["series_id"] for series in payload["series"]
    ]


def test_the_invalid_series_fails_on_its_own(client: TestClient) -> None:
    """One bad series must not invalidate the batch."""
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)
    body = response.json()

    by_id = {result["series_id"]: result for result in body["results"]}
    invalid = by_id[1500]

    assert response.status_code == 200
    assert invalid["status"] == "error"
    assert invalid["error"]["code"] == "INVALID_SERIES"
    assert "negative" in invalid["error"]["message"]


def test_valid_series_report_that_no_model_is_deployed(
    client: TestClient,
) -> None:
    """Honest reporting: the pipeline ran, but nothing can forecast yet."""
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)
    body = response.json()

    by_id = {result["series_id"]: result for result in body["results"]}

    for series_id in (1042, 1198, 1255, 1403):
        assert by_id[series_id]["error"]["code"] == "MODEL_NOT_AVAILABLE"
        assert by_id[series_id]["periods"] == []


def test_availability_findings_travel_as_warnings(client: TestClient) -> None:
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)
    body = response.json()

    by_id = {result["series_id"]: result for result in body["results"]}

    assert "sold to order" in " ".join(by_id[1198]["warnings"])
    assert "could not be classified" in " ".join(by_id[1403]["warnings"])
    assert by_id[1042]["warnings"] == []


def test_summary_counts_match_the_results(client: TestClient) -> None:
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)
    body = response.json()

    assert body["summary"]["ok"] == sum(
        1 for result in body["results"] if result["status"] == "ok"
    )
    assert body["summary"]["error"] == sum(
        1 for result in body["results"] if result["status"] == "error"
    )
    assert body["summary"]["total"] == (
        body["summary"]["ok"] + body["summary"]["error"]
    )


# --- request-level failures ---


def test_an_unknown_field_is_rejected(client: TestClient) -> None:
    """Contract drift has to surface, not pass silently."""
    payload = load_fixture("forecast_request_minimal.json")
    payload["series"][0]["made_to_order"] = False

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 422


def test_a_horizon_below_one_is_rejected(client: TestClient) -> None:
    payload = load_fixture("forecast_request_minimal.json")
    payload["horizon"] = 0

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 422


def test_an_unsupported_frequency_is_rejected(client: TestClient) -> None:
    payload = load_fixture("forecast_request_minimal.json")
    payload["frequency"] = "DAILY"

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 422


def test_an_empty_batch_is_rejected(client: TestClient) -> None:
    payload = load_fixture("forecast_request_minimal.json")
    payload["series"] = []

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 422


def test_a_batch_over_the_limit_is_rejected(settings: Settings) -> None:
    """Contract v1 §19: the service enforces a configurable batch size."""
    small = Settings(service_token=TOKEN, max_series_per_request=2)
    payload = load_fixture()

    with TestClient(create_app(small)) as client:
        response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 400
    assert "Split the job" in response.json()["detail"]


def test_the_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/v1/forecasts" in response.json()["paths"]


# --- the series mapping at the edge ---


def test_history_end_is_passed_through_to_the_domain(
    client: TestClient,
) -> None:
    """A series whose only sale falls after the cutoff has no usable history."""
    payload = load_fixture("forecast_request_minimal.json")
    payload["history_end"] = date(2026, 6, 7).isoformat()

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)
    result = response.json()["results"][0]

    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_SERIES"
