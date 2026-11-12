from datetime import date, timedelta

import pytest

from pango_prediction.adapters.outbound.statsforecast import (
    StatsForecastModel,
    StatsForecastSpec,
    statsforecast_models,
)
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.types import RegularizedSeries
from pango_prediction.ports.model import ModelRequest


class FakeStatsForecastModel:
    def forecast(
        self,
        values: object,
        *,
        h: int,
        fitted: bool,
    ) -> dict[str, tuple[float, ...]]:
        assert fitted is True
        del values
        return {
            "mean": (2.0,) * h,
            "fitted": (float("nan"), 1.0, 2.0, 1.0, 2.0),
        }


def make_request() -> ModelRequest:
    dates = tuple(date(2026, 10, 5) + timedelta(weeks=i) for i in range(5))
    series = RegularizedSeries(
        series_id="PW-001",
        dates=dates,
        values=(1.0, 2.0, 3.0, 2.0, 3.0),
    )
    return ModelRequest(
        series=series,
        availability=build_weekly_availability(series, ()),
        horizon=2,
    )


def test_statsforecast_adapter_translates_point_and_total_forecasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast._load_model_type",
        lambda name: FakeStatsForecastModel,
    )
    model = StatsForecastModel(StatsForecastSpec("fake", "Fake"))
    request = make_request()

    periods = model.predict(request)
    total = model.predict_total(request, periods)

    assert [period.estimate for period in periods] == [2.0, 2.0]
    assert total.estimate == 4.0
    assert total.lower <= total.estimate <= total.upper


def test_statsforecast_model_names_are_registered_without_eager_native_imports() -> None:
    assert [model.name for model in statsforecast_models()] == [
        "ets",
        "auto_arima",
        "tsb",
        "adida",
        "imapa",
        "croston",
    ]
