# PANGO Forecast Service

Production forecasting service for **PANGO**, an inventory management platform.

This repository contains the infrastructure and application logic required to transform historical demand and stock data into reproducible inventory demand forecasts at the:

```text
product × warehouse
```

level.

The forecasting unit is identified by:

```text
series_id = product_warehouse_id
```

The service receives historical data from **Pango Core**, applies the same preprocessing and demand classification logic used during model evaluation, selects the appropriate forecasting model through a versioned policy, and returns forecasts for the configured horizon.

The initial production horizon is:

```text
frequency = weekly
horizon = 4 weeks
```

---

## Project Status

🚧 **Under active development — HTTP integration ready for Pango Core**

The forecasting domain, development policy wiring, baseline execution and HTTP
response contract are implemented. The remaining integration work is on the
Pango Core side: submit normalized daily inputs, persist the response and
calculate inventory alerts and reorder points.

Development is intentionally incremental:

```text
Domain
  ↓
Models
  ↓
Policy
  ↓
Application Service
  ↓
HTTP API
  ↓
Pango Core Integration
  ↓
DeepAR Runtime
```

The first objective is not to deploy a complex ML model.

The first objective is to build a deterministic and fully tested forecasting pipeline.

---

# Architecture

PANGO separates forecasting responsibilities into two independent but connected workflows.

```text
ROUTE A
Model evaluation and selection
        │
        │ produces
        ▼
model_policy.json
DeepAR checkpoint (when applicable)
        │
        ▼
ROUTE B
Production forecasting service
```

This repository implements **Route B**.

Route A determines which forecasting strategy performs best.

Route B executes that strategy in production.

The core principle is:

```text
same series construction
+
same availability logic
+
same demand profile
+
same model
=
what was evaluated is what gets deployed
```

---

# System Context

```text
                    ┌──────────────────────┐
                    │      Pango Core      │
                    │       NestJS         │
                    │                      │
                    │ demand               │
                    │ stock                │
                    │ metadata             │
                    └──────────┬───────────┘
                               │
                               │ HTTP
                               ▼
                ┌──────────────────────────────┐
                │    PANGO Forecast Service    │
                │                              │
                │ FastAPI                      │
                │      │                       │
                │      ▼                       │
                │ Application Service          │
                │      │                       │
                │      ▼                       │
                │ Domain                       │
                │ ├── series                   │
                │ ├── availability             │
                │ ├── calendar                 │
                │ └── profile                  │
                │      │                       │
                │      ▼                       │
                │ Policy                       │
                │      │                       │
                │      ▼                       │
                │ Model Registry               │
                └──────┬───────────────┬───────┘
                       │               │
                       ▼               ▼
              Statistical Models   DeepAR Runtime
                    CPU                 GPU
```

The Forecast Service **does not access the PANGO PostgreSQL database directly**.

Pango Core is responsible for:

- resolving the tenant;
- retrieving historical demand;
- retrieving historical stock;
- resolving `product_warehouse_id`;
- creating forecast runs;
- sending normalized input to the Forecast Service;
- persisting forecast results.

The Forecast Service is responsible only for forecasting.

It does not calculate inventory alerts or reorder points. It returns weekly
forecast estimates and intervals so Pango Core can apply those business rules.

---

# Design Principles

## 1. Domain-first architecture

The forecasting domain must not depend on FastAPI, HTTP, databases, cloud providers, or Pango Core.

```text
API
 ↓
Application
 ↓
Domain
```

The domain must be executable directly from Python and reusable from notebooks, experiments and production services.

---

## 2. No training-serving skew

The transformation code used during experimentation must be the same code used in production.

The following modules are shared between model evaluation and production:

```text
domain/series.py
domain/availability.py
domain/calendar.py
domain/profile.py
models/
```

Model evaluation notebooks should import these modules instead of implementing their own preprocessing logic.

---

## 3. Models are interchangeable

Application code must not depend directly on a specific forecasting algorithm.

All models expose a common interface conceptually equivalent to:

```python
predict(series, horizon, params)
```

Models are resolved through a registry.

This avoids large `if / elif` chains and allows the active model to be selected through configuration.

---

## 4. Model selection belongs to Policy

