from datetime import date, timedelta

import pytest

from pango_prediction.domain.evaluation import (
    EvaluationPeriod,
    EvaluationTotal,
    evaluate_forecast,
    mase_scale,
)


def periods(
    actuals: tuple[float, ...],
    *,
    estimates: tuple[float, ...] | None = None,
    observable: tuple[bool, ...] | None = None,
) -> tuple[EvaluationPeriod, ...]:
    estimates = actuals if estimates is None else estimates
    observable = (True,) * len(actuals) if observable is None else observable
    return tuple(
        EvaluationPeriod(
            period_start=date(2026, 11, 2) + timedelta(weeks=index),
            estimate=estimate,
            lower=0.0,
            upper=max(actual, estimate) + 1.0,
            actual=actual,
            observable=is_observable,
        )
        for index, (actual, estimate, is_observable) in enumerate(
            zip(actuals, estimates, observable, strict=True)
        )
    )


def test_metrics_use_signed_errors_and_actual_denominator() -> None:
    metrics = evaluate_forecast(
        periods((5.0, 3.0), estimates=(3.0, 4.0)),
    )

    assert metrics.evaluated_periods == 2
    assert metrics.actual_total == 8.0
    assert metrics.estimate_total == 7.0
    assert metrics.error_total == 1.0
    assert metrics.wape == pytest.approx(3 / 8)
    assert metrics.bias == pytest.approx(1 / 8)
    assert metrics.mae == pytest.approx(1.5)
    assert metrics.interval_coverage == 1.0


def test_unobservable_periods_are_excluded_from_all_metrics() -> None:
    metrics = evaluate_forecast(
        periods(
            (5.0, 100.0),
            estimates=(3.0, 0.0),
            observable=(True, False),
        )
    )

    assert metrics.evaluated_periods == 1
    assert metrics.actual_total == 5.0
    assert metrics.error_total == 2.0
    assert metrics.mae == 2.0


def test_zero_actual_total_returns_undefined_ratio_metrics() -> None:
    metrics = evaluate_forecast(periods((0.0, 0.0), estimates=(0.0, 0.0)))

    assert metrics.wape is None
    assert metrics.bias is None
    assert metrics.mae == 0.0


def test_mase_uses_four_period_seasonal_naive_scale() -> None:
    assert mase_scale((1.0, 2.0, 3.0, 4.0, 2.0), 4) == pytest.approx(1.0)

    metrics = evaluate_forecast(
        periods((5.0, 3.0), estimates=(3.0, 4.0)),
        training_values=(1.0, 2.0, 3.0, 4.0, 2.0),
    )
    assert metrics.mase == pytest.approx(1.5)


def test_total_interval_is_evaluated_against_observable_actual_total() -> None:
    metrics = evaluate_forecast(
        periods((5.0, 3.0), estimates=(3.0, 4.0)),
        total=EvaluationTotal(estimate=7.0, lower=6.0, upper=9.0),
    )

    assert metrics.total_inside_interval is True


def test_invalid_period_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="inside its interval"):
        evaluate_forecast(
            (
                EvaluationPeriod(
                    period_start=date(2026, 11, 2),
                    estimate=5.0,
                    lower=0.0,
                    upper=4.0,
                    actual=5.0,
                ),
            )
        )
