"""Thin StatsForecast adapters for local statistical models.

The adapter translates between the model port and the pinned StatsForecast
implementation. It does not reimplement any forecasting algorithm.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from typing import Any

import numpy as np

from pango_prediction.adapters.outbound.baselines import _history
from pango_prediction.domain.intervals import (
    empirical_interval,
    horizon_total_interval,
)
from pango_prediction.domain.series import future_weeks
from pango_prediction.domain.types import ForecastPeriod, ForecastTotal
from pango_prediction.ports.model import (
    ModelError,
    ModelRequest,
    ModelUnavailableError,
)


@dataclass(frozen=True, slots=True)
class StatsForecastSpec:
    """Configuration needed to construct one StatsForecast model."""

    name: str
    model_type: str
    minimum_history: int = 2


def _positive_int_param(
    params: Mapping[str, object], key: str, model: str, default: int
) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelUnavailableError(f"{model} needs an integer '{key}' parameter")
    if value < 1:
        raise ModelUnavailableError(f"{model} needs '{key}' to be at least 1")
    return value


def _alpha_param(params: Mapping[str, object], key: str, model: str) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModelUnavailableError(f"{model} needs a numeric '{key}' parameter")
    result = float(value)
    if not 0 < result <= 1:
        raise ModelUnavailableError(
            f"{model} needs '{key}' between 0 (excluded) and 1"
        )
    return result


class StatsForecastModel:
    """Run one StatsForecast local model through the common model port."""

    def __init__(self, spec: StatsForecastSpec) -> None:
        self._spec = spec
        self.name = spec.name

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        values = _history(
            request,
            minimum=self._spec.minimum_history,
            model=self.name,
        )
        estimate, fitted = self._forecast(values, request.params, request.horizon)
        residuals = tuple(
            value - forecast
            for value, forecast in zip(values, fitted, strict=True)
            if forecast is not None
        )
        lower, upper = empirical_interval(estimate, residuals, request.quantiles)

        return tuple(
            ForecastPeriod(
                period_start=week,
                estimate=estimate,
                lower=lower,
                upper=upper,
            )
            for week in future_weeks(request.series.dates[-1], request.horizon)
        )

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del periods
        values = _history(
            request,
            minimum=self._spec.minimum_history,
            model=self.name,
        )
        estimate, fitted = self._forecast(values, request.params, request.horizon)
        total, lower, upper = horizon_total_interval(
            estimate,
            values,
            fitted,
            request.horizon,
            request.quantiles,
        )
        return ForecastTotal(estimate=total, lower=lower, upper=upper)

    def _forecast(
        self,
        values: tuple[float, ...],
        params: Mapping[str, object],
        horizon: int,
    ) -> tuple[float, tuple[float | None, ...]]:
        model = self._build_model(params)

        try:
            prediction = model.forecast(
                np.asarray(values, dtype=float),
                h=horizon,
                fitted=True,
            )
            estimate = float(prediction["mean"][0])
            fitted = tuple(
                None if not isfinite(float(value)) else float(value)
                for value in prediction["fitted"]
            )
        except Exception as error:  # noqa: BLE001 - foreign library boundary
            raise ModelError(
                f"StatsForecast model {self.name!r} failed while forecasting: "
                f"{error}"
            ) from error

        if len(fitted) != len(values):
            raise ModelError(
                f"StatsForecast model {self.name!r} returned {len(fitted)} fitted "
                f"values for {len(values)} observations"
            )
        return estimate, fitted

    def _build_model(self, params: Mapping[str, object]) -> Any:
        """Construct the library model the policy asked for.

        Every failure here is the same one for every series routed to this
        model: a parameter this adapter rejects, a parameter the library itself
        rejects, or a library the build does not carry. None of them is the
        current series' fault, so they all leave the model unavailable rather
        than failed.
        """
        model_type = _load_model_type(self._spec.model_type)
        season_length = _positive_int_param(
            params, "season_length", self.name, default=1
        )

        try:
            if self.name == "tsb":
                return model_type(
                    alpha_d=_alpha_param(params, "alpha_d", self.name),
                    alpha_p=_alpha_param(params, "alpha_p", self.name),
                )
            if self.name in {"ets", "auto_arima"}:
                return model_type(season_length=season_length)
            return model_type()
        except ModelUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 - foreign library boundary
            raise ModelUnavailableError(
                f"StatsForecast rejected the parameters of model {self.name!r}: "
                f"{error}"
            ) from error


def _load_model_type(name: str) -> type[Any]:
    try:
        module = import_module("statsforecast.models")
    except ImportError as error:
        raise ModelUnavailableError(
            "StatsForecast adapters require the statsforecast dependency"
        ) from error
    model_type = getattr(module, name, None)
    if not isinstance(model_type, type):
        raise ModelUnavailableError(
            f"StatsForecast does not provide model {name!r}"
        )
    return model_type


def _make_specs() -> tuple[StatsForecastSpec, ...]:
    return (
        StatsForecastSpec("ets", "AutoETS"),
        StatsForecastSpec("auto_arima", "AutoARIMA", minimum_history=3),
        StatsForecastSpec("tsb", "TSB"),
        StatsForecastSpec("adida", "ADIDA"),
        StatsForecastSpec("imapa", "IMAPA"),
        StatsForecastSpec("croston", "CrostonClassic"),
    )


def statsforecast_models() -> tuple[StatsForecastModel, ...]:
    """Construct all supported local StatsForecast model adapters."""
    return tuple(StatsForecastModel(spec) for spec in _make_specs())
