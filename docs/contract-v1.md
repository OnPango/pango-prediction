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
  "contract_version": "1.0"
}
```

Backward-compatible additions may remain in `1.x`. Breaking changes require a new major version.

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
  "contract_version": "1.0",
  "request_id": "req-123",
  "forecast_run_id": "run-123",
  "frequency": "weekly",
  "horizon": 4,
  "history_end": "2026-11-02",
  "calendar": {
    "closed_dates": []
  },
  "series": [
    {
      "series_id": "pw-7a8b",
      "product_id": "prod-123",
      "warehouse_id": "wh-baq",
      "metadata": {
        "group2": "REPUESTOS",
        "made_to_order": false
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

- `contract_version`: required, current value `1.0`.
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

Required.

```text
series_id = product_warehouse_id
```

Forecast Service treats it as opaque.

### `product_id`

Required for traceability.

### `warehouse_id`

Required for traceability.

### `metadata`

Optional.

Initial supported fields:

```json
{
  "group2": "...",
  "made_to_order": false
}
```

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

Current convention:

```text
Monday–Friday = 1.0
Saturday      = 0.6
Sunday        = 0.0
closed date   = 0.0
```

## 14. Forecast response

```json
{
  "contract_version": "1.0",
  "service_version": "0.1.0",
  "policy_version": "2026-11-v1",
  "request_id": "req-123",
  "forecast_run_id": "run-123",
  "summary": {
    "total": 1,
    "ok": 1,
    "error": 0
  },
  "results": [
    {
      "series_id": "pw-7a8b",
      "status": "ok",
      "profile": {
        "demand_class": "intermittent",
        "adi": 2.8,
        "cv2": 0.62,
        "observed_periods": 52,
        "nonzero_periods": 19,
        "short_history": false,
        "inactive": false
      },
      "model": {
        "name": "tsb",
        "implementation": "statsforecast",
        "library_version": "2.1.1",
        "model_version": "tsb-default-v1",
        "params": {}
      },
      "periods": [
        {
          "period_start": "2026-11-09",
          "estimate": 2.4,
          "lower": 0.0,
          "upper": 5.1
        }
      ],
      "warnings": []
    }
  ]
}
```

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
