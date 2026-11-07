"""Demand profile and classification.

Describes what a series' demand behaves like, so that the policy can decide
which model should handle it. The two are kept apart on purpose: Route A owns
the routing, and it must be able to change its mind without Route B changing
code.

The rules come from the demand-analysis notebook (sections 7.4 and 7.5):

- demand is classified on the **monthly** series and forecast on the weekly one
  (S27). This is the least obvious rule in the analysis and skipping it would
  change every model assignment: at weekly grain 91.9 % of periods are zero and
  1.2 % of units look like stable demand, while aggregated monthly 49.8 % do;
- the monthly grid is built from the **daily** observations rather than by
  folding weeks together, because a week that straddles a month boundary would
  otherwise push its whole demand into one of the two months and the statistics
  would drift away from the ones Route A evaluated;
- a month is unobservable when half or more of its days fell in a stockout;
- the average demand interval and the squared coefficient of variation are
  computed over the observable months only, with Syntetos and Boylan's cutoffs;
- history length, activity and evaluability are read from the weekly series.
"""

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, stdev

from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    DomainParameters,
)
from pango_prediction.domain.series import DAYS_PER_WEEK
from pango_prediction.domain.types import (
    DemandClass,
    DemandObservation,
    DemandProfile,
    HistoryClass,
    MonthlyDemand,
    RegularizedSeries,
    WeeklyAvailability,
)

ADI_CUTOFF = 1.32
"""Average demand interval above which demand counts as intermittent.

From Syntetos and Boylan rather than fitted here, so it is a platform rule and
the same for every tenant.
"""

CV2_CUTOFF = 0.49
"""Squared coefficient of variation above which demand sizes count as erratic."""

MONTHS_PER_YEAR = 12

MIN_MONTHS_FOR_CV2 = 2
"""A spread needs two values. One month of sales has none, hence INSUFFICIENT."""


def month_start(day: date) -> date:
    """The first day of the calendar month that contains ``day``."""
    return day.replace(day=1)


