"""Tests for the demand profile.

Every number asserted here comes from the demand-analysis notebook (7.4, 7.5).
Where a rule has a boundary, the boundary is what is tested: a series sitting
exactly on a cutoff is the case that decides whether two routes agree.
"""

from datetime import date, timedelta

import pytest

from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.parameters import DomainParameters
from pango_prediction.domain.profile import (
    build_demand_profile,
    build_monthly_series,
    classify_demand,
    classify_history,
    demand_quadrant,
    intermittency,
    last_complete_month_start,
)
from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    AvailabilityState,
    DemandClass,
    DemandObservation,
    DemandProfile,
    ForecastSeriesInput,
    HistoryClass,
    MonthlyDemand,
    RegularizedSeries,
    StockHandling,
    WeeklyAvailability,
)

SERIES_ID = "PW-001"


def daily(*pairs: tuple[date, float]) -> tuple[DemandObservation, ...]:
    return tuple(DemandObservation(date=day, quantity=value) for day, value in pairs)


def weeks_from(first_monday: date, count: int) -> tuple[date, ...]:
    return tuple(first_monday + timedelta(weeks=index) for index in range(count))


def availability_for(
    dates: tuple[date, ...],
    masked: frozenset[int] = frozenset(),
) -> WeeklyAvailability:
    """An availability whose mask is set directly, to isolate the profile."""
    return WeeklyAvailability(
        series_id=SERIES_ID,
        dates=dates,
        states=tuple(
            AvailabilityState.CENSORED if index in masked else AvailabilityState.AVAILABLE
            for index in range(len(dates))
        ),
        available_mask=tuple(index not in masked for index in range(len(dates))),
        closing_stock=tuple(None for _ in dates),
        stock_handling=StockHandling.STOCK_MANAGED,
    )


def month(year: int, index: int, value: float, *, observable: bool = True) -> MonthlyDemand:
    return MonthlyDemand(
        month_start=date(year, index, 1),
        demand=value,
        stockout_days=0 if observable else 31,
        observable=observable,
    )


# --- where the monthly grid starts and ends ---


def test_a_month_is_complete_only_when_its_last_day_is_in_the_history() -> None:
    assert last_complete_month_start(date(2026, 5, 31)) == date(2026, 5, 1)
    assert last_complete_month_start(date(2026, 5, 30)) == date(2026, 4, 1)


def test_the_grid_ends_at_the_last_complete_month() -> None:
    """Not at the series' last week: a part-month would read as a collapse."""
    grid = build_monthly_series(
        daily((date(2026, 2, 10), 4.0)),
        availability_for(weeks_from(date(2026, 2, 9), 18)),
        history_end=date(2026, 6, 15),
    )

    assert [point.month_start for point in grid] == [
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]


def test_months_before_the_first_sale_are_left_out() -> None:
    """A series cannot be intermittent before it existed.

    Counting those months as zeros would inflate ADI and make every new series
    look intermittent, which the history class already describes properly.
    """
    grid = build_monthly_series(
        daily((date(2026, 4, 20), 6.0)),
        availability_for(weeks_from(date(2026, 4, 20), 10)),
        history_end=date(2026, 6, 30),
    )

    assert grid[0].month_start == date(2026, 4, 1)
    assert len(grid) == 3


def test_a_month_without_a_sale_inside_the_range_is_a_real_zero() -> None:
    grid = build_monthly_series(
        daily((date(2026, 2, 10), 4.0), (date(2026, 4, 20), 6.0)),
        availability_for(weeks_from(date(2026, 2, 9), 18)),
        history_end=date(2026, 5, 31),
    )

    assert [point.demand for point in grid] == [4.0, 0.0, 6.0, 0.0]


def test_a_series_with_no_sale_has_no_grid() -> None:
    assert build_monthly_series((), availability_for(()), date(2026, 5, 31)) == ()


def test_the_window_truncates_the_grid_when_the_tenant_sets_one() -> None:
    """Off by default, because the notebook's classification did not truncate."""
    demand = daily((date(2026, 1, 15), 5.0), (date(2026, 5, 15), 5.0))
    availability = availability_for(weeks_from(date(2026, 1, 12), 22))

    whole = build_monthly_series(demand, availability, date(2026, 5, 31))
    windowed = build_monthly_series(
        demand,
        availability,
        date(2026, 5, 31),
        DomainParameters(classification_window_months=3),
    )

    assert [point.month_start for point in whole] == [
        date(2026, month_index, 1) for month_index in (1, 2, 3, 4, 5)
    ]
    assert [point.month_start for point in windowed] == [
        date(2026, month_index, 1) for month_index in (3, 4, 5)
    ]


# --- when a month stops being readable ---


def test_half_a_month_in_a_stockout_makes_it_unobservable() -> None:
    """February 2026 has 28 days, so two masked weeks are exactly half.

    The notebook's condition is ``stockout_days < days_in_month / 2``, so a
    month sitting exactly on half is unobservable.
    """
    demand = daily((date(2026, 2, 3), 9.0))
    dates = weeks_from(date(2026, 2, 2), 4)

    exactly_half = build_monthly_series(
        demand, availability_for(dates, frozenset({0, 1})), date(2026, 2, 28)
    )
    one_week = build_monthly_series(
        demand, availability_for(dates, frozenset({0})), date(2026, 2, 28)
    )

    assert exactly_half[0].stockout_days == 14
    assert exactly_half[0].observable is False

    assert one_week[0].stockout_days == 7
    assert one_week[0].observable is True


