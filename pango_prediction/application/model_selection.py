"""Choose a model by rolling-origin accuracy on the series' own history."""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from pango_prediction.domain.availability import (
    build_weekly_availability,
    observed_demand,
)
from pango_prediction.domain.evaluation import (
    EvaluationPeriod,
    ForecastMetrics,
    evaluate_forecast,
)
from pango_prediction.domain.parameters import DomainParameters
from pango_prediction.domain.types import (
    ForecastSeriesInput,
    ModelSpec,
    RegularizedSeries,
    WeeklyAvailability,
)
from pango_prediction.ports.model import (
    InsufficientHistoryError,
    ModelError,
    ModelRequest,
    ModelUnavailableError,
)
from pango_prediction.ports.registry import ModelRegistry

MAX_BACKTEST_FOLDS = 2
MIN_BACKTEST_FOLDS = 2
MIN_TRAINING_WEEKS = 5
SEASONAL_PERIODS = 4

CANDIDATES = (
    ModelSpec("zero"),
    ModelSpec("naive"),
    ModelSpec("moving_average", {"window": 4}),
    ModelSpec("sba", {"alpha": 0.1}),
)


@dataclass(frozen=True, slots=True)
class ModelCandidateScore:
    model: ModelSpec
    folds: int
    wape: float | None
    bias: float | None
    mae: float
    mase: float | None
    mean_rank: float


@dataclass(frozen=True, slots=True)
class ModelSelection:
    model: ModelSpec
    scores: tuple[ModelCandidateScore, ...]
    excluded_models: tuple[tuple[str, str], ...] = ()


class ModelSelectionError(Exception):
    """Fewer than two registered models could be fairly compared."""


class InsufficientBacktestHistoryError(ModelSelectionError):
    """The series cannot provide enough full rolling-origin folds."""


@dataclass(frozen=True, slots=True)
class _BacktestFold:
    training: RegularizedSeries
    training_availability: WeeklyAvailability
    training_values: tuple[float, ...]
    validation: tuple[tuple[date, float, bool], ...]


def select_model(
    source: ForecastSeriesInput,
    regularized: RegularizedSeries,
    *,
    horizon: int,
    registry: ModelRegistry,
    parameters: DomainParameters,
) -> ModelSelection:
    """Return the candidate with the best average rank across four metrics.

    Every candidate uses the same recent, expanding-origin validation folds.
    A candidate that cannot produce every fold is excluded rather than being
    compared on an easier subset of the history.
    """
    folds = _build_folds(
        source,
        regularized,
        horizon=horizon,
        parameters=parameters,
    )
    candidates = tuple(
        candidate for candidate in CANDIDATES if candidate.name in registry
    )
    if len(candidates) < 2:
        raise ModelSelectionError(
            "At least two registered candidate models are required for comparison."
        )

    scores: list[ModelCandidateScore] = []
    failures: list[tuple[str, Exception]] = []

    for candidate in candidates:
        try:
            metrics = _evaluate_candidate(
                candidate,
                folds,
                horizon=horizon,
                registry=registry,
            )
        except (ModelError, ModelUnavailableError) as error:
            failures.append((candidate.name, error))
            continue

        mae = metrics.mae
        if mae is None:
            raise ModelSelectionError(
                f"Model {candidate.name!r} has no observable validation periods."
            )
        scores.append(
            ModelCandidateScore(
                model=candidate,
                folds=len(folds),
                wape=metrics.wape,
                bias=metrics.bias,
                mae=mae,
                mase=metrics.mase,
                mean_rank=0.0,
            )
        )

    if len(scores) < 2:
        if not scores and failures and all(
            isinstance(error, InsufficientHistoryError) for _, error in failures
        ):
            raise InsufficientBacktestHistoryError(
                "Fewer than two candidate models have enough observable history "
                "to complete every validation fold."
            )

        failure_summary = "; ".join(
            f"{name}: {error}" for name, error in failures
        )
        raise ModelSelectionError(
            "Fewer than two candidate models completed every validation fold. "
            f"{failure_summary}"
        )

    ranked = _rank_candidates(scores)
    return ModelSelection(
        model=ranked[0].model,
        scores=ranked,
        excluded_models=tuple(
            (name, type(error).__name__) for name, error in failures
        ),
    )


