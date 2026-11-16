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

from pydantic import BaseModel, ConfigDict, Field, field_validator

REJECTED_METADATA_KEY = "made_to_order"
"""The one key `metadata` refuses. See `SeriesRequest.refuse_a_made_to_order_flag`."""


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
    """The dates the tenant does not operate.

    Two levels, because closures are not all of one kind: a public holiday or a
    company shutdown closes everything, while an inventory count closes one
    warehouse. They resolve as a union, so a warehouse cannot re-open a day the
    tenant declared closed. This settles the question this class used to carry
    as a comment; see `docs/domain-rules.md` §7.

    The service computes no holidays and knows no country. A country code would
    cover the public holidays and miss the company shutdown, Holy Saturday and
    every per-warehouse difference, so the dates are data.
    """

    closed_dates: list[date] = Field(
        default_factory=list,
        description="Non-operational dates for every warehouse",
    )
    closed_dates_by_warehouse: dict[int, list[date]] = Field(
        default_factory=dict,
        description="Extra non-operational dates, keyed by warehouses.id",
    )


class SeriesRequest(_Schema):
    series_id: int = Field(description="product_warehouses.id")
    product_id: int = Field(description="products.id")
    warehouse_id: int = Field(description="warehouses.id")
    metadata: dict[str, Any] = Field(default_factory=dict)
    demand: list[DemandRow] = Field(default_factory=list)
    stock: list[StockRow] = Field(default_factory=list)

    @field_validator("metadata")
    @classmethod
    def refuse_a_made_to_order_flag(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        """Keep metadata free-form, except for the one key that would mislead.

        The service derives stock handling from the series' own history
        (notebook S24), so a `made_to_order` flag could only ever be ignored.
        Ignoring it silently would let Pango Core believe it decided something.
        Unknown fields are refused everywhere else in the request for the same
        reason; this closes the one place where that rule did not reach.
        """
        if REJECTED_METADATA_KEY in metadata:
            raise ValueError(
                f"metadata.{REJECTED_METADATA_KEY} is not accepted: the service "
                "derives stock handling from the series' own stock history"
            )

        return metadata


class ForecastRequest(_Schema):
    contract_version: Literal["1.0", "1.1", "1.2"] = Field(
        description=(
            "Decides the envelope returned: 1.2 adds the no_forecast status, "
            "while 1.0 and 1.1 are answered with the 1.1 envelope"
        )
    )
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
    `    upper_bound` are NOT NULL in the tenant schema. These values come from the
    selected model's empirical residual interval.
    """

    period_start: date
    estimate: float
    lower: float
    upper: float


class TotalResponse(_Schema):
    estimate: float
    lower: float
    upper: float


class EvaluationPeriodRequest(_Schema):
    period_start: date
    estimate: float
    lower: float
    upper: float
    actual: float
    observable: bool


class EvaluationTotalRequest(_Schema):
    estimate: float
    lower: float
    upper: float


class EvaluationSeriesRequest(_Schema):
    series_id: int
    periods: Annotated[list[EvaluationPeriodRequest], Field(min_length=1)]
    training_values: list[float] = Field(default_factory=list)
    total: EvaluationTotalRequest | None = None


class ForecastEvaluationRequest(_Schema):
    contract_version: Literal["1.0", "1.1", "1.2"]
    request_id: str = Field(min_length=1)
    forecast_run_id: int
    frequency: Literal["WEEKLY"]
    horizon: Annotated[int, Field(ge=1)]
    series: Annotated[list[EvaluationSeriesRequest], Field(min_length=1)]


class MetricsResponse(_Schema):
    evaluated_periods: int
    actual_total: float
    estimate_total: float
    error_total: float
    wape: float | None
    bias: float | None
    mae: float | None
    mase: float | None
    interval_coverage: float | None
    total_inside_interval: bool | None


class SeriesEvaluationResponse(_Schema):
    series_id: int
    metrics: MetricsResponse


class ForecastEvaluationResponse(_Schema):
    contract_version: str
    service_version: str
    request_id: str
    forecast_run_id: int
    results: list[SeriesEvaluationResponse]
    aggregate: MetricsResponse


class SeriesErrorResponse(_Schema):
    code: str
    message: str


class ProfileResponse(_Schema):
    """What the series' demand behaves like, not which model will handle it.

    Returned even when the series cannot be forecast, because it is the answer
    to "why" - and for an inactive or unclassifiable series it is the whole of
    what the service has to say.
    """

    demand_class: str
    history_class: str
    stock_handling: str
    adi: float | None = Field(
        description="Observable months per month with a sale; null if none sold"
    )
    cv2: float | None = Field(
        description="Squared CV of the non-zero monthly demands; null below two"
    )
    observable_months: int
    nonzero_months: int
    history_weeks: int
    observed_weeks: int
    weeks_since_last_sale: int
    inactive: bool
    evaluable: bool


class SeriesResultResponse(_Schema):
    series_id: int
    status: Literal["ok", "error", "no_forecast"] = Field(
        description="no_forecast is only ever sent to a client that declares 1.2"
    )
    reason: str | None = Field(
        default=None,
        description="Why the policy declined the series; set with no_forecast",
    )
    periods: list[PeriodResponse] = Field(default_factory=list)
    total: TotalResponse | None = None
    model_name: str | None = None
    model_version: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: SeriesErrorResponse | None = None
    profile: ProfileResponse | None = None
    horizon_business_days: list[float] = Field(
        default_factory=list,
        description="Equivalent business days of each week being forecast",
    )


class SummaryResponse(_Schema):
    total: int
    ok: int
    error: int
    no_forecast: int = 0


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