Production code does not decide which forecasting algorithm is best.

That decision is represented by a versioned:

```text
model_policy.json
```

The policy maps demand profiles to model specifications.

Example:

```json
{
  "policy_version": "v1",
  "forecast_level": "product_warehouse",
  "frequency": "weekly",
  "horizon": 4,
  "models": {
    "smooth": {
      "name": "auto_ets",
      "params": {}
    },
    "intermittent": {
      "name": "croston_sba",
      "params": {}
    }
  }
}
```

The final policy is generated by the model evaluation workflow.

During development, this repository may use a temporary:

```text
development_policy.json
```

---

## 5. Training never happens during inference

The endpoint:

```text
POST /v1/forecasts
```

must never train a model.

DeepAR training runs independently as an offline batch process.

```text
training dataset
      ↓
DeepAR Trainer
      ↓
checkpoint
      ↓
private model storage
      ↓
DeepAR Runtime
      ↓
forecast
```

---

## 6. Partial failures are expected

Forecast requests may contain multiple series.

A failure in one series must not necessarily invalidate the entire batch.

Example:

```text
100 series requested

99 → OK
 1 → INVALID_INPUT
```

Each series produces its own status and stable error code.

HTTP-level errors are reserved for failures affecting the complete request.

---

# Forecasting Pipeline

Each forecast follows the same application pipeline:

```text
input
  ↓
series regularization
  ↓
availability analysis
  ↓
calendar
  ↓
demand profile
  ↓
model policy
  ↓
model registry
  ↓
forecast model
  ↓
forecast result
```

---

# Domain

## Series

`domain/series.py`

Responsibilities:

- receive daily demand observations;
- validate dates;
- validate quantities;
- aggregate duplicated daily observations;
- group demand into Monday–Sunday weeks;
- fill missing weeks;
- generate a regular weekly time series;
- preserve `product_warehouse_id` as the series identifier.

Production frequency:

```text
weekly
```

Production forecast horizon:

```text
4 weeks
```

---

## Availability

`domain/availability.py`

Availability is calculated from historical stock rather than received as a precomputed backend flag.

Supported states:

```text
AVAILABLE
CENSORED
UNKNOWN
```

The module will also handle domain rules including:

- no sales + zero ending stock;
- sales + zero ending stock;
- made-to-order products;
- products with extended periods without stock.

The output includes an availability mask that determines which historical observations can be considered reliable demand observations.

---

## Calendar

`domain/calendar.py`

The forecasting calendar represents operational availability.

Initial rules:

```text
Monday–Friday = 1.0
Saturday      = 0.6
Sunday        = 0.0
holiday       = 0.0
closure       = 0.0
```

The calendar is also generated for the future four-week forecasting horizon.

---

## Demand Profile

`domain/profile.py`

The demand profile describes the statistical behavior of each series.

Initial metrics include:

```text
observed_periods
nonzero_periods
ADI
CV²
```

The profile also identifies:

```text
inactive
short_history
```

Demand classification is kept separate from model policy.

This distinction is intentional:

```text
profile = what the demand looks like
policy  = what model should handle it
```

---

# Models

Models live under:

```text
src/pango_forecast/models/
```

Planned model registry:

```text
zero
naive
moving_average_8
seasonal_naive
auto_ets
croston_sba
tsb
adida
imapa
auto_arima
deepar
```

---

## Baselines

Initial baseline implementations:

- Zero
- Naive
- Moving Average 8
- Seasonal Naive, when enabled by the production policy

Baselines are first-class production models and must use the same model interface as more complex algorithms.

---

## Statistical Models

Statistical forecasting models are implemented as wrappers around **StatsForecast**.

The service will not manually reimplement the forecasting algorithms.

Planned models:

- AutoETS
- CrostonSBA
- TSB
- ADIDA
- IMAPA
- AutoARIMA

---

## DeepAR

DeepAR is treated differently from local CPU models.

It consists of two independent components.

### Trainer

```text
training/train_deepar.py
```

Responsibilities:

- consume a training dataset;
- train a global model across multiple series;
- store training configuration;
- store random seed;
- store training period;
- save model checkpoint;
- calculate artifact SHA-256;
- generate model metadata.

### Runtime

