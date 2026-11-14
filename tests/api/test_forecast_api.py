"""API tests for contract v1 (plan B28).

They drive the real application through the real domain: the only thing faked is
the service credential.
"""

import json
from collections.abc import Iterator
from datetime import date, timedelta
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


# --- the horizon belongs to the policy ---


@pytest.mark.parametrize("horizon", [1, 8])
def test_a_horizon_other_than_the_policys_is_a_bad_request(
    client: TestClient, horizon: int
) -> None:
    """Refused whole, not per series: the fault is in the request itself."""
    payload = load_fixture()
    payload["horizon"] = horizon

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert str(horizon) in detail
    assert "4" in detail


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

    assert body["contract_version"] == "1.1"
    assert body["service_version"] == "0.1.0"
    assert body["request_id"] == payload["request_id"]
    assert body["forecast_run_id"] == payload["forecast_run_id"]


def test_development_policy_version_is_reported(
    client: TestClient,
) -> None:
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)

    assert response.json()["policy_version"] == "development-1"


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


def test_valid_series_are_forecast_when_the_development_policy_matches(
    client: TestClient,
) -> None:
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)
    body = response.json()

    by_id = {result["series_id"]: result for result in body["results"]}

    for series_id in (1042, 1198):
        assert by_id[series_id]["status"] == "ok"
        assert len(by_id[series_id]["periods"]) == 4
        assert by_id[series_id]["total"] is not None
        assert by_id[series_id]["total"]["estimate"] == pytest.approx(
            sum(period["estimate"] for period in by_id[series_id]["periods"])
        )
        assert by_id[series_id]["model_name"] == "naive"

    for series_id in (1255, 1403):
        assert by_id[series_id]["error"]["code"] == "MODEL_NOT_AVAILABLE"
        assert by_id[series_id]["periods"] == []
        assert by_id[series_id]["total"] is None


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


# --- contract 1.1: the profile and the calendar covariate ---


def test_a_contract_1_0_request_is_still_accepted(client: TestClient) -> None:
    """The 1.1 additions are additive, which section 3 keeps inside 1.x.

    The fixtures still say 1.0, which is the point: Pango Core does not have to
    change anything to keep working.
    """
    payload = load_fixture()
    assert payload["contract_version"] == "1.0"

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["contract_version"] == "1.1"


def test_a_request_may_declare_1_1(client: TestClient) -> None:
    payload = load_fixture() | {"contract_version": "1.1"}

    assert client.post("/v1/forecasts", json=payload, headers=AUTH).status_code == 200


def test_an_unknown_contract_version_is_refused(client: TestClient) -> None:
    payload = load_fixture() | {"contract_version": "2.0"}

    assert client.post("/v1/forecasts", json=payload, headers=AUTH).status_code == 422


def test_every_series_with_demand_comes_back_profiled(client: TestClient) -> None:
    """No model is deployed, yet the series is no longer a black box."""
    response = client.post("/v1/forecasts", json=load_fixture(), headers=AUTH)

    profiled = [
        result
        for result in response.json()["results"]
        if result["profile"] is not None
    ]

    assert profiled, "no series was profiled"

    for result in profiled:
        profile = result["profile"]
        assert profile["demand_class"] in {
            "SMOOTH",
            "ERRATIC",
            "INTERMITTENT",
            "LUMPY",
            "INSUFFICIENT",
            "NOT_CLASSIFIABLE",
        }
        assert profile["history_class"] in {"NEW", "LIMITED", "MATURE"}
        assert profile["history_weeks"] >= 1
        assert profile["observed_weeks"] <= profile["history_weeks"]


def test_a_profile_travels_alongside_the_error(client: TestClient) -> None:
    """Both at once: it cannot be forecast, and here is why."""
    results = client.post(
        "/v1/forecasts", json=load_fixture(), headers=AUTH
    ).json()["results"]

    both = [
        result
        for result in results
        if result["profile"] is not None and result["error"] is not None
    ]

    assert both
    assert all(result["error"]["code"] == "MODEL_NOT_AVAILABLE" for result in both)


def test_the_horizon_covariate_has_one_value_per_forecast_week(
    client: TestClient,
) -> None:
    payload = load_fixture()

    results = client.post("/v1/forecasts", json=payload, headers=AUTH).json()["results"]
    profiled = [r for r in results if r["profile"] is not None]

    assert profiled
    for result in profiled:
        assert len(result["horizon_business_days"]) == payload["horizon"]
        assert all(0.0 <= value <= 5.6 for value in result["horizon_business_days"])