def _shift_month(month: date, step: int) -> date:
    index = (month.year * MONTHS_PER_YEAR) + (month.month - 1) + step

    return date(index // MONTHS_PER_YEAR, (index % MONTHS_PER_YEAR) + 1, 1)


def _days_in_month(month: date) -> int:
    return monthrange(month.year, month.month)[1]


def last_complete_month_start(history_end: date) -> date:
    """The first day of the last calendar month fully inside the history.

    The monthly counterpart of `series.last_complete_week_start`, and for the
    same reason: a month measured over part of its days understates demand, and
    a short final month would read as a collapse in sales. The notebook ends
    its monthly grid in August while its weekly grid runs into September for
    exactly this reason.
    """
    candidate = month_start(history_end)

    if candidate.replace(day=_days_in_month(candidate)) <= history_end:
        return candidate

    return _shift_month(candidate, -1)


def _months_between(first: date, last: date) -> list[date]:
    months: list[date] = []
    current = first

    while current <= last:
        months.append(current)
        current = _shift_month(current, 1)

    return months


def _stockout_days_by_month(availability: WeeklyAvailability) -> dict[date, int]:
    """Count the days of each month that fell inside a masked week.

    Each masked week contributes its seven days to the months they land in, so
    a week spanning a month boundary splits across both. The masked weeks are
    the same ones `availability.observed_demand` drops, which keeps one
    definition of "cannot be read as demand" across the whole domain.
    """
    days: defaultdict[date, int] = defaultdict(int)

    for monday, available in zip(
        availability.dates, availability.available_mask, strict=True
    ):
        if available:
            continue

        for offset in range(DAYS_PER_WEEK):
            days[month_start(monday + timedelta(days=offset))] += 1

    return days


def _monthly_totals(demand: tuple[DemandObservation, ...]) -> dict[date, float]:
    totals: defaultdict[date, float] = defaultdict(float)

    for observation in demand:
        totals[month_start(observation.date)] += observation.quantity

    return dict(totals)


def build_monthly_series(
    demand: tuple[DemandObservation, ...],
    availability: WeeklyAvailability,
    history_end: date,
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> tuple[MonthlyDemand, ...]:
    """Build the monthly grid demand is classified on (notebook 7.4).

    The grid runs from the first month with a sale to the last complete month.
    Months in between without a sale are real zeros and are kept; months before
    the first sale are left out, because a series cannot be intermittent during
    a period in which it did not yet exist, and counting those months would
    make every new series look intermittent.

    Truncating the grid to a fixed number of months is off by default; see
    `DomainParameters.classification_window_months` for why.
    """
    totals = _monthly_totals(demand)
    last_month = last_complete_month_start(history_end)

    months_with_sales = [
        month for month, value in totals.items() if value > 0 and month <= last_month
    ]

    if not months_with_sales:
        return ()

    first_month = min(months_with_sales)
    window = parameters.classification_window_months

    if window is not None:
        first_month = max(first_month, _shift_month(last_month, -(window - 1)))

    stockout_days = _stockout_days_by_month(availability)
    grid: list[MonthlyDemand] = []

    for month in _months_between(first_month, last_month):
        out_of_stock = stockout_days.get(month, 0)

        grid.append(
            MonthlyDemand(
                month_start=month,
                demand=totals.get(month, 0.0),
                stockout_days=out_of_stock,
                observable=out_of_stock < _days_in_month(month) / 2,
            )
        )

    return tuple(grid)


def _observable_demand(grid: tuple[MonthlyDemand, ...]) -> list[float]:
    return [point.demand for point in grid if point.observable]


def intermittency(
    grid: tuple[MonthlyDemand, ...],
) -> tuple[float | None, float | None]:
    """The average demand interval and the squared coefficient of variation.

    ADI is observable months divided by months with a sale, which is the
    estimator the notebook uses rather than a mean of measured gaps. CV squared
    is taken over the **non-zero** months only, because it measures how uneven
    the demand sizes are, not how often demand happens: that is ADI's job.

    Either is ``None`` when it cannot be computed: ADI without a single sale,
    CV squared without two, since the spread of one number is undefined. The
    standard deviation is the sample one, matching the pandas default the
    notebook ran on; the population one would shift every CV squared downwards
    and move series across the cutoff.
    """
    observable = _observable_demand(grid)
    nonzero = [value for value in observable if value > 0]

    adi = len(observable) / len(nonzero) if nonzero else None

    cv2: float | None = None

    if len(nonzero) >= MIN_MONTHS_FOR_CV2:
        cv2 = (stdev(nonzero) / mean(nonzero)) ** 2

    return adi, cv2


def demand_quadrant(adi: float, cv2: float) -> DemandClass:
    """The Syntetos-Boylan quadrant for an already computed pair.

    The comparisons are strict, so a series sitting exactly on a cutoff falls
    on the irregular side of it. That is the order of the notebook's
    ``np.select`` and it decides the boundary cases.
    """
    if adi < ADI_CUTOFF and cv2 < CV2_CUTOFF:
        return DemandClass.SMOOTH

    if adi < ADI_CUTOFF:
        return DemandClass.ERRATIC

    if cv2 < CV2_CUTOFF:
        return DemandClass.INTERMITTENT

    return DemandClass.LUMPY


def classify_demand(grid: tuple[MonthlyDemand, ...]) -> DemandClass:
    """Classify a monthly grid, including the two ways it can fail.

    No observable month at all is NOT_CLASSIFIABLE: there is no series to
    classify. Observable months whose statistics cannot be computed are
    INSUFFICIENT: there is a series, and it is too short to measure.
    """
    if not _observable_demand(grid):
        return DemandClass.NOT_CLASSIFIABLE

    adi, cv2 = intermittency(grid)

    if adi is None or cv2 is None:
        return DemandClass.INSUFFICIENT

    return demand_quadrant(adi, cv2)


def classify_history(
    history_weeks: int,
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> HistoryClass:
    """How much weekly history a series has (notebook E5).

    The boundaries are inclusive upper bounds, matching the notebook's
    ``pd.cut`` bins: with the defaults 13 weeks is still new and 14 is limited.
    """
    if history_weeks <= parameters.new_history_max_weeks:
        return HistoryClass.NEW

    if history_weeks <= parameters.limited_history_max_weeks:
        return HistoryClass.LIMITED

    return HistoryClass.MATURE


def _weeks_since_last_sale(series: RegularizedSeries) -> int:
    last_sale = max(
        (index for index, value in enumerate(series.values) if value > 0),
        default=None,
    )

    if last_sale is None:
        return len(series.dates)

    return len(series.dates) - 1 - last_sale


def build_demand_profile(
    demand: tuple[DemandObservation, ...],
    series: RegularizedSeries,
    availability: WeeklyAvailability,
    history_end: date,
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> DemandProfile:
    """Profile one series.

    ``demand`` is the daily input the weekly ``series`` was built from: the
    monthly grid needs the days, not the weeks. ``history_end`` is the same
    cutoff the weekly series was built against, and the monthly grid ends at
    the last complete month of it rather than at the series' last week.
    """
    if not series.dates:
        raise ValueError("a demand profile needs a series with at least one week")

    if series.series_id != availability.series_id:
        raise ValueError("the series and its availability must be the same series")

    if series.dates != availability.dates:
        raise ValueError("the series and its availability must share one grid")

    grid = build_monthly_series(demand, availability, history_end, parameters)
    adi, cv2 = intermittency(grid)
    observable = _observable_demand(grid)

    history_weeks = len(series.dates)
    observed_weeks = sum(availability.available_mask)
    weeks_since_last_sale = _weeks_since_last_sale(series)

    return DemandProfile(
        series_id=series.series_id,
        demand_class=classify_demand(grid),
        history_class=classify_history(history_weeks, parameters),
        stock_handling=availability.stock_handling,
        adi=adi,
        cv2=cv2,
        observable_months=len(observable),
        nonzero_months=sum(1 for value in observable if value > 0),
        history_weeks=history_weeks,
        observed_weeks=observed_weeks,
        weeks_since_last_sale=weeks_since_last_sale,
        inactive=weeks_since_last_sale >= parameters.inactivity_weeks,
        evaluable=observed_weeks >= parameters.min_observed_weeks,
    )
