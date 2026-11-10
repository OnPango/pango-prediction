# PANGO Forecast Service — Contract v1

**Status:** Draft v1  
**Communication:** HTTP + JSON  
**Caller:** Pango Core (NestJS)  
**Provider:** Pango Forecast Service (Python / FastAPI)

## 1. Purpose

This contract defines how Pango Core sends normalized demand and stock data to the Forecast Service and how the Forecast Service returns forecasts.

Production forecasting grain:

```text
product × warehouse
```

Therefore:

```text
series_id = product_warehouse_id
```

Pango Core owns database access, tenancy, authentication, business semantics and persistence. Forecast Service owns temporal aggregation, availability reconstruction, profiling, policy resolution and forecasting.

## 2. Service boundary

### Pango Core responsibilities

- Resolve tenant.
- Read Supabase PostgreSQL.
- Resolve `product_id`, `warehouse_id` and `product_warehouse_id`.
- Determine which business movements count as demand.
- Handle reversals and invalidated movements.
- Exclude internal movements.
- Integrate external historical imports.
- Send normalized daily demand.
- Send stock observations.
- Send calendar closures.
- Create and persist `forecast_run`.
- Persist `forecasts` and `forecast_periods`.

### Forecast Service responsibilities

- Validate the request.
- Convert daily observations into weekly series.
- Use Monday as week start.
- Fill missing periods.
- Reconstruct availability.
- Compute `available_mask`.
- Compute `dias_habiles`.
- Compute ADI/CV².
- Classify demand.
- Resolve a versioned model policy.
- Execute StatsForecast or DeepAR.
- Return forecast and model metadata.

Forecast Service does **not** connect directly to Supabase PostgreSQL.

## 3. Versioning

Every request includes:

```json
{
  "contract_version": "1.1"
}
```

Backward-compatible additions may remain in `1.x`. Breaking changes require a new major version.

**The service serves `1.1` and accepts a request declaring `1.0` or `1.1`.** Everything 1.1 added is additive, so a 1.0 client that ignores response fields it does not know keeps working unchanged.

### What changed in 1.1

| Change | Where |
|---|---|
| `results[].profile` is returned for every series that got far enough, including one that cannot be forecast | section 14 |
| `results[].horizon_business_days` reports the calendar covariate per forecast week | sections 13, 14 |
| `calendar.closed_dates_by_warehouse` accepts closures for one warehouse | section 13 |

### Corrections to the 1.0 text

These were already wrong rather than newly changed, and are recorded as discrepancies in `docs/domain-rules.md` section 14:

- identifiers are **integers**, not strings: `product_warehouses.id`, `products.id`, `warehouses.id` and `forecast_runs.id` are all integers in the tenant schema;
- `frequency` is `"WEEKLY"`, the schema's enum casing;
- `metadata.made_to_order` does not exist and is **rejected**. No such column exists, and the service derives stock handling from the series' own history (notebook S24). Sending it fails validation, because unknown fields are refused rather than ignored - a contract drift should surface, not pass silently;
- the `profile` block's field names changed, because 1.0 conflated the two grains it is computed on. See section 14.

## 4. Authentication

Recommended header:

```http
Authorization: Bearer <service-token>
```

Do not reuse end-user JWTs.

## 5. Endpoints

### Health

```http
GET /health
```

Example response:

```json
{
  "status": "ok",
  "service": "pango-forecast-service",
  "service_version": "0.1.0"
}
```

### Forecast

```http
POST /v1/forecasts
Content-Type: application/json
```

## 6. Forecast request

```json
{
  "contract_version": "1.1",
  "request_id": "req-123",
  "forecast_run_id": 41,
  "frequency": "WEEKLY",
  "horizon": 4,
  "history_end": "2026-11-01",
  "calendar": {
    "closed_dates": ["2026-11-16"],
    "closed_dates_by_warehouse": {
      "3": ["2026-11-02"]
    }
  },
  "series": [
    {
      "series_id": 1198,
      "product_id": 742,
      "warehouse_id": 3,
      "metadata": {
        "group2": "REPUESTOS"
      },
      "demand": [
        {
          "date": "2026-10-05",
          "quantity": 3
        }
      ],
      "stock": [
        {
          "date": "2026-10-05",
          "closing_stock": 12
        }
      ]
    }
  ]
}
```

## 7. Top-level fields

- `contract_version`: required; accepted values are `1.0` and `1.1`. New
  integrations should send `1.1`.
- `request_id`: required unique tracing identifier.
- `forecast_run_id`: required Pango Core run identifier; opaque to Forecast Service.
- `frequency`: required; current supported value `weekly`.
- `horizon`: required; current production value `4`.
- `history_end`: required, format `YYYY-MM-DD`.
- `calendar.closed_dates`: optional array of non-operational dates.
- `series`: required array of product-warehouse series.

No observation after `history_end` may be used.

## 8. Series fields

### `series_id`

Required. An integer: `product_warehouses.id`.

```text
series_id = product_warehouse_id
```

