from datetime import date, timedelta

import pytest

from pango_prediction.domain.evaluation import (
    BlockWeek,
    EvaluationPeriod,
    EvaluationTotal,
    evaluate_block,
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


# --- Route A's series-block convention (criteria.md sections 1.3 and 1.7) ---


def block(
    actuals: tuple[float, ...],
    *,
    forecasts: tuple[float, ...] | None = None,
    observable: tuple[bool, ...] | None = None,
    bounds: tuple[float, float] | None = (0.125, 5.625),
) -> tuple[BlockWeek, ...]:
    forecasts = (3.5,) * len(actuals) if forecasts is None else forecasts
    observable = (True,) * len(actuals) if observable is None else observable

    return tuple(
        BlockWeek(
            period_start=date(2026, 8, 3) + timedelta(weeks=index),
            forecast=forecast,
            actual=actual,
            observable=is_observable,
            lower=None if bounds is None else bounds[0],
            upper=None if bounds is None else bounds[1],
        )
        for index, (actual, forecast, is_observable) in enumerate(
            zip(actuals, forecasts, observable, strict=True)
        )
    )


def test_block_metrics_follow_route_a_on_a_worked_example() -> None:
    """Checked by hand, independently of the implementation.

    y = 2 + 4 + 6 + 3 = 15 and f = 4 x 3.5 = 14, so e = f - y = -1: the model
    forecast one unit too few. Week 3 (6) is above the upper bound 5.625.
    """
    metrics = evaluate_block(
        block((2.0, 4.0, 6.0, 3.0)),
        total=EvaluationTotal(estimate=14.0, lower=8.2, upper=20.4),
    )

    assert metrics is not None
    assert metrics.evaluable_weeks == 4
    assert metrics.actual_total == 15.0
    assert metrics.forecast_total == 14.0
    assert metrics.error == -1.0
    assert metrics.absolute_error == 1.0
    assert metrics.wape == pytest.approx(1 / 15)
    assert metrics.bias == pytest.approx(-1 / 15)
    assert metrics.weekly_coverage == pytest.approx(3 / 4)
    assert metrics.total_inside_interval is True


def test_block_error_is_measured_on_the_sum_not_week_by_week() -> None:
    """The grain is the decision: weekly errors of +5 and -5 cancel in a block.

    Week by week this would be a WAPE of 1.0; Route A measures the block total,
    which is what a four-week replenishment order depends on.
    """
    metrics = evaluate_block(
        block((10.0, 0.0), forecasts=(5.0, 5.0), bounds=(0.0, 10.0))
    )

    assert metrics is not None
    assert metrics.error == 0.0
    assert metrics.wape == 0.0


def test_a_positive_bias_means_the_model_forecast_too_much() -> None:
    metrics = evaluate_block(block((1.0, 1.0), forecasts=(2.0, 2.0)))

    assert metrics is not None
    assert metrics.error == 2.0
    assert metrics.bias == pytest.approx(1.0)


def test_unevaluable_weeks_leave_both_sums_and_the_total_coverage() -> None:
    metrics = evaluate_block(
        block((2.0, 4.0, 0.0, 3.0), observable=(True, True, False, True)),
        total=EvaluationTotal(estimate=14.0, lower=8.2, upper=20.4),
    )

    assert metrics is not None
    assert metrics.evaluable_weeks == 3
    assert metrics.actual_total == 9.0
    assert metrics.forecast_total == 10.5
    assert metrics.weekly_coverage == 1.0
    # A total over three weeks cannot be compared with a four-week interval.
    assert metrics.total_inside_interval is None


def test_a_block_with_fewer_than_two_evaluable_weeks_is_discarded() -> None:
    one = block((2.0, 0.0, 0.0, 0.0), observable=(True, False, False, False))
    two = block((2.0, 4.0, 0.0, 0.0), observable=(True, True, False, False))

    assert evaluate_block(one) is None
    assert evaluate_block(two) is not None


def test_zero_actual_demand_leaves_the_ratios_undefined() -> None:
    metrics = evaluate_block(block((0.0, 0.0, 0.0, 0.0)))

    assert metrics is not None
    assert metrics.error == 14.0
    assert metrics.wape is None
    assert metrics.bias is None


def test_a_forecast_counted_as_zero_has_no_interval_to_cover() -> None:
    """A declined series enters as f = 0 (Route A DEC-1) and has no interval."""
    metrics = evaluate_block(
        block((2.0, 4.0, 6.0, 3.0), forecasts=(0.0,) * 4, bounds=None)
    )

    assert metrics is not None
    assert metrics.error == -15.0
    assert metrics.wape == 1.0
    assert metrics.weekly_coverage is None
    assert metrics.total_inside_interval is None


@pytest.mark.parametrize(
    ("week", "message"),
    [
        (
            BlockWeek(date(2026, 8, 3), forecast=1.0, actual=-1.0, observable=True),
            "negative",
        ),
        (
            BlockWeek(date(2026, 8, 3), forecast=1.0, actual=float("nan"), observable=True),
            "finite",
        ),
        (
            BlockWeek(
                date(2026, 8, 3), forecast=float("inf"), actual=1.0, observable=True
            ),
            "finite",
        ),
        (
            BlockWeek(
                date(2026, 8, 3), forecast=6.0, actual=1.0, observable=True,
                lower=0.0, upper=5.0,
            ),
            "inside its interval",
        ),
        (
            BlockWeek(
                date(2026, 8, 3), forecast=1.0, actual=1.0, observable=True, lower=0.0
            ),
            "both bounds",
        ),
    ],
)
def test_an_invalid_block_week_is_rejected(week: BlockWeek, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate_block((week, week))


def test_an_empty_block_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        evaluate_block(())
