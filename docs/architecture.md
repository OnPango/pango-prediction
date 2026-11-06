# PANGO Forecast Service — Architecture

**Status:** Initial architecture  
**Forecast grain:** `product_warehouse_id`

## 1. Goal

Build a forecasting subsystem that is modular, testable, reproducible, horizontally scalable and compatible with both CPU models and GPU DeepAR.

Core principle:

```text
what is evaluated = what is deployed
```

## 2. System context

Current system:

```text
Frontend
   ↓
Pango Core
NestJS
   ↓
Supabase PostgreSQL
```

Pango Core is multi-tenant:

```text
public
tenant_<subdomain>
```

Forecast Service is a separate Python service.

## 3. Target architecture

```text
                           Frontend
                              |
                              v
                    +-------------------+
                    |    Pango Core     |
                    | NestJS / Prisma   |
                    +---------+---------+
                              |
               +--------------+---------------+
               |                              |
               v                              v
      Supabase PostgreSQL            Forecast API
      public + tenant_*             Python / FastAPI
                                             |
                            +----------------+----------------+
                            |                                 |
                            v                                 v
                     StatsForecast                    DeepAR Runtime
                         CPU                              GPU
                                                            |
                                                            v
                                                     Model checkpoint
                                                            |
                                                            v
                                                    Private storage


                       Offline / Batch Training

                    training dataset / bundle
                              |
                              v
                       DeepAR Trainer
                           GPU job
                              |
                              v
                         checkpoint
                              |
                              v
                     private model storage
```

## 4. Forecast grain

Production unit:

```text
product × warehouse
```

Therefore:

```text
series_id = product_warehouse_id
```

There is no global-product forecast followed by allocation.

## 5. Architectural boundaries

### Pango Core owns

- authentication;
- authorization;
- tenant resolution;
- PostgreSQL;
- products;
- warehouses;
- `product_warehouse_id`;
- business movement semantics;
- external historical imports;
- forecast-run lifecycle;
- forecast persistence;
- replenishment;
- alerts;
- frontend-facing APIs.

### Forecast Service owns

- temporal aggregation;
- regular time grid;
- weekly definition;
- stockout interpretation;
- availability state;
- `available_mask`;
- calendar features;
- ADI/CV²;
- demand classification;
- model policy;
- model execution;
- forecast intervals;
- model metadata.

### DeepAR Runtime owns

- checkpoint loading;
- GPU inference;
- checkpoint cache;
- neural runtime preprocessing.

### DeepAR Trainer owns

- offline training;
- evaluation;
- checkpoint generation;
- artifact metadata.

## 6. Why Python does not access PostgreSQL

Forecast Service does not receive:

```text
DATABASE_URL
DIRECT_URL
```

Reasons:

1. Pango Core already owns tenancy.
2. Pango Core already understands business semantics.
3. Python should not know tenant schema names.
4. Database changes should not leak into forecasting code.
5. Forecast Service becomes easier to test.
6. Forecast Service becomes horizontally scalable.
7. Security scope is smaller.

Communication:

```text
Pango Core
    ↓ HTTP JSON
Forecast Service
```

## 7. Repository structure

```text
pango-forecast-service/
│
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
│
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.deepar
│   └── Dockerfile.train
│
├── src/
│   └── pango_forecast/
│       ├── main.py
│       ├── config.py
│       │
│       ├── api/
│       │   ├── routes.py
│       │   ├── schemas.py
│       │   └── errors.py
│       │
│       ├── application/
│       │   └── forecast_service.py
│       │
│       ├── domain/
│       │   ├── types.py
│       │   ├── series.py
│       │   ├── availability.py
│       │   ├── calendar.py
│       │   ├── profile.py
│       │   └── policy.py
│       │
│       ├── models/
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── baselines.py
│       │   ├── statsforecast.py
│       │   └── deepar.py
│       │
│       └── artifacts/
│           └── model_policy.json
│
├── training/
│   ├── train_deepar.py
│   └── evaluate.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── api/
│   └── fixtures/
│
└── docs/
    ├── contract-v1.md
    ├── architecture.md
    ├── models.md
    └── operations.md
```

## 8. Layered architecture

```text
API
 ↓
Application
 ↓
Domain
 ↓
Models
```

This is intentionally simpler than a ceremonial Clean Architecture.

## 9. API layer

Location:

```text
src/pango_forecast/api/
```

Responsibilities:

- HTTP parsing;
- Pydantic validation;
- service authentication;
- status codes;
- serialization;
- error translation.

