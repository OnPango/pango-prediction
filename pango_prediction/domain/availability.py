"""Availability analysis.

Decides, week by week, whether the demand recorded for a series can be read as
real demand or whether it was limited by the product not being on hand.

Availability is derived from stock history, never received as a resolved flag
(plan B5). The rules and thresholds come from the demand-analysis notebook
(`docs/retail-inventory-forecasting-demand-analysis.ipynb`, sections 7.2 and 7.3):

- the weekly closing balance is the last daily closing of the week, and a week
  without stock movement keeps the previous balance;
- a negative balance means nothing available, not negative availability;
- a week with no sales and no closing stock is a stockout;
- a week that sold and closed at zero stays observed (S25);
- consecutive stockout weeks form an episode, and an episode longer than
  ``MAX_STOCKOUT_EPISODE_WEEKS`` is read as the product no longer being
  supplied (S26) instead of as a stockout;
- weeks after the last sale are inactivity, not censoring, and are left to the
  demand profile;
- censoring is only evaluated for series that are actually held in stock, which
  is itself inferred from the stock the series opened its sales with (S24).
"""

from datetime import date, timedelta
from math import isfinite

from pango_prediction.domain.series import DAYS_PER_WEEK
from pango_prediction.domain.types import (
    AvailabilityState,
    RegularizedSeries,
    StockHandling,
    StockObservation,
    WeeklyAvailability,
)

STOCK_TOLERANCE = 1e-6
"""Tolerance for comparing a stock balance against zero (notebook ``TOL``)."""

MAX_STOCKOUT_EPISODE_WEEKS = 13
"""Longest stockout episode still read as a stockout (notebook S26).

Thirteen weeks is roughly four replenishment cycles; beyond that the notebook
reads the gap as the product no longer being supplied.
"""

MADE_TO_ORDER_THRESHOLD = 0.5
"""Share of selling weeks that may start without stock (notebook S24).

At or above this share the product is read as sold to order rather than held in
stock. The notebook reports that moving the threshold to 0.25 or 0.75 changes
the classification of only 3.7 % of units.
"""

MIN_WEEKS_WITH_STOCK_DATA = 4
"""Selling weeks with a known opening balance needed to classify (notebook S24)."""


def _daily_closing_stock(
    stock: tuple[StockObservation, ...],
) -> dict[date, float]:
    """Collapse stock observations to one closing balance per day.

    When a day carries several observations the last one in the received order
    wins, which mirrors reading the final balance of the day in the ledger.
    """
    closing_by_day: dict[date, float] = {}

    for observation in stock:
        if not isfinite(observation.closing_stock):
            raise ValueError("Closing stock must be finite")

        closing_by_day[observation.date] = observation.closing_stock

    return closing_by_day