Responsibilities:

- load a versioned checkpoint;
- cache the checkpoint;
- perform inference;
- report inference duration;
- report execution device;
- report checkpoint cache hits.

DeepAR models are tenant-specific unless explicitly validated otherwise.

Example model reference:

```text
deepar-equipmaster-v1
```

---

# Model Artifacts

Large model artifacts must not be committed to Git.

DeepAR checkpoints will be stored in private object storage.

Each artifact should contain or reference:

```text
model_ref
checkpoint_version
training_period
library_version
artifact_sha256
metadata
```

Checksum verification must occur before using downloaded model artifacts.

---

# HTTP API

The external interface is implemented using FastAPI.

FastAPI acts only as an HTTP adapter.

It must not contain forecasting business logic.

Initial endpoints:

```http
GET /health
```

```http
POST /v1/forecasts
```

---

## Forecast Request

The v1 contract includes:

```text
contract_version
request_id
forecast_run_id
frequency
horizon
history_end
calendar
series[]
```

Each series contains:

```text
series_id
product_id
warehouse_id
metadata
demand[]
stock[]
```

Where:

```text
series_id = product_warehouse_id
```

The complete contract will be documented in:

```text
docs/contract-v1.md
```

---

# Service-to-Service Security

The Forecast Service is an internal PANGO service.

Authentication between Pango Core and the Forecast Service must use a dedicated service credential.

User JWTs must not be reused for this communication.

Secrets must:

- be configured through the runtime environment;
- never be committed to Git;
- be different between environments.

---

# Repository Structure

Planned repository structure:

```text
pango-prediction/
│
├── pango_prediction/
│   │
│   ├── domain/                 # Business / forecasting rules
│   │   ├── types.py
│   │   ├── series.py
│   │   ├── availability.py
│   │   ├── calendar.py
│   │   ├── profile.py
│   │   └── policy.py
│   │
│   ├── application/            # Use cases
│   │   └── forecast_service.py
│   │
│   ├── ports/                  # Interfaces
│   │   ├── model.py
│   │   └── artifact_store.py
│   │
│   ├── adapters/
│   │   │
│   │   ├── inbound/            # Things calling PANGO Prediction
│   │   │   └── api/
│   │   │       ├── schemas.py
│   │   │       ├── routes.py
│   │   │       └── errors.py
│   │   │
│   │   └── outbound/           # Things PANGO Prediction calls
│   │       ├── statsforecast/
│   │       ├── deepar/
│   │       └── artifacts/
│   │
│   ├── config.py
│   └── main.py
│
├── training/
│   └── train_deepar.py
│
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.deepar
│   └── Dockerfile.train
├── docs/
│   ├── contract-v1.md
│   └── architecture/
├── pyproject.toml
└── README.md
└── tests/
    ├── unit/
    │   ├── domain/
    │   └── models/
    │
    ├── integration/
    ├── api/
    └── golden/
```

---

# Runtime Components

The final architecture may produce three container images from this repository.

## Forecast API — CPU

```text
Dockerfile.api
```

Contains:

- FastAPI;
- forecasting domain;
- application service;
- StatsForecast;
- policy loader.

It does not require PyTorch when DeepAR inference is deployed separately.

---

## DeepAR Runtime — GPU

```text
Dockerfile.deepar
```

Contains:

- PyTorch;
- CUDA-compatible runtime;
- NeuralForecast;
- DeepAR inference logic;
- shared forecasting domain.

---

## DeepAR Trainer — GPU Batch Job

```text
Dockerfile.train
```

Runs:

```text
training/train_deepar.py
```

The trainer is not intended to operate as a permanently running HTTP service.

---

# Development Setup

Python version:

```text
Python 3.12
```

The project uses `pip` and a virtual environment. `uv` is not used.

Create the environment once:

```text
python -m venv .venv
```

Activate it in every new shell:

```text
Windows (PowerShell):  .venv\Scripts\Activate.ps1
Linux / macOS:         source .venv/bin/activate
```

Install the runtime dependencies and the `dev` group, both read from `pyproject.toml`:

```text
python -c "import subprocess, sys, tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call([sys.executable, '-m', 'pip', 'install', *p['project']['dependencies'], *p['dependency-groups']['dev']])"
```

