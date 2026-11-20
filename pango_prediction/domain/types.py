from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class DemandObservation:
    date: date
    quantity: float


@dataclass(frozen=True, slots=True)
class StockObservation:
    date: date
    closing_stock: float


@dataclass(frozen=True, slots=True)
class ForecastSeriesInput:
    series_id: str
    product_id: str
    warehouse_id: str
    demand: tuple[DemandObservation, ...]
    stock: tuple[StockObservation, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    history_only: bool = False
    """The series is sent for its history and is never forecast or replenished.

    It marks what cannot be attributed to a warehouse that exists today, such as
    the sales of a warehouse that was later renamed or split. Nothing in the
    series' own data can say this, so it arrives as a flag.
    """


@dataclass(frozen=True, slots=True)
class RegularizedSeries:
    series_id: str
    dates: tuple[date, ...]
    values: tuple[float, ...]


class AvailabilityState(StrEnum):
    """Whether a week's demand can be read as real demand.

    AVAILABLE
        Demand was observable: either the product sold, or it did not sell while
        stock was on hand.
    CENSORED
        No sales and no closing stock, within a stockout episode no longer than
        the tenant's threshold. Real demand is unknown and at least as large as
        the observed zero.
    OUT_OF_ASSORTMENT
        The same situation, but the episode is longer than that threshold. The
        notebook (S26) reads these as the product no longer being supplied
        rather than as a stockout. The threshold is tenant-scoped: see
        `domain/parameters.py`.
    UNKNOWN
        Availability could not be determined, because there is no reliable
        stock information or the week lies outside the window in which the
        product was being sold.
    """

    AVAILABLE = "AVAILABLE"
    CENSORED = "CENSORED"
    OUT_OF_ASSORTMENT = "OUT_OF_ASSORTMENT"
    UNKNOWN = "UNKNOWN"


class StockHandling(StrEnum):
    """How a series is supplied, inferred from its own history (notebook S24).

    STOCK_MANAGED
        The product is normally held in stock, so a week without sales and
        without stock can be read as a stockout.
    MADE_TO_ORDER
        The product sells without being held in stock, so zero stock is its
        normal state and says nothing about demand.
    NOT_CLASSIFIABLE
        There are too few weeks with both a sale and a known opening balance to
        decide, so no week of the series can be evaluated for censoring.
    """

    STOCK_MANAGED = "STOCK_MANAGED"
    MADE_TO_ORDER = "MADE_TO_ORDER"
    NOT_CLASSIFIABLE = "NOT_CLASSIFIABLE"


class NoForecastReason(StrEnum):
    """Why a series is deliberately not forecast.

    Declining a series is a decision, not a failure: most of a catalogue is
    inactive products and warehouses that hold no stock, and none of that is
    something going wrong. The set is closed and was agreed with Route A, so a
    policy naming anything else is a broken artifact.

    The values are lower case because they are the ones a policy file states in
    its ``reason`` key and the ones the response returns, unchanged.

    INACTIVE
        The series has not sold within the tenant's inactivity window (S29).
    SOLD_TO_ORDER
        The series is routed away because it is sold to order. Reserved for a
        later policy: policy v1 has no rule that uses it. Not the same thing as
        `StockHandling.MADE_TO_ORDER`, which only describes how zero stock is
        read.
    NON_STOCK_WAREHOUSE
        The warehouse holds no stock, so its demand must not drive
        replenishment.
    NO_ACTIVE_WAREHOUSE
        The product has no active warehouse to receive a forecast.
    HISTORY_ONLY
        The series is sent for its history and is not itself forecast.
    """

    INACTIVE = "inactive"
    SOLD_TO_ORDER = "sold_to_order"
    NON_STOCK_WAREHOUSE = "non_stock_warehouse"
    NO_ACTIVE_WAREHOUSE = "no_active_warehouse"
    HISTORY_ONLY = "history_only"


@dataclass(frozen=True, slots=True)
class WeeklyAvailability:
    """Availability of a weekly series, aligned to ``RegularizedSeries.dates``.

    ``available_mask`` is false exactly on the weeks whose demand must not be
    read as real demand, which is the same set of weeks the notebook drops from
    the model series. ``closing_stock`` keeps the resolved weekly balance for
    traceability, with ``None`` where no stock information was available.
    ``stock_handling`` records why the series was, or was not, evaluated for
    censoring; the demand profile needs it too.
    """

    series_id: str
    dates: tuple[date, ...]
    states: tuple[AvailabilityState, ...]
    available_mask: tuple[bool, ...]
    closing_stock: tuple[float | None, ...]
    stock_handling: StockHandling


class DemandClass(StrEnum):
    """The Syntetos-Boylan quadrant of a series, computed on months (S27).

    The grain is not incidental. At weekly grain 91.9 % of periods are zero and
    1.2 % of units look like stable demand; aggregated monthly, 49.8 % do. The
    notebook therefore classifies monthly and forecasts weekly.

    SMOOTH, ERRATIC, INTERMITTENT, LUMPY
        The four quadrants of average demand interval against the squared
        coefficient of variation of the demand sizes.
    INSUFFICIENT
        There is a monthly series, but the statistics cannot be computed from
        it: a series with a single month of sales has no spread to measure.
        Not a rare edge case, 44 % of products.
    NOT_CLASSIFIABLE
        There is no monthly series to classify at all, because every month in
        the window was spent in a stockout.

    These values are a Route B proposal. `forecasts.demand_class` is a
    per-tenant enum that has not been created yet.
    """

    SMOOTH = "SMOOTH"
    ERRATIC = "ERRATIC"
    INTERMITTENT = "INTERMITTENT"
    LUMPY = "LUMPY"
    INSUFFICIENT = "INSUFFICIENT"
    NOT_CLASSIFIABLE = "NOT_CLASSIFIABLE"


class HistoryClass(StrEnum):
    """How much weekly history a series has (notebook E5).

    Length alone does not decide whether a series can be forecast; the notebook
    is explicit that what limits modelling is the scarcity of sales, not the
    length of the history. It decides whether a series can be forecast *on its
    own* or has to borrow from similar series.

    The boundaries are tenant calibration, not platform rules: they describe
    one catalogue's product life cycle.
    """

    NEW = "NEW"
    LIMITED = "LIMITED"
    MATURE = "MATURE"


@dataclass(frozen=True, slots=True)
class MonthlyDemand:
    """One calendar month of the classification grid (notebook 7.4).

    ``observable`` is false when half or more of the month's days fell in a
    stockout, which makes the month's total a censored number rather than
    demand. The statistics are computed over the observable months only.
    """

    month_start: date
    demand: float
    stockout_days: int
    observable: bool


@dataclass(frozen=True, slots=True)
class DemandProfile:
    """What a series' demand behaves like.

    Deliberately not *which model should handle it*: that is the policy's
    decision, and keeping the two apart is what lets Route A change its mind
    about routing without Route B changing any code.

    The monthly fields describe the classification grid; the weekly fields
    describe the series that will actually be forecast. They disagree on
    purpose, because the two grains answer different questions.
    """

    series_id: str
    demand_class: DemandClass
    history_class: HistoryClass
    stock_handling: StockHandling
    adi: float | None
    """Average demand interval: observable months per month with a sale."""
    cv2: float | None
    """Squared coefficient of variation of the non-zero monthly demands."""
    observable_months: int
    nonzero_months: int
    history_weeks: int
    observed_weeks: int
    """Weeks whose demand can be read as demand, so the maskable weeks removed."""
    weeks_since_last_sale: int
    inactive: bool
    evaluable: bool
    """Whether the series has enough observable weeks to be forecast alone (E1)."""


@dataclass(frozen=True, slots=True)
class ForecastPeriod:
    """One forecast week, labelled by the Monday that starts it.

    The interval is part of the result, not an extra: `forecast_periods` stores
    ``lower_bound`` and ``upper_bound`` as NOT NULL, so a point estimate alone
    cannot be persisted.
    """

    period_start: date
    estimate: float
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if self.lower < 0:
            raise ValueError("a demand bound cannot be negative")

        if not self.lower <= self.estimate <= self.upper:
            raise ValueError(
                "the estimate must lie inside its interval "
                f"({self.lower}, {self.estimate}, {self.upper})"
            )


@dataclass(frozen=True, slots=True)
class ForecastTotal:
    """The calibrated aggregate forecast across the requested horizon."""

    estimate: float
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if self.lower < 0:
            raise ValueError("a demand bound cannot be negative")
        if not self.lower <= self.estimate <= self.upper:
            raise ValueError(
                "the total estimate must lie inside its interval "
                f"({self.lower}, {self.estimate}, {self.upper})"
            )


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ForecastResult:
    series_id: str
    periods: tuple[ForecastPeriod, ...]
    model_name: str
    model_version: str
    policy_version: str
    warnings: tuple[str, ...] = ()
