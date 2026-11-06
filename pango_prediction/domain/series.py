from collections import defaultdict
from datetime import date, timedelta
from math import isfinite

from pango_prediction.domain.types import (
    ForecastSeriesInput,
    RegularizedSeries,
)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_weekly_series(
    series: ForecastSeriesInput,
) -> RegularizedSeries:
    if not series.demand:
        return RegularizedSeries(
            series_id=series.series_id,
            dates=(),
            values=(),
        )

    weekly_demand: defaultdict[date, float] = defaultdict(float)

    for observation in series.demand:
        if not isfinite(observation.quantity):
            raise ValueError("Demand quantity must be finite")

        if observation.quantity < 0:
            raise ValueError("Demand quantity cannot be negative")

        week = _week_start(observation.date)
        weekly_demand[week] += observation.quantity

    first_week = min(weekly_demand)
    last_week = max(weekly_demand)

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