Run tests:

```text
python -m pytest
```

Run linting:

```text
ruff check .
```

Run type checks:

```text
python -m mypy pango_prediction tests
```

---

# Testing Strategy

Testing is a core requirement of the forecasting architecture.

## Unit Tests

Cover:

```text
series
availability
calendar
profile
policy
registry
models
```

---

## Integration Tests

Validate the complete application flow:

```text
input
 ↓
Forecast Service
 ↓
model execution
 ↓
ForecastResult
```

---

## API Tests

Initial API scenarios:

- health endpoint;
- valid forecast request;
- invalid series;
- partial success;
- unauthorized request.

---

## Golden Tests

Golden tests validate that the exact same input produces equivalent transformed series in experimentation and production.

The objective is to detect accidental differences between:

```text
model evaluation pipeline
```

and:

```text
production inference pipeline
```

---

# Observability

Every forecast run should produce structured JSON logs.

Minimum fields:

```text
request_id
forecast_run_id
series_count
policy_version
model
model_version
duration
status
```

DeepAR runtime additionally records:

```text
gpu_device
checkpoint
cache_hit
checkpoint_load_time
inference_time
```

---

# Pango Core Integration

Pango Core owns the forecasting workflow exposed to the frontend.

Conceptually:

```text
Frontend
   │
   │ POST /forecast-runs
   ▼
Pango Core
   │
   │ 202 Accepted
   ▼
forecast_run
   │
   ├── collect historical data
   ├── call Forecast Service
   ├── persist forecast
   └── update run status
```

The frontend can later query:

```http
GET /forecast-runs/:id
```

Long-running GPU inference must not require the browser to keep an HTTP connection open.

A message broker is not required for the initial MVP unless real operational needs justify it.

---

# Development Roadmap

## Milestone 0 — Foundation

- [ ] Initialize repository
- [ ] Configure `pyproject.toml`
- [ ] Configure testing
- [ ] Configure linting
- [ ] Configure type checking
- [ ] Create project structure
- [ ] Define domain types

## Milestone 1 — Forecast Domain

- [ ] Implement `series.py`
- [ ] Implement `availability.py`
- [ ] Implement `calendar.py`
- [ ] Implement `profile.py`
- [ ] Add unit tests
- [ ] Add golden tests

## Milestone 2 — Model Engine

- [ ] Define model interface
- [ ] Implement model registry
- [ ] Implement baselines
- [ ] Implement StatsForecast wrappers
- [ ] Implement policy loader
- [ ] Implement application service

## Milestone 3 — HTTP API

- [ ] Define contract v1
- [ ] Implement FastAPI schemas
- [ ] Implement `/health`
- [ ] Implement `/v1/forecasts`
- [ ] Implement service authentication
- [ ] Implement partial failures

## Milestone 4 — DeepAR

- [ ] Implement trainer
- [ ] Implement artifact metadata
- [ ] Implement checkpoint storage
- [ ] Implement DeepAR runtime
- [ ] Implement checkpoint cache
- [ ] Add GPU container images

## Milestone 5 — Pango Integration

- [ ] Implement forecasting module in Pango Core
- [ ] Create forecast runs
- [ ] Persist forecast results
- [ ] Support asynchronous frontend workflow

## Milestone 6 — Production Readiness

- [ ] Structured logging
- [ ] Integration tests
- [ ] API tests
- [ ] CI/CD
- [ ] Docker builds
- [ ] Deployment
- [ ] Runtime monitoring

---

# Non-Goals

This service is **not** responsible for:

- user authentication;
- inventory CRUD;
- retrieving data directly from PANGO PostgreSQL;
- deciding which model performs best;
- storing business forecast results permanently;
- training DeepAR during forecast requests;
- exposing forecasting directly to the frontend.

These responsibilities belong to other parts of the PANGO architecture or to the model evaluation workflow.

---

# PANGO Forecasting Principle

The central invariant of this repository is:

```text
same data semantics
+
same preprocessing
+
same demand classification
+
same model implementation
=
reproducible production forecasts
```

Any architectural decision that breaks this invariant should be treated as a forecasting correctness issue, not merely as a software implementation detail.