def test_a_week_spanning_two_months_splits_its_days_between_them() -> None:
    """The week of 30 March 2026 gives 2 days to March and 5 to April."""
    grid = build_monthly_series(
        daily((date(2026, 3, 3), 5.0)),
        availability_for(weeks_from(date(2026, 3, 2), 8), frozenset({4})),
        history_end=date(2026, 4, 30),
    )

    by_month = {point.month_start: point.stockout_days for point in grid}

    assert by_month[date(2026, 3, 1)] == 2
    assert by_month[date(2026, 4, 1)] == 5


# --- the two statistics ---


def test_adi_is_observable_months_over_months_with_a_sale() -> None:
    grid = (month(2026, 1, 10.0), month(2026, 2, 0.0), month(2026, 3, 20.0), month(2026, 4, 0.0))

    adi, _ = intermittency(grid)

    assert adi == pytest.approx(2.0)


def test_cv_squared_uses_the_non_zero_months_only() -> None:
    """Over (10, 20): sample standard deviation 7.0711, mean 15.

    If the zero months were included the mean would halve and CV squared would
    be four times larger, which would move the series across the cutoff.
    """
    grid = (month(2026, 1, 10.0), month(2026, 2, 0.0), month(2026, 3, 20.0), month(2026, 4, 0.0))

    _, cv2 = intermittency(grid)

    assert cv2 == pytest.approx(0.2222222, abs=1e-6)


def test_cv_squared_is_the_sample_standard_deviation() -> None:
    """Over (10, 20, 30): sample 10, population 8.165.

    The notebook ran on the pandas default, which is the sample one. The
    population version would give 0.1667 here instead of 0.25.
    """
    grid = (month(2026, 1, 10.0), month(2026, 2, 20.0), month(2026, 3, 30.0))

    _, cv2 = intermittency(grid)

    assert cv2 == pytest.approx(0.25)


def test_an_unobservable_month_is_left_out_of_both_statistics() -> None:
    grid = (
        month(2026, 1, 10.0),
        month(2026, 2, 0.0, observable=False),
        month(2026, 3, 20.0),
    )

    adi, cv2 = intermittency(grid)

    assert adi == pytest.approx(1.0)
    assert cv2 == pytest.approx(0.2222222, abs=1e-6)


def test_one_month_of_sales_has_no_measurable_spread() -> None:
    grid = (month(2026, 1, 10.0), month(2026, 2, 0.0))

    adi, cv2 = intermittency(grid)

    assert adi == pytest.approx(2.0)
    assert cv2 is None


# --- the quadrants ---


@pytest.mark.parametrize(
    "adi,cv2,expected",
    [
        (1.0, 0.1, DemandClass.SMOOTH),
        (1.0, 1.0, DemandClass.ERRATIC),
        (3.0, 0.1, DemandClass.INTERMITTENT),
        (3.0, 1.0, DemandClass.LUMPY),
    ],
)
def test_the_four_quadrants(adi: float, cv2: float, expected: DemandClass) -> None:
    assert demand_quadrant(adi, cv2) == expected


@pytest.mark.parametrize(
    "adi,cv2,expected",
    [
        (1.31, 0.48, DemandClass.SMOOTH),
        (1.32, 0.48, DemandClass.INTERMITTENT),
        (1.31, 0.49, DemandClass.ERRATIC),
        (1.32, 0.49, DemandClass.LUMPY),
    ],
)
def test_a_series_exactly_on_a_cutoff_falls_on_the_irregular_side(
    adi: float,
    cv2: float,
    expected: DemandClass,
) -> None:
    """The comparisons are strict, matching the notebook's ``np.select``."""
    assert demand_quadrant(adi, cv2) == expected


def test_a_series_too_short_to_measure_is_insufficient() -> None:
    """44 % of the catalogue: there is a series, it just has one sale."""
    assert classify_demand((month(2026, 1, 10.0), month(2026, 2, 0.0))) is (
        DemandClass.INSUFFICIENT
    )


def test_observable_months_without_a_single_sale_are_insufficient() -> None:
    grid = (month(2026, 1, 10.0, observable=False), month(2026, 2, 0.0))

    assert classify_demand(grid) is DemandClass.INSUFFICIENT


def test_a_grid_with_no_observable_month_is_not_classifiable() -> None:
    """A different answer from INSUFFICIENT: there is no series at all here."""
    grid = (
        month(2026, 1, 10.0, observable=False),
        month(2026, 2, 20.0, observable=False),
    )

    assert classify_demand(grid) is DemandClass.NOT_CLASSIFIABLE


def test_an_empty_grid_is_not_classifiable() -> None:
    assert classify_demand(()) is DemandClass.NOT_CLASSIFIABLE