def horizon_weeks(payload: dict[str, Any]) -> list[date]:
    """The Mondays the fixture's horizon covers.

    Every series ends at the last complete week of the history, so they share
    one horizon. history_end 2026-11-01 is a Sunday, so that week is complete
    and the horizon starts on 2026-11-02.
    """
    history_end = date.fromisoformat(payload["history_end"])
    last_complete = history_end - timedelta(days=history_end.weekday())

    return [
        last_complete + timedelta(weeks=step)
        for step in range(1, payload["horizon"] + 1)
    ]


def covariate_by_series(payload: dict[str, Any], body: dict[str, Any]) -> dict[int, list[float]]:
    return {
        series["series_id"]: result["horizon_business_days"]
        for series, result in zip(payload["series"], body["results"], strict=True)
    }


def test_a_closed_date_reduces_the_covariate_for_that_warehouse_only(
    client: TestClient,
) -> None:
    """Series 1198 lives in warehouse 3; 1042, 1255 and 1403 do not."""
    payload = load_fixture()
    first_monday = horizon_weeks(payload)[0]

    payload["calendar"] = {
        "closed_dates": [],
        "closed_dates_by_warehouse": {"3": [first_monday.isoformat()]},
    }

    covariate = covariate_by_series(
        payload,
        client.post("/v1/forecasts", json=payload, headers=AUTH).json(),
    )

    assert covariate[1198][0] == pytest.approx(4.6)

    for series_id in (1042, 1255, 1403):
        assert covariate[series_id][0] == pytest.approx(5.6)

    # Only the week that was closed changes, not the rest of the horizon.
    assert covariate[1198][1:] == pytest.approx([5.6, 5.6, 5.6])


def test_a_tenant_wide_closure_reaches_every_warehouse(client: TestClient) -> None:
    """The union means a company closure needs no per-warehouse repetition."""
    payload = load_fixture()
    first_monday = horizon_weeks(payload)[0]

    payload["calendar"] = {
        "closed_dates": [first_monday.isoformat()],
        "closed_dates_by_warehouse": {},
    }

    covariate = covariate_by_series(
        payload,
        client.post("/v1/forecasts", json=payload, headers=AUTH).json(),
    )

    for series_id in (1042, 1198, 1255, 1403):
        assert covariate[series_id][0] == pytest.approx(4.6)


def test_a_fully_closed_horizon_has_no_business_days(client: TestClient) -> None:
    payload = load_fixture()

    payload["calendar"] = {
        "closed_dates": [
            (monday + timedelta(days=offset)).isoformat()
            for monday in horizon_weeks(payload)
            for offset in range(7)
        ],
        "closed_dates_by_warehouse": {},
    }

    results = client.post("/v1/forecasts", json=payload, headers=AUTH).json()["results"]
    profiled = [result for result in results if result["profile"] is not None]

    assert profiled
    for result in profiled:
        assert result["horizon_business_days"] == [0.0] * payload["horizon"]


# --- contract section 20: logging ---


def test_a_request_is_logged_with_the_contract_fields(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = load_fixture()

    with caplog.at_level("INFO", logger="pango_prediction.adapters.inbound.api.routes"):
        client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert len(caplog.records) == 1

    # `extra` fields are stored in the record's __dict__ rather than as typed
    # attributes of LogRecord, which is also how a structured handler reads them.
    logged = caplog.records[0].__dict__

    assert logged["request_id"] == payload["request_id"]
    assert logged["forecast_run_id"] == payload["forecast_run_id"]
    assert logged["series_count"] == len(payload["series"])
    assert logged["status"] == "partial"
    assert logged["duration_ms"] >= 0
    assert logged["policy_version"] == "development-1"


def test_the_log_carries_no_part_of_the_payload(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Section 20 forbids logging full histories.

    Beyond the privacy of a tenant's sales, a batch of 500 series would write
    megabytes per request.
    """
    payload = load_fixture()

    with caplog.at_level("INFO", logger="pango_prediction.adapters.inbound.api.routes"):
        client.post("/v1/forecasts", json=payload, headers=AUTH)

    text = caplog.records[0].getMessage()

    assert "quantity" not in text
    assert "closing_stock" not in text
    assert len(text) < 300

    first_demand = payload["series"][0]["demand"][0]
    assert first_demand["date"] not in text


def test_a_rejected_request_writes_no_forecast_record(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Authentication fails before the use case runs, so there is nothing to
    report about a forecast that never happened."""
    with caplog.at_level("INFO", logger="pango_prediction.adapters.inbound.api.routes"):
        response = client.post("/v1/forecasts", json=load_fixture())

    assert response.status_code == 401
    assert caplog.records == []
