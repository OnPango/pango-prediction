from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pango_prediction.config import Settings
from pango_prediction.main import create_app

TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(service_token=TOKEN, max_series_per_request=500)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def evaluation_payload() -> dict[str, Any]:
    return {
        "contract_version": "1.1",
        "request_id": "evaluation-1",
        "forecast_run_id": 41,
        "frequency": "WEEKLY",
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
                    },
                    {
                        "period_start": "2026-11-09",
                        "estimate": 4,
                        "lower": 3,
                        "upper": 5,
                        "actual": 3,
                        "observable": False,
                    },
                ],
                "training_values": [1, 2, 3, 4, 2],
                "total": {"estimate": 7, "lower": 4, "upper": 9},
            }
        ],
    }


def test_evaluation_returns_per_series_and_aggregate_metrics(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/forecast-evaluations",
        json=evaluation_payload(),
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "evaluation-1"
    assert body["results"][0]["metrics"]["evaluated_periods"] == 1
    assert body["results"][0]["metrics"]["wape"] == pytest.approx(2 / 5)
    assert body["aggregate"]["interval_coverage"] == 1.0
    assert body["aggregate"]["total_inside_interval"] is True


def test_evaluation_requires_service_authentication(client: TestClient) -> None:
    response = client.post(
        "/v1/forecast-evaluations",
        json=evaluation_payload(),
    )

    assert response.status_code == 401


def test_invalid_evaluation_interval_is_rejected(client: TestClient) -> None:
    payload = evaluation_payload()
    payload["series"][0]["periods"][0]["upper"] = 2

    response = client.post(
        "/v1/forecast-evaluations",
        json=payload,
        headers=AUTH,
    )

    assert response.status_code == 422