Forecast Service treats it as opaque. The domain keeps it a string so the same
code serves either forecast grain; the conversion happens at the HTTP edge.

### `product_id`

Required for traceability. An integer: `products.id`.

### `warehouse_id`

Required. An integer: `warehouses.id`.

Not only for traceability any more: it selects the warehouse's closed dates,
so a wrong value gives the series the wrong calendar.

### `metadata`

Optional, and free-form.

```json
{
  "group2": "REPUESTOS"
}
```

**No `made_to_order`.** There is no such column, and a flag would compete with
what the service derives from the series' own stock history (notebook S24): the
notebook finds 332 series sold to order, while only 13 are routed to an
aggregated forecast for it. Sending the field fails validation rather than being
ignored, so the mismatch surfaces during integration instead of in production.

## 9. Demand observations

Example:

```json
{
  "date": "2026-10-05",
  "quantity": 3
}
```

Pango Core must already apply business semantics.

Current EquipMaster rules:

```text
FV  → demand
DMC → not subtracted from primary demand target
REM → not added to avoid double counting
SA  → excluded
```

Forecast Service must not know World Office document types.

Pango Core may aggregate valid demand by:

```text
product_warehouse_id + date
```

Forecast Service performs weekly aggregation.

## 10. Stock observations

Example:

```json
{
  "date": "2026-10-05",
  "closing_stock": 12
}
```

For the MVP, daily closing stock is preferred.

## 11. Availability rules

Forecast Service derives:

```text
AVAILABLE
CENSORED
UNKNOWN
SIN_SURTIDO
```

Rules:

1. No sales + final stock = 0 → `CENSORED`.
2. Sales occurred + final stock = 0 → `AVAILABLE`.
3. Made-to-order products use their special rule.
4. More than 13 consecutive weeks without stock → `SIN_SURTIDO`.

Forecast Service derives `available_mask` internally.

## 12. Weekly aggregation

Week definition:

```text
Monday → Sunday
```

The period is labeled by the Monday at the start of the week.

Do not rely on ambiguous default `W-MON` behavior.

## 13. Business-day feature

Equivalent business days per week, returned as `results[].horizon_business_days`, one value per week being forecast.

The **weight rule** is shared domain code, the same in the benchmark and in production:

```text
weekday weight  = from the tenant's table
closed date     = 0.0
week            = the sum of its seven days
```

The default table is EquipMaster's, and a full week is its sum:

```text
Monday-Friday = 1.0
Saturday      = 0.6
Sunday        = 0.0
full week     = 5.6
```

The whole table is tenant calibration, not a Saturday weight with the rest fixed: a tenant closed on Saturdays needs 0, and one that trades on Sundays needs a different table.

**Which dates are closed is data, and Pango Core sends it:**

```json
"calendar": {
  "closed_dates": ["2026-11-16"],
  "closed_dates_by_warehouse": { "3": ["2026-11-02"] }
}
```

`closed_dates` applies to every warehouse; `closed_dates_by_warehouse` adds to it for one. They resolve as a union, so a warehouse cannot re-open a day the tenant declared closed.

Core must send every non-operational date, not only public holidays: the company shutdown around the year end, Holy Saturday (not an official Colombian holiday, yet the company does not invoice), inventory counts, and any local closure. **The service computes no holidays and takes no country.** A country code would cover the public holidays and miss all of the above, and it would make parity depend on both routes pinning the same holiday package rather than on reading the same list. The reasoning is in `docs/domain-rules.md` section 7.

The feature is known into the future by construction, since it reads only the dates and the tenant's table, which is what lets the horizon carry the same covariate as the history.

## 14. Forecast response

Captured from the running service, not written by hand. Series 1198 is sold to order and could be profiled; series 1500 sent a negative quantity and could not.

```json
{
  "contract_version": "1.1",
  "service_version": "0.1.0",
  "policy_version": null,
  "request_id": "req-2026-11-02-0001",
  "forecast_run_id": 41,
  "summary": {
    "total": 2,
    "ok": 0,
    "error": 2
  },
  "results": [
    {
      "series_id": 1198,
      "status": "error",
      "periods": [],
      "warnings": [
        "The series is sold to order, so zero stock was not read as a stockout."
      ],
      "error": {
        "code": "MODEL_NOT_AVAILABLE",
        "message": "No forecasting model is deployed yet: the model policy and the model implementations are not part of this release."
      },
      "profile": {
        "demand_class": "SMOOTH",
        "history_class": "LIMITED",
        "stock_handling": "MADE_TO_ORDER",
        "adi": 1.0,
        "cv2": 0.13999999999999999,
        "observable_months": 5,
        "nonzero_months": 5,
        "history_weeks": 22,
        "observed_weeks": 22,
        "weeks_since_last_sale": 0,
        "inactive": false,
        "evaluable": true
      },
      "horizon_business_days": [
        4.6,
        5.6,
        4.6,
        5.6
      ]
    },
    {
      "series_id": 1500,
      "status": "error",
      "periods": [],
      "warnings": [],
      "error": {
        "code": "INVALID_SERIES",
        "message": "Demand quantity cannot be negative"
      },
      "profile": null,
      "horizon_business_days": []
    }
  ]
}
```

