# PANGO Forecast Service — Contract v1

**Status:** Implementation contract v1.2
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

### Backend integration checklist

Before calling the service, Pango Core must:

1. Select only finalized movements whose `movement_types.counts_as_demand` is
   true.
2. Apply reversals, invalidations and internal-movement rules before
   aggregating demand by `product_warehouse_id` and local calendar date.
3. Convert `movements.occurred_at` from its stored timezone using
   `settings.time_zone` (default `America/Bogota`) before assigning a date.
4. Reconstruct daily closing stock by replaying movements and movement lines;
   `stock_balances` is current state, not a historical series.
5. Send the last complete Sunday as `history_end`. A cutoff in the middle of a
   week drops that partial week.
6. Send every non-operational date known for the forecast history and horizon,
   using the tenant-wide and warehouse-specific calendar fields.
7. Split batches above `MAX_SERIES_PER_REQUEST` into independent requests.

The service does not infer business document semantics, holidays, warehouse
stock ownership or a made-to-order flag from the request metadata.

## 3. Versioning

Every request includes:

```json
{
  "contract_version": "1.2"
}
```

Backward-compatible additions may remain in `1.x`. Breaking changes require a new major version.

**The service serves `1.2` and accepts a request declaring `1.0`, `1.1` or `1.2`.** The version a request declares decides the envelope it is answered in:

| Request declares | Response declares | A series the policy declines |
|---|---|---|
| `1.2` | `1.2` | `status: "no_forecast"` with a `reason` |
| `1.0` or `1.1` | `1.1` | `status: "error"` with code `MODEL_NOT_AVAILABLE`, as before 1.2 |

Everything 1.1 added was an extra field, so a 1.0 client that ignores response fields it does not know kept working unchanged. A status is different: a client that knows `ok` and `error` may branch on exactly those two, so `no_forecast` is never sent to a client that did not ask for 1.2. Nothing else differs between the two envelopes. Both endpoints accept the three versions.

### What changed in 1.2

| Change | Where |
|---|---|
| `results[].status` gains `no_forecast`, for a series the policy declines on purpose | section 14 |
| `results[].reason` states why, from a closed vocabulary of five values | section 14 |
| `summary.no_forecast` counts those series apart from `error` | section 14 |
| A policy rule's `reason` must be one of those five values, and a rule cannot state both a `model` and a `reason` | section 15.1 |
| `warehouses[]` says which warehouses hold stock; a series in one that does not is declined as `non_stock_warehouse` | sections 6, 7 |
| `series[].history_only` marks a series sent only for its history; it is declined as `history_only` | section 8 |

