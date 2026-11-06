from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


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
    date: date
    value: float


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