def _resolve_weekly_stock(
    weeks: tuple[date, ...],
    stock: tuple[StockObservation, ...],
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """Resolve the opening and closing balance of every week in ``weeks``.

    A week closes with its own latest observation; a week without any
    observation carries the previous balance forward. A week opens with the
    previous week's closing, which is the notebook's ``shift()`` on the stock
    column. Observations dated before the series starts seed that carry, so a
    series whose first weeks had no stock movement is not reported as unknown.
    """
    closing_by_day = _daily_closing_stock(stock)

    if not weeks:
        return (), ()

    carried: float | None = None

    for day in sorted(day for day in closing_by_day if day < weeks[0]):
        carried = closing_by_day[day]

    opening: list[float | None] = []
    closing: list[float | None] = []

    for monday in weeks:
        opening.append(carried)

        sunday = monday + timedelta(days=DAYS_PER_WEEK - 1)

        observed_days = sorted(
            day for day in closing_by_day if monday <= day <= sunday
        )

        if observed_days:
            carried = closing_by_day[observed_days[-1]]

        closing.append(carried)

    return tuple(opening), tuple(closing)


def _available_quantity(closing_stock: float) -> float:
    """A negative balance means nothing available (notebook 7.2)."""
    return max(closing_stock, 0.0)


def _classify_stock_handling(
    values: tuple[float, ...],
    opening_stock: tuple[float | None, ...],
) -> StockHandling:
    """Decide how a series is supplied from the stock it opened its sales with.

    Only weeks that both sold and have a known opening balance can inform the
    decision. ``NOT_CLASSIFIABLE`` takes precedence over ``MADE_TO_ORDER``,
    matching the notebook, where the ``sin_dato`` assignment is applied last and
    therefore overrides ``bajo_pedido``.
    """
    openings: list[float] = []

    for index, demand in enumerate(values):
        opening = opening_stock[index]

        if demand > 0 and opening is not None:
            openings.append(opening)

    if len(openings) < MIN_WEEKS_WITH_STOCK_DATA:
        return StockHandling.NOT_CLASSIFIABLE

    started_empty = sum(
        1 for opening in openings if _available_quantity(opening) <= STOCK_TOLERANCE
    )

    if started_empty / len(openings) >= MADE_TO_ORDER_THRESHOLD:
        return StockHandling.MADE_TO_ORDER

    return StockHandling.STOCK_MANAGED


def classify_stock_handling(
    series: RegularizedSeries,
    stock: tuple[StockObservation, ...],
) -> StockHandling:
    """Classify how ``series`` is supplied (notebook S24)."""
    opening_stock, _ = _resolve_weekly_stock(series.dates, stock)

    return _classify_stock_handling(series.values, opening_stock)


def _last_week_with_demand(values: tuple[float, ...]) -> int | None:
    for index in range(len(values) - 1, -1, -1):
        if values[index] > 0:
            return index

    return None


def _resolve_episodes(
    states: list[AvailabilityState],
    is_stockout: list[bool],
) -> None:
    """Label each run of consecutive stockout weeks by its length.

    Mutates ``states`` in place. Runs are broken by any week that is not a
    stockout, so an undetermined week cannot extend an episode.
    """
    start = 0

    while start < len(is_stockout):
        if not is_stockout[start]:
            start += 1
            continue

        end = start

        while end < len(is_stockout) and is_stockout[end]:
            end += 1

        length = end - start

        state = (
            AvailabilityState.CENSORED
            if length <= MAX_STOCKOUT_EPISODE_WEEKS
            else AvailabilityState.OUT_OF_ASSORTMENT
        )

        for index in range(start, end):
            states[index] = state

        start = end


def build_weekly_availability(
    series: RegularizedSeries,
    stock: tuple[StockObservation, ...],
) -> WeeklyAvailability:
    """Classify the availability of every week of ``series``.

    ``series`` must already be a regular weekly grid, so that availability and
    demand share one definition of the week.
    """
    opening_stock, closing_stock = _resolve_weekly_stock(series.dates, stock)
    stock_handling = _classify_stock_handling(series.values, opening_stock)
    last_sale = _last_week_with_demand(series.values)

    states: list[AvailabilityState] = []
    is_stockout: list[bool] = []

    for index, demand in enumerate(series.values):
        balance = closing_stock[index]

        if last_sale is None or index > last_sale:
            # After the last sale the zeros are inactivity, not censoring.
            state = AvailabilityState.UNKNOWN
        elif demand > 0:
            # It sold, so it was available. This also covers a week that sold
            # until it ran out (S25).
            state = AvailabilityState.AVAILABLE
        elif balance is None:
            state = AvailabilityState.UNKNOWN
        elif _available_quantity(balance) > STOCK_TOLERANCE:
            # Stock was on hand and it did not sell: real zero demand.
            state = AvailabilityState.AVAILABLE
        elif stock_handling is StockHandling.STOCK_MANAGED:
            state = AvailabilityState.CENSORED
        else:
            # Zero stock is either this product's normal state or a situation
            # we cannot read, so the week says nothing about demand (S24).
            state = AvailabilityState.UNKNOWN

        states.append(state)
        is_stockout.append(state == AvailabilityState.CENSORED)

    _resolve_episodes(states, is_stockout)

    return WeeklyAvailability(
        series_id=series.series_id,
        dates=series.dates,
        states=tuple(states),
        available_mask=tuple(
            state
            not in (
                AvailabilityState.CENSORED,
                AvailabilityState.OUT_OF_ASSORTMENT,
            )
            for state in states
        ),
        closing_stock=closing_stock,
        stock_handling=stock_handling,
    )