The API layer must not compute ADI, build weeks, select models or call StatsForecast directly.

## 10. Application layer

Main component:

```text
application/forecast_service.py
```

Flow:

```text
request
↓
build series
↓
build availability
↓
build calendar
↓
profile demand
↓
resolve policy
↓
resolve model
↓
forecast
↓
result
```

## 11. Domain layer

Location:

```text
src/pango_forecast/domain/
```

This is the reusable forecasting core and must remain independent from FastAPI and Pango Core.

## 12. `types.py`

Defines:

```text
DemandObservation
StockObservation
ForecastSeriesInput
RegularizedSeries
DemandProfile
ModelSpec
ForecastPeriod
ForecastResult
```

## 13. `series.py`

Responsibilities:

- validate daily demand;
- order observations;
- aggregate duplicate dates;
- define Monday-start weeks;
- aggregate daily demand to weekly;
- fill missing weeks;
- produce a regular series.

Week definition:

```text
Monday → Sunday
```

## 14. `availability.py`

Produces:

```text
AVAILABLE
CENSORED
UNKNOWN
SIN_SURTIDO
available_mask
```

Rules include no-sales/zero-stock, sales with zero closing stock, made-to-order treatment and long no-stock periods.

This code is shared by benchmark and production.

## 15. `calendar.py`

Responsible for business-day calculation and future calendar generation.

Current convention:

```text
Mon–Fri = 1.0
Saturday = 0.6
Sunday = 0.0
closure = 0.0
```

## 16. `profile.py`

Responsible for:

- observed periods;
- nonzero periods;
- ADI;
- CV²;
- inactive series;
- short-history detection;
- demand classification.

Profile describes the series. It does not select the model.

## 17. `policy.py`

Maps:

```text
DemandProfile
→ ModelSpec
```

The mapping comes from:

```text
model_policy.json
```

This keeps experimental decisions outside hardcoded source logic.

## 18. Models layer

All models expose a common interface, conceptually:

```python
predict(series, horizon, params)
```

## 19. Model registry

Stable model names:

```text
zero
naive
moving_average_8
auto_ets
croston_sba
tsb
adida
imapa
auto_arima
deepar
```

Avoid large `if/elif` model dispatch chains.

## 20. StatsForecast wrappers

`models/statsforecast.py` contains thin wrappers around the same library used by benchmark.

Do not reimplement:

- AutoETS;
- Croston SBA;
- TSB;
- ADIDA;
- IMAPA;
- AutoARIMA.

Therefore:

```text
benchmark model = production model
```

## 21. DeepAR architecture

DeepAR introduces two distinct workloads:

```text
training
inference
```

They must remain separate.

## 22. DeepAR training

```text
training dataset
      ↓
DeepAR Trainer
      ↓
GPU
      ↓
checkpoint
      ↓
evaluation
      ↓
private model storage
```

Training never runs inside `/v1/forecasts`.

## 23. DeepAR inference

```text
Forecast API
      ↓
policy selects DeepAR
      ↓
DeepAR Runtime
      ↓
load/cache checkpoint
      ↓
GPU inference
      ↓
forecast
```

Track checkpoint version, model reference, device, cache hit and inference time.

## 24. Multi-tenant DeepAR

A checkpoint trained on one organization must not automatically serve another.

Example:

```text
deepar-equipmaster-v3
```

Pango Core maintains:

```text
organization → active model_ref
```

Forecast Service receives only the required model reference.

## 25. Model storage

Checkpoints must not live in:

```text
Git
PostgreSQL
```

Use private object storage.

Supabase Storage is a natural candidate.

Artifacts:

```text
model.ckpt
metadata.json
```

PostgreSQL stores only references and metadata.

## 26. Recommended model metadata

```text
model
version
status
artifact_path
artifact_sha256
training_start
training_end
trained_at
metrics
policy_version
```

This supports traceability and rollback.

## 27. Forecast run lifecycle

Owned by Pango Core:

```text
PENDING
↓
RUNNING
↓
COMPLETED
```

Alternative terminal states:

```text
PARTIAL
FAILED
```

## 28. Frontend execution pattern

Recommended:

```http
POST /forecast-runs
```

returns:

```http
202 Accepted
```

with `run_id`.

Frontend later calls:

```http
GET /forecast-runs/:id
```

Pango Core may still call Forecast Service synchronously internally for the MVP.

## 29. Queue strategy

RabbitMQ/Kafka are not required initially.

