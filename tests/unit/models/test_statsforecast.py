from collections.abc import Mapping
from datetime import date, timedelta

import pytest

from pango_prediction.adapters.outbound.statsforecast import (
    StatsForecastModel,
    StatsForecastSpec,
    statsforecast_models,
)
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.types import RegularizedSeries
from pango_prediction.ports.model import (
    ModelError,
    ModelRequest,
    ModelUnavailableError,
)


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


def make_request(params: Mapping[str, object] | None = None) -> ModelRequest:
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
        params=params or {},
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


# --- a misconfigured model is unavailable, not a failure of this series ------
#
# Everything below separates the two faults the contract keeps apart. A
# parameter the model cannot accept, or a library this build does not carry,
# is the same fault for every series routed to that model: MODEL_NOT_AVAILABLE.
# A library that raises while forecasting is this series' own failure:
# MODEL_FAILED. Neither may escape as a 500 and sink the whole batch.


@pytest.fixture
def fake_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast._load_model_type",
        lambda name: FakeStatsForecastModel,
    )


@pytest.mark.parametrize("season_length", [2.5, "4", True, None])
def test_a_season_length_that_is_not_an_integer_leaves_the_model_unavailable(
    fake_library: None, season_length: object
) -> None:
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelUnavailableError, match="integer 'season_length'"):
        model.predict(make_request({"season_length": season_length}))


def test_a_season_length_below_one_leaves_the_model_unavailable(
    fake_library: None,
) -> None:
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelUnavailableError, match="at least 1"):
        model.predict(make_request({"season_length": 0}))


@pytest.mark.parametrize("alpha_d", [0, 1.5, "0.3", None])
def test_a_tsb_alpha_outside_the_unit_range_leaves_the_model_unavailable(
    fake_library: None, alpha_d: object
) -> None:
    model = StatsForecastModel(StatsForecastSpec("tsb", "TSB"))

    with pytest.raises(ModelUnavailableError):
        model.predict(make_request({"alpha_d": alpha_d, "alpha_p": 0.3}))


def test_a_missing_statsforecast_dependency_leaves_the_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry names the model eagerly; the library loads lazily.

    So a build without `statsforecast` registers a name it cannot run, which is
    the same disagreement between build and policy as an unregistered name.
    """

    def no_library(name: str) -> object:
        raise ImportError(f"no module named {name!r}")

    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast.import_module",
        no_library,
    )
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelUnavailableError, match="statsforecast"):
        model.predict(make_request({"season_length": 1}))


def test_a_model_the_library_does_not_provide_leaves_the_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast.import_module",
        lambda name: object(),
    )
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelUnavailableError, match="does not provide"):
        model.predict(make_request({"season_length": 1}))


def test_a_library_that_raises_while_forecasting_fails_only_this_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingModel:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def forecast(self, values: object, *, h: int, fitted: bool) -> object:
            raise ArithmeticError("singular matrix")

    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast._load_model_type",
        lambda name: ExplodingModel,
    )
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelError, match="singular matrix"):
        model.predict(make_request({"season_length": 1}))


def test_a_fitted_series_of_the_wrong_length_fails_only_this_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ShortFittedModel:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def forecast(
            self, values: object, *, h: int, fitted: bool
        ) -> dict[str, tuple[float, ...]]:
            del values, fitted
            return {"mean": (2.0,) * h, "fitted": (1.0, 2.0)}

    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast._load_model_type",
        lambda name: ShortFittedModel,
    )
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelError, match="fitted"):
        model.predict(make_request({"season_length": 1}))


def test_a_parameter_the_library_itself_rejects_leaves_the_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter cannot know every constraint the library enforces.

    A season length this adapter accepts may still be refused by the library
    when it builds the model, and that is a fault of the policy, not of the
    series being forecast.
    """

    class PickyModel:
        def __init__(self, season_length: int) -> None:
            raise ValueError(f"season_length {season_length} is not supported")

    monkeypatch.setattr(
        "pango_prediction.adapters.outbound.statsforecast._load_model_type",
        lambda name: PickyModel,
    )
    model = StatsForecastModel(StatsForecastSpec("ets", "AutoETS"))

    with pytest.raises(ModelUnavailableError, match="rejected the parameters"):
        model.predict(make_request({"season_length": 7}))
