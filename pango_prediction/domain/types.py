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


@dataclass(frozen=True, slots=True)
class DemandProfile:
    series_id: str
    observed_periods: int
    nonzero_periods: int
    adi: float | None
    cv2: float | None
    demand_class: str | None
    inactive: bool
    short_history: bool


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