Start with:

```text
forecast_runs
+
backend orchestration
+
HTTP Forecast Service
```

Introduce a queue only if concurrency, retries, long-running jobs or backpressure require it.

## 30. Stateless Forecast API

Forecast API must not store:

- user session;
- tenant session;
- database transaction;
- run state.

This allows horizontal scaling behind a load balancer.

## 31. CPU/GPU deployment split

### CPU service

Contains:

- FastAPI;
- domain;
- application;
- StatsForecast;
- policy.

### GPU runtime

Contains:

- PyTorch;
- NeuralForecast;
- DeepAR;
- checkpoint cache.

### GPU trainer

Contains:

- training pipeline;
- model evaluation;
- checkpoint publication.

## 32. Docker layout

```text
docker/
├── Dockerfile.api
├── Dockerfile.deepar
└── Dockerfile.train
```

Benefits:

- smaller CPU image;
- isolated GPU dependencies;
- independent scaling;
- simpler debugging.

## 33. Testing strategy

### Unit

Test:

```text
series
availability
calendar
profile
policy
models
```

### Integration

Test:

```text
request
→ application service
→ result
```

without HTTP.

### API

Test:

- health;
- valid forecast;
- invalid request;
- invalid series;
- partial success;
- unauthorized request.

### Golden test

Privately verify:

```text
EDA output
=
production series builder output
```

using EquipMaster data.

## 34. Benchmark reuse

Notebooks must import the same domain and model wrappers:

```python
from pango_forecast.domain.series import ...
from pango_forecast.domain.availability import ...
from pango_forecast.domain.profile import ...
from pango_forecast.models.statsforecast import ...
```

This avoids duplicate forecasting logic.

## 35. Observability

Structured logs include:

```text
request_id
forecast_run_id
policy_version
series_count
model_name
model_version
duration_ms
status
```

DeepAR adds:

```text
checkpoint_ref
checkpoint_cache_hit
checkpoint_load_ms
inference_ms
device
```

Do not log full historical payloads by default.

## 36. Security

Use service-to-service authentication independent from end-user auth.

Forecast Service does not validate PANGO user roles.

That remains a Pango Core responsibility.

## 37. CI/CD

### Forecast API

```text
tests
↓
lint
↓
type checks
↓
CPU Docker build
↓
integration tests
↓
deploy
```

### DeepAR runtime

```text
tests
↓
GPU Docker build
↓
small inference smoke test
↓
deploy
```

### Trainer

CI performs only a small synthetic training smoke test, not full training.

## 38. Failure isolation

A failure in one series must not fail the entire batch.

Example:

```text
99 series = OK
1 series = MODEL_FAILED
```

Pango Core may persist successful results.

## 39. Scaling strategy

Initial:

```text
1 Forecast API CPU instance
1 GPU runtime when required
```

Future CPU scaling:

```text
load balancer
├── Forecast API 1
├── Forecast API 2
└── Forecast API N
```

GPU scaling remains independent.

## 40. Architectural invariants

1. Forecast grain is `product_warehouse_id`.
2. Forecast Service does not access tenant PostgreSQL directly.
3. Pango Core owns business semantics.
4. Forecast Service owns temporal forecasting transformations.
5. Benchmark and production share domain code.
6. StatsForecast models are wrappers, not rewrites.
7. DeepAR training is offline.
8. Checkpoints are versioned artifacts.
9. Forecast API is stateless.
10. Policy is versioned and external to profiling logic.

## 41. Open decisions

Still open:

- final CPU hosting provider;
- final GPU hosting provider;
- exact model-storage implementation;
- final DB schema for model metadata;
- DeepAR retraining policy;
- model activation/rollback procedure;
- maximum request batch size;
- exact production service-auth mechanism.

These do not block implementation of the domain core.

## 42. First end-to-end target

```text
Pango Core
   ↓
one synthetic product_warehouse series
   ↓
Forecast API
   ↓
series.py
   ↓
availability.py
   ↓
profile.py
   ↓
development policy
   ↓
baseline / StatsForecast
   ↓
response
```

DeepAR runtime integration comes after this path works.

## 43. Final principle

```text
Pango Core
= business + tenancy + persistence

Forecast Domain
= series + availability + profiling

Model Layer
= forecasting algorithms

Training / Benchmark
= evaluation + selection + model artifacts
```

The bridge between experimentation and production is:

```text
shared domain code
+
versioned model policy
+
versioned model artifacts
```
