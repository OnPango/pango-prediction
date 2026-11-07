"""Weekly series construction.

Turns daily demand observations into a regular Monday-Sunday weekly series.

The rules implemented here mirror the demand-analysis notebook
(`docs/retail-inventory-forecasting-demand-analysis.ipynb`, sections 7.1 and 7.5)
so that the series evaluated during model selection and the series served in
production are built by the same code.
"""

from collections import defaultdict
from datetime import date, timedelta
from math import isfinite

from pango_prediction.domain.types import (
    ForecastSeriesInput,
    RegularizedSeries,
)

DAYS_PER_WEEK = 7


def week_start(day: date) -> date:
    """Return the Monday of the Monday-Sunday week that contains ``day``."""
    return day - timedelta(days=day.weekday())


def last_complete_week_start(history_end: date) -> date:
    """Return the Monday of the last week fully contained in the history.

    A week counts as complete only when its Sunday is still within the history.
    The notebook (7.5) drops the trailing partial week for the same reason: a
    week measured over fewer than seven days understates demand.
    """
    candidate = week_start(history_end)

    if candidate + timedelta(days=DAYS_PER_WEEK - 1) <= history_end:
        return candidate

    return candidate - timedelta(weeks=1)


def future_weeks(last_week: date, horizon: int) -> tuple[date, ...]:
    """The Mondays of the ``horizon`` weeks after ``last_week``.

    One definition, shared by the forecast periods and by the calendar
    covariate. If each derived its own, a week's business days could end up
    attached to a different week from the estimate they explain.
    """
    if horizon < 1:
        raise ValueError("the horizon must be at least one period")

    return tuple(
        last_week + timedelta(weeks=step) for step in range(1, horizon + 1)
    )


def build_weekly_series(
    series: ForecastSeriesInput,
    history_end: date,
) -> RegularizedSeries:
    """Build a regular weekly demand series for ``series``.

    The series starts at the first week with positive demand and ends at the
    last complete week of the history. Intermediate and trailing weeks without
    demand are filled with zero, so the grid has no gaps.

    Demand is gross demand: returns are not subtracted (notebook 5.3 B), which
    is why negative quantities are rejected instead of being accumulated.
    """
    weekly_demand: defaultdict[date, float] = defaultdict(float)

    for observation in series.demand:
        if not isfinite(observation.quantity):
            raise ValueError("Demand quantity must be finite")

        if observation.quantity < 0:
            raise ValueError("Demand quantity cannot be negative")

        if observation.date > history_end:
            raise ValueError(
                f"Demand observation {observation.date.isoformat()} is "
                f"after history_end {history_end.isoformat()}"
            )

        weekly_demand[week_start(observation.date)] += observation.quantity

    empty = RegularizedSeries(
        series_id=series.series_id,
        dates=(),
        values=(),
    )

    weeks_with_demand = [week for week, value in weekly_demand.items() if value > 0]

    if not weeks_with_demand:
        return empty

    first_week = min(weeks_with_demand)
    last_week = last_complete_week_start(history_end)

    if first_week > last_week:
        return empty

    dates: list[date] = []
    values: list[float] = []

    current_week = first_week

    while current_week <= last_week:
        dates.append(current_week)
        values.append(weekly_demand.get(current_week, 0.0))

        current_week += timedelta(weeks=1)

    return RegularizedSeries(
        series_id=series.series_id,
        dates=tuple(dates),
        values=tuple(values),
    )