`profile` is returned whenever the series got far enough to be profiled, **including when it cannot be forecast**: for an inactive or unclassifiable series it is the whole of what the service has to say. It is `null` only when the series failed before that, as 1500 did.

### The profile block

| Field | Meaning |
|---|---|
| `demand_class` | `SMOOTH`, `ERRATIC`, `INTERMITTENT`, `LUMPY`, `INSUFFICIENT` or `NOT_CLASSIFIABLE`. Computed on the **monthly** series (notebook S27) |
| `history_class` | `NEW`, `LIMITED` or `MATURE`, from the length of the **weekly** series |
| `stock_handling` | `STOCK_MANAGED`, `MADE_TO_ORDER` or `NOT_CLASSIFIABLE`, derived from the series' own stock history (S24) |
| `adi` | Observable months per month with a sale. `null` when nothing sold |
| `cv2` | Squared coefficient of variation of the non-zero monthly demands. `null` below two such months, which is why `INSUFFICIENT` exists |
| `observable_months`, `nonzero_months` | The classification grid, after dropping months spent in a stockout |
| `history_weeks`, `observed_weeks` | The weekly series, and how much of it can be read as demand |
| `weeks_since_last_sale`, `inactive` | Activity (S29) |
| `evaluable` | Whether there are enough observable weeks to forecast the series on its own (E1) |

The monthly and the weekly counts disagree on purpose: classification happens monthly and forecasting happens weekly, because at weekly grain 91.9 % of periods are zero and almost nothing looks like stable demand.

**This replaces 1.0's profile block**, which had `observed_periods`, `nonzero_periods` and `short_history`. Those conflated the two grains into one pair of counters and reduced three history classes to a boolean. The `demand_class` values are also upper case, matching Route A's enum.

### Not yet populated

`periods` is always empty and `policy_version` is always `null`, because no model policy is deployed. The envelope carries both so that Core can integrate against the final shape. A `model` block does not appear at all yet, for the same reason.

## 15. DeepAR metadata

When DeepAR is selected:

```json
{
  "model": {
    "name": "deepar",
    "implementation": "neuralforecast",
    "library_version": "...",
    "model_version": "deepar-equipmaster-v3",
    "model_ref": "deepar-equipmaster-v3",
    "params": {
      "input_size": 26,
      "scaler_type": "robust"
    }
  }
}
```

A checkpoint trained for one organization must not automatically be applied to another.

## 15.1 Development policy artifact

Until Route A publishes the evaluated production policy, Route B may load a
development policy with this shape:

```json
{
  "policy_version": "development-1",
  "frequency": "WEEKLY",
  "horizon": 4,
  "params_version": "bd4371bcce546b17",
  "rules": [
    {
      "when": { "demand_class": "SMOOTH" },
      "model": "naive"
    }
  ],
  "default": {
    "model": "naive"
  }
}
```

Rules are evaluated in declaration order. A rule may match
`demand_class`, `history_class`, `stock_handling`, `inactive` or `evaluable`.
The policy loader rejects an unknown model, an incompatible frequency or
horizon, and a `params_version` that does not match the domain calibration.
This policy is for integration testing only; it is not Route A's final model
selection decision.

## 16. Per-series failures

A single invalid series must not fail the entire batch.

Example:

```json
{
  "series_id": "pw-invalid",
  "status": "error",
  "error": {
    "code": "INSUFFICIENT_HISTORY",
    "message": "The series does not contain enough observable history."
  }
}
```

Initial stable error codes:

```text
INVALID_SERIES
INSUFFICIENT_HISTORY
UNKNOWN_AVAILABILITY
MODEL_FAILED
MODEL_NOT_AVAILABLE
CHECKPOINT_NOT_AVAILABLE
UNSUPPORTED_DATA
```

## 17. HTTP status codes

- `200 OK`: request processed; may contain partial series failures.
- `400 Bad Request`: malformed request.
- `401 Unauthorized`: invalid or missing service credential.
- `403 Forbidden`: credential valid but unauthorized.
- `422 Unprocessable Entity`: semantically invalid request.
- `500 Internal Server Error`: unexpected failure.
- `503 Service Unavailable`: required runtime/model unavailable.

## 18. Idempotency and traceability

`request_id` and `forecast_run_id` must be traceable.

Pango Core owns persistence-level idempotency.

Forecast Service never writes business forecast results directly to PostgreSQL.

## 19. Batching

The service should enforce a configurable:

```text
MAX_SERIES_PER_REQUEST
```

If needed, Pango Core may split large jobs into multiple calls while keeping the same contract.

## 20. Logging

Log at least:

```text
request_id
forecast_run_id
series_count
policy_version
model_name
model_version
duration_ms
status
```

Do not log full historical payloads by default.

## 21. Compatibility rule

Changes to weekly semantics, availability rules, profiling, request fields or output semantics must be reflected in:

```text
contract version
domain tests
benchmark
production service
```

Benchmark and production must use the same domain implementation.