The two request fields are optional and are accepted whatever version the request declares. Only the status they lead to follows the version, like every declined series.

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
- `metadata.made_to_order` does not exist and is **rejected** with `422`,
  both at series level and inside `metadata`. The service derives stock
  handling from the series' own history (notebook S24), so the flag could only
  be ignored, and ignoring it silently would hide a contract drift. Every other
  key of `metadata` stays free-form.
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
  "service": "pango-prediction",
  "service_version": "0.1.0"
}
```

### Forecast

```http
POST /v1/forecasts
Content-Type: application/json
```

### Forecast evaluation

```http
POST /v1/forecast-evaluations
Content-Type: application/json
```

## 6. Forecast request

```json
{
  "contract_version": "1.2",
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
  "warehouses": [
    { "warehouse_id": 3, "holds_stock": true }
  ],
  "series": [
    {
      "series_id": 1198,
      "product_id": 742,
      "warehouse_id": 3,
      "history_only": false,
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

- `contract_version`: required; accepted values are `1.0`, `1.1` and `1.2`.
  New integrations should send `1.2`, which is the only one answered with the
  `no_forecast` status (section 3).
- `request_id`: required unique tracing identifier.
- `forecast_run_id`: required Pango Core run identifier; opaque to Forecast Service.
- `frequency`: required; current supported value `WEEKLY`.
- `horizon`: required; current production value `4`. It must equal the horizon declared by the loaded model policy: intervals and totals are calibrated for that horizon, so any other value is refused with `400` for the whole request (section 17).
- `history_end`: required, format `YYYY-MM-DD`.
- `calendar.closed_dates`: optional array of non-operational dates for every
  warehouse.
- `calendar.closed_dates_by_warehouse`: optional additional non-operational
  dates keyed by `warehouses.id`.
- `warehouses`: optional array, one entry per warehouse, each with its
  `warehouse_id` and a boolean `holds_stock`. It is administrator-managed
  configuration that Pango Core stores and sends; the service cannot derive
  it, because a warehouse that ran out of stock and one that never holds any
  look the same in a series.
  - A series whose warehouse has `holds_stock: false` is not forecast: it
    comes back as `no_forecast` with the reason `non_stock_warehouse`.
  - A warehouse left out, or the whole block left out, is treated as holding
    stock, and each of its series says so in a warning.
  - The same `warehouse_id` twice is refused with `422`.
  - A warehouse with no series in the request is ignored, so the whole
    configuration can be sent with every batch.
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
aggregated forecast for it. Sending the key fails validation with `422`, so the
mismatch surfaces during integration instead of in production. It is the only
key `metadata` refuses.

### `history_only`

Optional boolean, `false` when absent.

`true` marks a series that is sent for its history and is never forecast or
replenished: for instance the sales of a warehouse that was later renamed or
split, which cannot be attributed to a warehouse that exists today. It comes
back as `no_forecast` with the reason `history_only`, so that every series
sent has a result.

The service does not yet use that history for any other series. It is part of
the contract now so that Pango Core builds the request once.

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

### Input granularity

`frequency` and `horizon` describe the forecast that comes back, not the data
that goes in. Demand is always sent as dated rows, one `date` and one
`quantity` each, inside the series they belong to. The request has no date
range and no weekly `from`/`to`: its only range marker is the top-level
`history_end`.

- Send one row per `product_warehouse_id` and local calendar date, with that
  day's total. Several rows with the same date are accepted and summed, so one
  row per movement also works; it only makes the payload larger.
- Omit the days without demand. The service fills the gaps with zero.
- Do not pre-aggregate to weeks. The service builds the Monday-Sunday weeks
  itself, and it classifies demand on calendar **months** computed from the
  same daily rows (section 14). A weekly total would put the whole demand of a
  week that straddles two months into one of them.
- Quantities are gross and non-negative. A negative quantity, or a row dated
  after `history_end`, fails that series with `INVALID_SERIES`.

Stock rows follow the same shape, a `date` and a `closing_stock` (section 10).

## 10. Stock observations

Example:

```json
{
  "date": "2026-10-05",
  "closing_stock": 12
}
```

For the MVP, daily closing stock is preferred.

Stock rows may be sparse: send a row on the days the reconstructed balance
changes rather than repeating the same balance for every calendar day. The
service carries the latest known balance forward within the weekly grid.

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

Captured from the running service, not written by hand. The development policy
routes series 1198 to the `naive` baseline; series 1500 sent a negative
quantity and could not be processed.

```json
{
  "contract_version": "1.2",
  "service_version": "0.1.0",
  "policy_version": "development-1",
  "request_id": "req-2026-11-02-0001",
  "forecast_run_id": 41,
  "summary": {
    "total": 2,
    "ok": 1,
    "error": 1,
    "no_forecast": 0
  },
  "results": [
    {
      "series_id": 1198,
      "status": "ok",
      "reason": null,
      "periods": [
        {
          "period_start": "2026-11-02",
          "estimate": 3.0,
          "lower": 0.0,
          "upper": 6.0
        },
        {
          "period_start": "2026-11-09",
          "estimate": 3.0,
          "lower": 0.0,
          "upper": 6.0
        },
        {
          "period_start": "2026-11-16",
          "estimate": 3.0,
          "lower": 0.0,
          "upper": 6.0
        },
        {
          "period_start": "2026-11-23",
          "estimate": 3.0,
          "lower": 0.0,
          "upper": 6.0
        }
      ],
      "total": {
        "estimate": 12.0,
        "lower": 4.800000000000001,
        "upper": 18.0
      },
      "model_name": "naive",
      "model_version": "development",
      "warnings": [
        "The series is sold to order, so zero stock was not read as a stockout."
      ],
      "error": null,
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
      "reason": null,
      "periods": [],
      "total": null,
      "model_name": null,
      "model_version": null,
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

`periods` contains one entry per future Monday-starting week. `total` is the
calibrated aggregate across the requested horizon. Its interval is not formed
by summing the weekly lower and upper bounds; each model must provide the
horizon-total interval explicitly. `total` is `null` for a series that did not
produce a forecast.

### The three statuses

| `status` | Meaning | What accompanies it |
|---|---|---|
| `ok` | The series was forecast | `periods`, `total`, `model_name`, `model_version` |
| `no_forecast` | The policy declined the series on purpose. Nothing failed | `reason`; `periods` is empty and `total` is `null` |
| `error` | Something prevented a forecast | `error.code` and `error.message` |

`profile`, `warnings` and `horizon_business_days` travel with all three whenever the series got far enough.

`reason` is one of:

| `reason` | Meaning |
|---|---|
| `inactive` | The series has not sold within the tenant's inactivity window |
| `sold_to_order` | Reserved for a later policy. Policy v1 has no rule that uses it |
| `non_stock_warehouse` | The warehouse holds no stock, so its demand must not drive replenishment |
| `no_active_warehouse` | The product has no active warehouse to receive a forecast |
| `history_only` | The series is sent for its history and is not itself forecast |

Who states each reason:

| `reason` | Stated by |
|---|---|
| `inactive` | A policy rule |
| `sold_to_order` | A policy rule. Policy v1 has none |
| `non_stock_warehouse` | The request: `warehouses[]` with `holds_stock: false` |
| `history_only` | The request: `history_only: true` on the series |
| `no_active_warehouse` | Nothing yet. It belongs to the product-to-warehouse allocation, which is not connected to the service |

The request is heard before the policy. What an administrator configured stays true however the demand of the series changes, so it needs no policy loaded and is not overridden by one; `history_only` is checked before the warehouse. Declining a series does not excuse its data: invalid input still fails it with `INVALID_SERIES`.

A series the policy declines, captured from the service for a request declaring `1.2`:

```json
{
  "series_id": 1042,
  "status": "no_forecast",
  "reason": "inactive",
  "periods": [],
  "total": null,
  "warnings": [
    "Stock handling could not be classified, so stockouts were not evaluated for this series.",
    "The series has not sold for 94 weeks and is read as inactive.",
    "The series sold in too few months for its demand pattern to be measured."
  ],
  "error": null
}
```

with `"summary": {"total": 1, "ok": 0, "error": 0, "no_forecast": 1}`. The same series for a request declaring `1.1`:

```json
{
  "series_id": 1042,
  "status": "error",
  "reason": null,
  "periods": [],
  "total": null,
  "warnings": [
    "Stock handling could not be classified, so stockouts were not evaluated for this series.",
    "The series has not sold for 94 weeks and is read as inactive.",
    "The series sold in too few months for its demand pattern to be measured."
  ],
  "error": {
    "code": "MODEL_NOT_AVAILABLE",
    "message": "This series is not forecast: inactive."
  }
}
```

with `"summary": {"total": 1, "ok": 0, "error": 1, "no_forecast": 0}`. `reason` and `summary.no_forecast` are present in both envelopes; in the older one they are always `null` and `0`.

A series in a warehouse that holds no stock, for a request declaring `1.2`:

```json
{
  "series_id": 1042,
  "status": "no_forecast",
  "reason": "non_stock_warehouse",
  "periods": [],
  "total": null,
  "warnings": [],
  "error": null
}
```

History insufficiency is **not** a `no_forecast`. A series with too little observable history still comes back as an `error`, and stays one until a policy rule routes it with a reason of its own.

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

With the development policy, successful series include four `periods`,
`model_name`, `model_version` and `policy_version`. Route A can replace the
development artifact without changing the HTTP envelope. Series that are not
evaluable or have no applicable policy continue to return an empty `periods`
array and a stable per-series error. Series the policy declines return
`no_forecast` to a 1.2 client, as described above.

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
  "params_version": "f5f416da285217b5",
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

A rule states either a `model`, which forecasts the series, or a `reason`,
which declines it:

```json
{ "when": { "inactive": true }, "reason": "inactive" }
```

`reason` must be one of the five values of section 14; the loader rejects any
other, and rejects a rule that states both a `model` and a `reason`. A rule
with neither is accepted and means the policy has nothing for the series:
that is reported as the error `MODEL_NOT_AVAILABLE`, not as `no_forecast`.
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

Pango Core must persist the result for every input series, including failures.
For a successful series, persist the periods, total, model metadata, warnings
and profile available in the response. For a failed series, persist the stable
error code and message without discarding successful series from the same
request. For a declined series, persist the `no_forecast` status and its
`reason`: it is a decision to keep, not a failure to retry.

## 17. HTTP status codes

- `200 OK`: request processed; may contain partial series failures.
- `400 Bad Request`: malformed request; also a batch over the series limit, and a `horizon` other than the one the loaded policy is calibrated for.
- `401 Unauthorized`: invalid or missing service credential.
- `403 Forbidden`: credential valid but unauthorized.
- `422 Unprocessable Entity`: semantically invalid request.
- `500 Internal Server Error`: unexpected failure.
- `503 Service Unavailable`: required runtime/model unavailable.

## 18. Idempotency and traceability

`request_id` and `forecast_run_id` must be traceable.

Pango Core owns persistence-level idempotency.

Forecast Service never writes business forecast results directly to PostgreSQL.

For retries, send the same `request_id`, `forecast_run_id` and request payload.
Do not reuse a request identifier for a different batch. A split batch gives
each request its own identifier, while a retry of one split uses that same
identifier.

Pango Core should persist `policy_version` from the response envelope and
`model_name`/`model_version` from each successful series. These values are
required for later auditability even though the current tenant schema may need
new columns for them.

## 19. Forecast evaluation

After the forecast horizon has elapsed, Pango Core sends the stored forecast
and the realised demand to:

```http
POST /v1/forecast-evaluations
```

The endpoint uses the same service credential as `/v1/forecasts`. Each period
contains `estimate`, `lower`, `upper`, `actual` and a required explicit
`observable` flag. Non-observable periods are excluded from all metrics.
`observable` must come from the availability snapshot used for the forecast;
Core must not infer it from `actual == 0`. A censored or out-of-assortment
period is sent as `observable: false`.

`training_values` contains the historical weekly demand values used for MASE,
in chronological order and with the same observable-demand semantics. It may
be omitted when that history is unavailable; in that case MASE is `null`. The
service uses a four-week seasonal-naive scale.

The request is:

```json
{
  "contract_version": "1.1",
  "request_id": "evaluation-2026-11-30-41",
  "forecast_run_id": 41,
  "frequency": "WEEKLY",
  "horizon": 2,
  "series": [
    {
      "series_id": 1198,
      "periods": [
        {
          "period_start": "2026-11-02",
          "estimate": 3.0,
          "lower": 2.0,
          "upper": 5.0,
          "actual": 5.0,
          "observable": true
        },
        {
          "period_start": "2026-11-09",
          "estimate": 4.0,
          "lower": 3.0,
          "upper": 5.0,
          "actual": 3.0,
          "observable": false
        }
      ],
      "training_values": [1.0, 2.0, 3.0, 4.0, 2.0],
      "total": {
        "estimate": 7.0,
        "lower": 4.0,
        "upper": 9.0
      }
    }
  ]
}
```

Core should send one evaluation request only after every period in the run
has closed. The `series_id` and `forecast_run_id` must refer to persisted
records in Core. `period_start` is Monday, and `periods` must be sent in
chronological order. `actual` is gross weekly demand and cannot be negative.
`total` is the original four-week forecast total interval; it is omitted only
when the forecast did not produce one.

The response is:

```json
{
  "contract_version": "1.1",
  "service_version": "0.1.0",
  "request_id": "evaluation-2026-11-30-41",
  "forecast_run_id": 41,
  "results": [
    {
      "series_id": 1198,
      "metrics": {
        "evaluated_periods": 1,
        "actual_total": 5.0,
        "estimate_total": 3.0,
        "error_total": 2.0,
        "wape": 0.4,
        "bias": 0.4,
        "mae": 2.0,
        "mase": 2.0,
        "interval_coverage": 1.0,
        "total_inside_interval": true
      }
    }
  ],
  "aggregate": {
    "evaluated_periods": 1,
    "actual_total": 5.0,
    "estimate_total": 3.0,
    "error_total": 2.0,
    "wape": 0.4,
    "bias": 0.4,
    "mae": 2.0,
    "mase": null,
    "interval_coverage": 1.0,
    "total_inside_interval": true
  }
}
```

Core persists the response in a child table keyed by
`(forecast_run_id, series_id)`. Repeated requests with the same
`forecast_run_id` must be idempotent: Core may upsert the metrics, but must
not create a second official evaluation.

The response contains metrics per series and an `aggregate` across all
observable periods. The official metrics are:

```text
error = actual - estimate
WAPE = sum(abs(error)) / sum(actual)
Bias = sum(error) / sum(actual)
MAE = mean(abs(error))
MASE = MAE / seasonal-naive scale
interval_coverage = periods inside [lower, upper] / evaluated periods
```

When the observable actual total is zero, WAPE and Bias are `null`. When MASE
does not have enough history or has a zero scale, it is `null`. Pango Core
persists the request facts and the returned metrics; Prediction calculates the
official values.

## 20. Batching

The service should enforce a configurable:

```text
MAX_SERIES_PER_REQUEST
```

If needed, Pango Core may split large jobs into multiple calls while keeping the same contract.

Each request must contain at least one series and no more than the configured
limit. A request over the limit receives `400`; series-level validation or
model failures are returned inside a `200` response.

## 21. Logging

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

## 22. Compatibility rule

Changes to weekly semantics, availability rules, profiling, request fields or output semantics must be reflected in:

```text
contract version
domain tests
benchmark
production service
```

Benchmark and production must use the same domain implementation.

### Backend delivery checklist

The Backend integration is ready for implementation when it can demonstrate:

- authenticated calls to `/v1/forecasts`;
- demand filtered through `counts_as_demand`;
- reversal and final-status handling;
- timezone-correct daily aggregation;
- reconstructed daily stock history;
- Sunday `history_end`;
- tenant-wide and warehouse-specific closures;
- persistence of successful forecasts, totals, profiles, warnings and errors;
- idempotent retries using `request_id`;
- a partial-run state when some series fail;
- `contract_version` `1.2` declared, and `no_forecast` results stored as
  decisions and not counted or retried as failures;
- the `warehouses[]` block sent with every request, from administrator-managed
  configuration, and historical series marked `history_only`;
- calls to `/v1/forecast-evaluations` after all forecast periods close.
