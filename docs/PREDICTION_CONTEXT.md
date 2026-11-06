# PANGO Prediction — Engineering Context

## Purpose

PANGO contains a prediction module for forecasting inventory demand at product-warehouse level.

The production prediction implementation lives in:

```text
pango-prediction
```

It is a Python service separated from Pango Core.

Pango Core is responsible for business-system integration and persistence.

PANGO Prediction is responsible for forecasting.

---

## Two-route strategy

Development is intentionally divided into two parallel routes.

### Route A — Model evaluation

Purpose:

Determine which forecasting models perform best for the real PANGO demand problem.

Route A produces:

```text
model_policy.json
```

and, when applicable:

```text
DeepAR checkpoint
```

Route A performs:

- dataset construction;
- EDA;
- backtesting;
- demand profiling;
- baseline evaluation;
- StatsForecast evaluation;
- DeepAR evaluation;
- model comparison;
- candidate freezing;
- final reporting.

### Route B — Production implementation

This repository implements Route B.

Purpose:

Build a deployable Python forecasting service that receives normalized demand, stock and metadata from Pango Core and returns a four-week forecast.

The service must not query the PANGO PostgreSQL database directly.

---

## Integration invariant

Both routes must share the same forecasting semantics.

The intention is:

```text
same series
+
same availability
+
same calendar
+
same demand profile
+
same model implementation
=
evaluated behavior == production behavior
```

Route A should import the production domain code rather than reproduce those transformations inside notebooks.

---

## Forecast level

Forecasts are produced at:

```text
product × warehouse
```

The canonical identifier is:

```text
series_id = product_warehouse_id
```

Frequency:

```text
weekly
```

Forecast horizon:

```text
4 weeks
```

Week definition:

```text
Monday through Sunday
```

---

## Domain pipeline

The production forecasting flow is:

```text
input
↓
series
↓
availability
↓
calendar
↓
profile
↓
policy
↓
model
↓
forecast result
```

### Series

The series component receives daily demand and:

- validates observations;
- aggregates duplicate daily observations;
- maps dates to Monday-starting weeks;
- aggregates demand weekly;
- fills missing weeks;
- produces a regular weekly series.

### Availability

Availability uses stock history to distinguish observable demand from potentially stock-censored demand.

States:

```text
AVAILABLE
CENSORED
UNKNOWN
```

Known semantics:

```text
week with zero sales + zero closing stock
→ CENSORED

week with positive sales + zero closing stock
→ observed / AVAILABLE

insufficient reliable stock information
→ UNKNOWN
```

More than 13 consecutive weeks without stock must be detected as an out-of-assortment condition.

The exact treatment for made-to-order products remains a domain rule that must not be invented if it has not yet been formally defined.

### Calendar

Initial operational calendar rules:

```text
Monday-Friday = 1.0
Saturday      = 0.6
Sunday        = 0.0
holiday       = 0.0
closure       = 0.0
```

The calendar must also support generation for the four-week future forecast horizon.

### Demand profile

The demand profile calculates at least:

```text
observed_periods
nonzero_periods
ADI
CV²
inactive
short_history
demand class
```

Demand profiling describes the series.

It does not select the production model.

---

## Policy

Model selection is externalized through a versioned policy.

Conceptually:

```text
DemandProfile
     ↓
model_policy.json
     ↓
ModelSpec
```

The final policy comes from Route A.

Route B may use a development policy before the final evaluation is available.

Policy metadata should include:

```text
policy_version
forecast_level
frequency
horizon
model mapping
parameters
model library
library version
```

For DeepAR it additionally references model artifacts/checkpoint metadata.

---

## Planned models

Initial baselines:

```text
zero
naive
moving_average_8
seasonal_naive
```

Statistical model candidates:

```text
AutoETS
CrostonSBA
TSB
ADIDA
IMAPA
AutoARIMA
```

These should use StatsForecast rather than custom reimplementations.

DeepAR is a global neural forecasting candidate.

Do not train one DeepAR model per individual series.

---

## DeepAR architecture

Training and inference are separate concerns.

### Training

Offline batch operation:

```text
training dataset
↓
trainer
↓
checkpoint
↓
metadata
↓
SHA-256
↓
private artifact storage
```

### Runtime

Production inference:

```text
model_ref
↓
checkpoint loader/cache
↓
DeepAR runtime
↓
forecast
```

The HTTP forecast endpoint must never train DeepAR.

A model trained for one tenant must not automatically be assumed valid for another tenant.

---

## System boundaries

### Pango Core owns

- tenant resolution;
- demand retrieval;
- stock retrieval;
- `product_warehouse_id` resolution;
- forecast-run creation;
- HTTP call to PANGO Prediction;
- result persistence;
- frontend-facing execution status.

### PANGO Prediction owns

- time-series regularization;
- availability analysis;
- operational calendar features;
- demand profiling;
- policy loading;
- model execution;
- forecast result generation.

### Frontend

The frontend should communicate with Pango Core rather than directly with the prediction service.

---

## Target API

Planned endpoints:

```text
GET /health
POST /v1/forecasts
```

FastAPI must remain an inbound adapter.

Forecasting logic does not belong inside API routes.

---

## Batch behavior

Forecast requests may contain multiple series.

Partial failure is allowed.

Example:

```text
100 requested
99 OK
1 INVALID_INPUT
```

Individual series should have stable status/error information.

HTTP errors should represent failures affecting the overall request rather than a single invalid series whenever practical.

---

## Architecture

The repository uses pragmatic Hexagonal Architecture.

```text
Pango Core
    │
    ▼
Inbound Adapter
    │
    ▼
Application
    │
    ▼
Domain
    │
    ▼
Ports
    │
    ▼
Outbound Adapters
```

Technology-specific dependencies stay at the edges.

---

## Testing strategy

### Unit tests

Cover domain behavior:

```text
types
series
availability
calendar
profile
policy
model registry
models
```

### Integration tests

Validate:

```text
request-like input
↓
ForecastService
↓
result
```

### API tests

Eventually cover:

- health;
- valid forecast;
- invalid input;
- partial success;
- unauthorized service request.

### Golden tests

A private golden dataset/test should compare series construction used by experimentation with production behavior.

The goal is to detect training-serving skew.

---

## Current engineering philosophy

Build complexity incrementally.

Start with a correct, tested domain.

Then add:

```text
model abstractions
↓
application orchestration
↓
HTTP
↓
statistical adapters
↓
DeepAR
↓
containers
↓
integration
↓
production infrastructure
```

Do not build infrastructure ahead of demonstrated need.