def _build_folds(
    source: ForecastSeriesInput,
    regularized: RegularizedSeries,
    *,
    horizon: int,
    parameters: DomainParameters,
) -> tuple[_BacktestFold, ...]:
    fold_count = min(
        MAX_BACKTEST_FOLDS,
        max(0, (len(regularized.dates) - MIN_TRAINING_WEEKS) // horizon),
    )
    if fold_count < MIN_BACKTEST_FOLDS:
        raise InsufficientBacktestHistoryError(
            "Model comparison requires at least two full validation folds "
            f"of {horizon} weeks after {MIN_TRAINING_WEEKS} training weeks."
        )

    folds: list[_BacktestFold] = []
    series_length = len(regularized.dates)

    for folds_from_end in range(fold_count, 0, -1):
        start = series_length - folds_from_end * horizon
        end = start + horizon
        training = RegularizedSeries(
            series_id=regularized.series_id,
            dates=regularized.dates[:start],
            values=regularized.values[:start],
        )
        training_end = training.dates[-1] + timedelta(days=6)
        training_stock = tuple(
            observation
            for observation in source.stock
            if observation.date <= training_end
        )
        training_availability = build_weekly_availability(
            training, training_stock, parameters
        )
        training_values = observed_demand(training, training_availability)

        validation_end = regularized.dates[end - 1] + timedelta(days=6)
        validation_prefix = RegularizedSeries(
            series_id=regularized.series_id,
            dates=regularized.dates[:end],
            values=regularized.values[:end],
        )
        validation_stock = tuple(
            observation
            for observation in source.stock
            if observation.date <= validation_end
        )
        validation_availability = build_weekly_availability(
            validation_prefix, validation_stock, parameters
        )
        validation = tuple(
            (
                regularized.dates[index],
                regularized.values[index],
                validation_availability.available_mask[index],
            )
            for index in range(start, end)
        )

        if sum(observable for _, _, observable in validation) >= 2:
            folds.append(
                _BacktestFold(
                    training=training,
                    training_availability=training_availability,
                    training_values=training_values,
                    validation=validation,
                )
            )

    if len(folds) < MIN_BACKTEST_FOLDS:
        raise InsufficientBacktestHistoryError(
            "Model comparison requires at least two validation folds with "
            "two or more observable weeks each."
        )

    return tuple(folds)


def _evaluate_candidate(
    candidate: ModelSpec,
    folds: tuple[_BacktestFold, ...],
    *,
    horizon: int,
    registry: ModelRegistry,
) -> ForecastMetrics:
    model = registry.resolve(candidate.name)
    all_periods: list[EvaluationPeriod] = []
    fold_mase: list[float] = []

    for fold in folds:
        request = ModelRequest(
            series=fold.training,
            availability=fold.training_availability,
            horizon=horizon,
            params=candidate.params,
        )
        predictions = model.predict(request)
        expected_dates = tuple(date for date, _, _ in fold.validation)
        if len(predictions) != horizon or tuple(
            period.period_start for period in predictions
        ) != expected_dates:
            raise ModelError(
                f"Model {candidate.name!r} did not return the expected "
                f"{horizon} validation periods."
            )

        periods = tuple(
            EvaluationPeriod(
                period_start=prediction.period_start,
                estimate=prediction.estimate,
                lower=prediction.lower,
                upper=prediction.upper,
                actual=actual,
                observable=observable,
            )
            for prediction, (_, actual, observable) in zip(
                predictions, fold.validation, strict=True
            )
        )
        all_periods.extend(periods)
        metrics = evaluate_forecast(
            periods,
            training_values=fold.training_values,
            seasonal_periods=SEASONAL_PERIODS,
        )
        if metrics.mae is None:
            raise ModelError(
                f"Model {candidate.name!r} has no observable validation periods."
            )
        if metrics.mase is not None:
            fold_mase.append(metrics.mase)

    aggregate = evaluate_forecast(all_periods, seasonal_periods=SEASONAL_PERIODS)
    if aggregate.mae is None:
        raise ModelError(
            f"Model {candidate.name!r} has no observable validation periods."
        )
    return ForecastMetrics(
        evaluated_periods=aggregate.evaluated_periods,
        actual_total=aggregate.actual_total,
        estimate_total=aggregate.estimate_total,
        error_total=aggregate.error_total,
        wape=aggregate.wape,
        bias=aggregate.bias,
        mae=aggregate.mae,
        mase=None if not fold_mase else fmean(fold_mase),
        interval_coverage=aggregate.interval_coverage,
        total_inside_interval=aggregate.total_inside_interval,
    )


def _rank_candidates(
    scores: list[ModelCandidateScore],
) -> tuple[ModelCandidateScore, ...]:
    metric_values: tuple[dict[str, float | None], ...] = (
        {score.model.name: score.wape for score in scores},
        {
            score.model.name: None if score.bias is None else abs(score.bias)
            for score in scores
        },
        {score.model.name: score.mae for score in scores},
        {score.model.name: score.mase for score in scores},
    )
    ranks: dict[str, list[float]] = {score.model.name: [] for score in scores}

    for values in metric_values:
        ordered = sorted(
            ((name, value) for name, value in values.items() if value is not None),
            key=lambda item: (item[1], item[0]),
        )
        start = 1
        while start <= len(ordered):
            end = start
            while end < len(ordered) and ordered[end][1] == ordered[start - 1][1]:
                end += 1
            average_rank = (start + end) / 2
            for name, _ in ordered[start - 1 : end]:
                ranks[name].append(average_rank)
            start = end + 1

    ranked = [
        ModelCandidateScore(
            model=score.model,
            folds=score.folds,
            wape=score.wape,
            bias=score.bias,
            mae=score.mae,
            mase=score.mase,
            mean_rank=fmean(ranks[score.model.name]),
        )
        for score in scores
    ]

    return tuple(
        sorted(
            ranked,
            key=lambda score: (
                score.mean_rank,
                float("inf") if score.wape is None else score.wape,
                float("inf") if score.bias is None else abs(score.bias),
                score.mae,
                float("inf") if score.mase is None else score.mase,
                score.model.name,
            ),
        )
    )