# Route A's `demand_class` enum, from its reply on the policy artifact.
ROUTE_A_DEMAND_CLASSES = frozenset(
    {
        "SMOOTH",
        "ERRATIC",
        "INTERMITTENT",
        "LUMPY",
        "INSUFFICIENT",
        "NOT_CLASSIFIABLE",
    }
)


def test_the_demand_classes_match_the_policy_enum_exactly() -> None:
    """A class the policy cannot express is a class no rule can route."""
    assert {member.value for member in DemandClass} == ROUTE_A_DEMAND_CLASSES


# --- history and activity, read from the weekly series ---


@pytest.mark.parametrize(
    "weeks,expected",
    [
        (1, HistoryClass.NEW),
        (13, HistoryClass.NEW),
        (14, HistoryClass.LIMITED),
        (52, HistoryClass.LIMITED),
        (53, HistoryClass.MATURE),
    ],
)
def test_the_history_class_boundaries(weeks: int, expected: HistoryClass) -> None:
    """Inclusive upper bounds, matching the notebook's ``pd.cut`` bins."""
    assert classify_history(weeks) == expected


def profile_of(
    values: tuple[float, ...],
    *,
    masked: frozenset[int] = frozenset(),
    parameters: DomainParameters | None = None,
) -> DemandProfile:
    first_monday = date(2026, 1, 5)
    dates = weeks_from(first_monday, len(values))
    series = RegularizedSeries(series_id=SERIES_ID, dates=dates, values=values)

    return build_demand_profile(
        demand=daily(
            *(
                (day + timedelta(days=2), value)
                for day, value in zip(dates, values, strict=True)
                if value > 0
            )
        ),
        series=series,
        availability=availability_for(dates, masked),
        history_end=dates[-1] + timedelta(days=6),
        parameters=parameters or DomainParameters(),
    )


def test_a_series_is_inactive_at_the_window_and_active_one_week_before() -> None:
    """26 weeks without a sale, which is the default inactivity window."""
    at_window = profile_of((5.0,) + (0.0,) * 26)
    one_week_short = profile_of((5.0,) + (0.0,) * 25)

    assert at_window.weeks_since_last_sale == 26
    assert at_window.inactive is True

    assert one_week_short.weeks_since_last_sale == 25
    assert one_week_short.inactive is False


def test_evaluability_counts_observable_weeks_not_weeks_of_history() -> None:
    """A stocked-out week is not evidence, so it does not count towards E1."""
    masked = frozenset(range(2, 7))

    profile = profile_of((5.0,) * 12, masked=masked)

    assert profile.history_weeks == 12
    assert profile.observed_weeks == 7
    assert profile.evaluable is False

    assert profile_of((5.0,) * 12).evaluable is True


# --- the guards, and the whole pipeline ---


def test_profiling_an_empty_series_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one week"):
        build_demand_profile(
            demand=(),
            series=RegularizedSeries(series_id=SERIES_ID, dates=(), values=()),
            availability=availability_for(()),
            history_end=date(2026, 5, 31),
        )


def test_a_mismatched_availability_grid_is_refused() -> None:
    dates = weeks_from(date(2026, 1, 5), 3)

    with pytest.raises(ValueError, match="share one grid"):
        build_demand_profile(
            demand=daily((date(2026, 1, 7), 5.0)),
            series=RegularizedSeries(
                series_id=SERIES_ID, dates=dates, values=(5.0, 5.0, 5.0)
            ),
            availability=availability_for(dates[:2]),
            history_end=date(2026, 1, 31),
        )


def test_profiling_a_real_series_end_to_end() -> None:
    """A product selling every Wednesday for twenty weeks, with no stock data.

    Built through the same functions the service calls, so the weekly and the
    monthly grid are derived rather than hand-written. Monthly demand comes out
    as (20, 20, 20, 25, 15): five observable months, all with a sale, so
    ADI is 1.0 and CV squared is 0.03125, which is smooth demand.
    """
    sales = []
    wednesday = date(2026, 1, 7)

    while wednesday <= date(2026, 5, 20):
        sales.append((wednesday, 5.0))
        wednesday += timedelta(weeks=1)

    series_input = ForecastSeriesInput(
        series_id=SERIES_ID,
        product_id="P-1",
        warehouse_id="W-1",
        demand=daily(*sales),
        stock=(),
    )
    history_end = date(2026, 5, 31)

    series = build_weekly_series(series_input, history_end)
    availability = build_weekly_availability(series, series_input.stock)
    profile = build_demand_profile(
        series_input.demand, series, availability, history_end
    )

    assert profile.history_weeks == 21
    assert profile.history_class is HistoryClass.LIMITED
    assert profile.weeks_since_last_sale == 1
    assert profile.inactive is False
    assert profile.evaluable is True

    assert profile.observable_months == 5
    assert profile.nonzero_months == 5
    assert profile.adi == pytest.approx(1.0)
    assert profile.cv2 == pytest.approx(0.03125)
    assert profile.demand_class is DemandClass.SMOOTH

    # No stock history at all, so censoring could not be evaluated (S24).
    assert profile.stock_handling is StockHandling.NOT_CLASSIFIABLE
