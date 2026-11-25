"""API tests for the opt-in holdout validation of `/v1/forecasts`.

Two promises are tested here. A client that does not ask for the validation
receives exactly what it received before the block existed, so the Route A
parity and the 20-nov delivery are untouched. A client that asks for it gets,
per series, Route A's series-block score of the deployed policy on the last
complete weeks of the history it sent.
"""

import json
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pango_prediction.config import Settings
from pango_prediction.main import create_app

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
ROUTE_A_POLICY = ROOT / "config" / "model_policy.json"

TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

LAST_WEEK = date(2026, 10, 26)
TRAIN = [5.0] * 16 + [2.0, 3.0, 4.0, 5.0]
ACTUAL = [2.0, 4.0, 6.0, 3.0]


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        service_token=TOKEN,
        max_series_per_request=500,
        policy_path=str(ROUTE_A_POLICY),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def fixture(name: str = "forecast_request.json") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    payload["contract_version"] = "1.2"
    return payload


def weekly_series(values: Sequence[float], series_id: int = 1042) -> dict[str, Any]:
    """A sale on each week's Wednesday and ten units in stock every Sunday."""
    first = LAST_WEEK - timedelta(weeks=len(values) - 1)

    return {
        "series_id": series_id,
        "product_id": 318,
        "warehouse_id": 1,
        "demand": [
            {"date": (first + timedelta(weeks=i, days=2)).isoformat(), "quantity": v}
            for i, v in enumerate(values)
            if v > 0
        ],
        "stock": [
            {"date": (first + timedelta(weeks=i, days=6)).isoformat(), "closing_stock": 10.0}
            for i in range(len(values))
        ],
    }


def post(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/forecasts", json=payload, headers=AUTH)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- nothing changes for a client that does not ask ---


def test_without_the_flag_no_result_carries_a_validation_key(client: TestClient) -> None:
    body = post(client, fixture())

    assert all("validation" not in result for result in body["results"])


def test_an_explicit_false_is_the_same_response_as_leaving_it_out(
    client: TestClient,
) -> None:
    left_out = client.post("/v1/forecasts", json=fixture(), headers=AUTH)
    payload = fixture()
    payload["include_validation"] = False
    explicit = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert explicit.content == left_out.content


def test_asking_for_the_validation_does_not_change_the_forecast(
    client: TestClient,
) -> None:
    plain = post(client, fixture())
    payload = fixture()
    payload["include_validation"] = True
    validated = post(client, payload)

    for result in validated["results"]:
        assert "validation" in result
        del result["validation"]

    assert validated == plain


# --- what a client that asks receives ---


def test_a_validation_is_scored_with_route_a_convention(client: TestClient) -> None:
    payload = fixture("forecast_request_minimal.json")
    payload["include_validation"] = True
    payload["series"] = [weekly_series(TRAIN + ACTUAL)]

    validation = post(client, payload)["results"][0]["validation"]

    assert validation["convention"] == "route_a_series_block"
    assert validation["status"] == "evaluated"
    assert validation["reason"] is None
    assert validation["train_end"] == "2026-10-04"
    assert validation["forecast_status"] == "ok"
    assert validation["model_name"] == "moving_average"
    assert validation["counted_as_zero"] is False

    weeks = validation["weeks"]
    assert [week["period_start"] for week in weeks] == [
        "2026-10-05",
        "2026-10-12",
        "2026-10-19",
        "2026-10-26",
    ]
    assert [week["actual"] for week in weeks] == ACTUAL
    assert [week["estimate"] for week in weeks] == pytest.approx([3.5] * 4)
    assert all(week["evaluable"] for week in weeks)

    # y = 15, f = 14, e = f - y = -1: one unit under-forecast.
    metrics = validation["metrics"]
    assert metrics["evaluable_weeks"] == 4
    assert metrics["actual_total"] == 15.0
    assert metrics["forecast_total"] == pytest.approx(14.0)
    assert metrics["error"] == pytest.approx(-1.0)
    assert metrics["absolute_error"] == pytest.approx(1.0)
    assert metrics["wape"] == pytest.approx(1 / 15)
    assert metrics["bias"] == pytest.approx(-1 / 15)


def test_every_series_of_the_fixture_gets_a_validation_or_its_reason(
    client: TestClient,
) -> None:
    payload = fixture()
    payload["include_validation"] = True

    results = {result["series_id"]: result for result in post(client, payload)["results"]}

    for series_id in (1042, 1198, 1255):
        assert results[series_id]["validation"]["status"] == "evaluated"

    # A new series: it had not sold yet when the held-out weeks began.
    assert results[1403]["validation"]["status"] == "not_evaluated"
    assert results[1403]["validation"]["reason"] == "no_history_at_cutoff"
    # Invalid data is a failure, never a forecast of zero.
    assert results[1500]["validation"]["reason"] == "forecast_failed"
    assert results[1500]["validation"]["metrics"] is None


def test_a_legacy_client_still_reads_the_validation_in_full(client: TestClient) -> None:
    """The block is opt-in, so its vocabulary does not follow the version.

    The main status is translated for a 1.1 client, as before; inside the
    validation the policy's decision is named as it is.
    """
    payload = fixture("forecast_request_minimal.json")
    payload["contract_version"] = "1.1"
    payload["include_validation"] = True
    payload["series"] = [weekly_series([5.0] * 10 + [0.0] * 30 + ACTUAL)]

    result = post(client, payload)["results"][0]
    validation = result["validation"]

    assert validation["forecast_status"] == "no_forecast"
    assert validation["forecast_reason"] == "inactive"
    assert validation["counted_as_zero"] is True
    assert validation["metrics"]["error"] == -15.0
    assert [week["estimate"] for week in validation["weeks"]] == [0.0] * 4


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_include_validation_must_be_a_json_boolean(
    client: TestClient, value: object
) -> None:
    payload = fixture()
    payload["include_validation"] = value

    response = client.post("/v1/forecasts", json=payload, headers=AUTH)

    assert response.status_code == 422


def test_the_openapi_document_describes_the_validation(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    assert "include_validation" in components["ForecastRequest"]["properties"]
    assert "validation" in components["SeriesResultResponse"]["properties"]
    assert "ValidationResponse" in components
