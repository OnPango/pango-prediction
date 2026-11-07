"""HTTP request and response shapes for contract v1.

These are the only place Pydantic appears. They follow `docs/contract-v1.md`,
with the corrections the tenant schema forces and that
`tests/fixtures/README.md` records: identifiers are integers because
`product_warehouses.id` is an integer, `frequency` carries the schema's enum
casing, and there is no `made_to_order` flag because no such column exists and
the service derives it from history.
"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Schema(BaseModel):
    """Reject unknown fields so a contract drift surfaces instead of passing."""

    model_config = ConfigDict(extra="forbid")


class DemandRow(_Schema):
    date: date
    quantity: float


class StockRow(_Schema):
    date: date
    closing_stock: float


class CalendarSpec(_Schema):
    # Unresolved: whether closures are per request or per warehouse now that the
    # grain is product x warehouse. See docs/domain-rules.md §12.
    closed_dates: list[date] = Field(default_factory=list)


class SeriesRequest(_Schema):
    series_id: int = Field(description="product_warehouses.id")
    product_id: int = Field(description="products.id")
    warehouse_id: int = Field(description="warehouses.id")
    metadata: dict[str, Any] = Field(default_factory=dict)
    demand: list[DemandRow] = Field(default_factory=list)
    stock: list[StockRow] = Field(default_factory=list)


class ForecastRequest(_Schema):
    contract_version: Literal["1.0"]
    request_id: str = Field(min_length=1)
    forecast_run_id: int = Field(description="forecast_runs.id")
    frequency: Literal["WEEKLY"]
    horizon: Annotated[int, Field(ge=1)] = Field(
        description="forecast_runs.horizon, which the schema constrains to >= 1"
    )
    history_end: date
    calendar: CalendarSpec = Field(default_factory=CalendarSpec)
    series: Annotated[list[SeriesRequest], Field(min_length=1)]


class PeriodResponse(_Schema):
    """One forecast period.

    All three numbers are required, because `forecast_periods.lower_bound` and
    `upper_bound` are NOT NULL in the tenant schema. Nothing populates this yet:
    it arrives with the model port.
    """

    period_start: date
    estimate: float
    lower: float
    upper: float


class SeriesErrorResponse(_Schema):
    code: str
    message: str


class SeriesResultResponse(_Schema):
    series_id: int
    status: Literal["ok", "error"]
    periods: list[PeriodResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: SeriesErrorResponse | None = None


class SummaryResponse(_Schema):
    total: int
    ok: int
    error: int


class ForecastResponse(_Schema):
    contract_version: str
    service_version: str
    policy_version: str | None
    request_id: str
    forecast_run_id: int
    summary: SummaryResponse
    results: list[SeriesResultResponse]


class HealthResponse(_Schema):
    status: Literal["ok"]
    service: str
    service_version: str
