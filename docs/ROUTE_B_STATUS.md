# Route B — Status

PANGO Prediction, the production forecasting service. Prepared for the Route A owner and for whoever tracks this project.

**Last updated:** 16 November 2026 · **Contract served:** v1.2, with 1.0 and 1.1 still answered in 1.1 · **Domain parameters fingerprint:** `f5f416da285217b5`

Counts and tables describe `feature/warehouse-config-contract`, branched from `dev` at
`bcfe90e`. Section 5 records which Route A questions are closed; the Backend
contract is ready for the Core-side implementation.

---

## 1. Where it stands

**The forecasting domain is complete and the service produces deterministic baseline forecasts. The HTTP bridge is ready for Pango Core integration; production model selection and offline training remain Route A work.**

The service receives normalized daily demand and stock from Pango Core, constructs weekly series, resolves the development policy, executes a registered baseline and returns four forecast periods plus a calibrated horizon total per successful series. Per-series failures are returned without discarding successful series in the same request.

What remains is the Core-side client and persistence flow, followed by replacement of the development policy with the versioned policy produced by Route A.

---

## 2. What is implemented

| Capability | Module | Notebook basis |
|---|---|---|
| Weekly series construction | `domain/series.py` | 7.1, 7.5 |
| Availability, censoring, episodes, stock handling | `domain/availability.py` | 7.2, 7.3, S24–S26, S31 |
| Demand profile, monthly classification, ADI/CV² | `domain/profile.py` | 7.4, 7.5, S27, E1, E5, S29 |
| Calendar covariate (equivalent business days) | `domain/calendar.py` | 11.3, 11.4, S34 |
| Prediction intervals and horizon totals from empirical residuals | `domain/intervals.py` | Route A: 0.10 / 0.90 |
| Tenant-scoped calibration and its parity hash | `domain/parameters.py` | §11 of `domain-rules.md` |
| Policy artifact: loading, validation, per-series resolution | `domain/policy.py` | — |
| Forecast evaluation metrics | `domain/evaluation.py` | `contract-v1.md` §19 |
| Product-to-warehouse mean allocation, not called by the use case | `domain/allocation.py` | Route A A15 |
| Model port | `ports/model.py` | — |
| Model registry | `ports/registry.py` | — |
| Baselines: `zero`, `naive`, `moving_average` | `adapters/outbound/baselines.py` | — |
| SBA, in plain Python | `adapters/outbound/croston.py` | StatsForecast `CrostonSBA` 2.1.1 |
| StatsForecast wrappers: `ets`, `auto_arima`, `tsb`, `adida`, `imapa`, `croston` | `adapters/outbound/statsforecast.py` | — |
| DeepAR artifact boundary: metadata and checksum validation, injected runner | `adapters/outbound/deepar.py` | — |
| Forecast use case | `application/forecast_service.py` | — |
| HTTP contract v1.1, authentication, batching, logging, forecast evaluations | `adapters/inbound/api/` | `contract-v1.md` |
| CPU container | `docker/Dockerfile.api` | plan B22 |
| CI: tests, lint, types, image build and smoke test | `.github/workflows/ci.yml` | — |

Source: 2,361 lines of domain, 359 of application, 190 of ports, 1,583 of adapters.

### Verified by

| Suite | Tests |
|---|---|
| Domain | 232 |
| Models and the port | 67 |
| Application | 41 |
| API | 56 |
| Integration against the request fixtures | 14 |
| Registry, config, assembly, architecture, packaging | 43 |
| **Total** | **453** |

`ruff check .` clean · `mypy --strict` clean across 62 files · architecture tests assert that no inner layer imports a web framework and that the domain imports nothing outward. The model-error isolation regression is covered at the application and HTTP levels.

One gap in that coverage: no test runs the StatsForecast wrappers against the real library. Their unit tests replace it with a fake, and the CI smoke request is served by the development policy, which routes to baselines only.

---

## 3. What the service answers today

A successful result contains weekly periods, the horizon total and model metadata. Captured from the service on the request fixture, with the first of its four periods shown; `policy_version` travels once, in the response envelope:

```json
{
  "series_id": 1198,
  "status": "ok",
  "model_name": "naive",
  "model_version": "development",
  "periods": [
    {
      "period_start": "2026-11-02",
      "estimate": 3.0,
      "lower": 0.0,
      "upper": 6.0
    }
  ],
  "total": {
    "estimate": 12.0,
    "lower": 4.800000000000001,
    "upper": 18.0
  }
}
```

An invalid or non-evaluable series is reported independently:

```json
{
  "series_id": 1500,
  "status": "error",
  "periods": [],
  "error": {
    "code": "INVALID_SERIES",
    "message": "Demand quantity cannot be negative"
  }
}
```

The complete response envelope and field definitions are maintained in
[`contract-v1.md`](contract-v1.md). Pango Core should persist successful
periods and per-series errors separately, then calculate inventory alerts and
reorder points from the persisted forecast.

Per-request log, contract §20:

```text
INFO: request_id=req-... forecast_run_id=41 series_count=5 policy_version=development-1
      model_name=None model_version=None duration_ms=1.3 status=partial
```

`model_name` and `model_version` are null in this record: model identity is per series and travels in each result. No part of the payload is logged.

---

## 4. What remains for end-to-end integration

Pango Core still needs to implement the consumer side of the contract:

- build the request from movements already marked as demand-generating and daily stock observations;
- reconstruct daily stock history by replaying movements and movement lines;
- call `POST /v1/forecasts` with authentication and a unique `request_id`;
- persist `ForecastRun`, successful `Forecast` rows and their `ForecastPeriod` rows;
- persist `policy_version`, model metadata, profiles, warnings and horizon totals;
- persist per-series errors and represent partial runs;
- make retries idempotent using `request_id`;
- use the forecast output to calculate alerts and reorder points.

The Backend request/response instructions are now maintained in
[`contract-v1.md`](contract-v1.md). The following remain outside the current
integration bridge:

- administrator-managed closure dates;
- DeepAR training and checkpoint serving;
- the final Route A model-selection policy;
- direct database access from this service.

The current policy is explicitly a development artifact and must not be
interpreted as the final statistical model decision.

---

## 5. Decisions Route B took that Route A has to confirm

Each of these changes a number or a routing outcome, so none is cosmetic.

Route A answered on 14 November. 5.1 to 5.5 are closed, 5.6 and 5.7 are implemented in contract 1.2, and 5.8 is decided but needs Core to seed the calendar. Each subsection says where it stands.

### 5.1 The classification window — resolved

Route A confirmed 24 months (S30, A5). Route B now uses
`classification_window_months=24`; the resulting parameter fingerprint is
`f5f416da285217b5`.

### 5.2 The fingerprint recipe

`params_version` is meant to prove the evaluation run and the production run used the same calibration. That only works if both routes hash the same way. Route B's recipe:

```text
the DomainParameters fields as JSON, keys sorted, no whitespace
  -> SHA-256 -> first 16 hex characters
```

For the current defaults: **`f5f416da285217b5`**. Route A confirmed that the
fingerprint comes from `DomainParameters.fingerprint()` and is frozen with the
tag.

### 5.3 `zero` is not in the model enum — resolved

Route B ships `zero` as the benchmark every other model must beat. It is
absent from the policy's model enum, so no rule can route to it. Forecasting
zero remains a reference behavior, not a selectable policy model.

Route A confirmed that `zero` remains a reference only and is not a candidate or
policy model in v1.

### 5.4 `INSUFFICIENT` against `NOT_CLASSIFIABLE` — resolved

The notebook distinguishes two failures that the earlier five-class proposal conflated. Route B maps them onto the two values the enum already has:

| Value | Meaning | Notebook |
|---|---|---|
| `INSUFFICIENT` | There is a monthly series; its statistics cannot be computed | `insuficiente` (44 % of products) |
| `NOT_CLASSIFIABLE` | There is no monthly series: every month was spent in a stockout | `sin serie mensual` |

Route A confirmed this mapping. `history_class` and `demand_class` retain the
separate `NEW` / `LIMITED` / `MATURE` and demand-class vocabularies.

### 5.5 `MADE_TO_ORDER` means two different things — resolved for v1

The notebook's S24 classes **332 series** as sold to order, while only **13** are routed to an aggregated forecast. If the policy's rules match on `MADE_TO_ORDER` expecting the 13, they will catch 332. Route B reports the S24 class in `profile.stock_handling`; the routing decision needs its own name.

Route A confirmed that `MADE_TO_ORDER` is only the S24 stock-handling class.
If v2 adds the aggregate-routing rule, its policy condition will be named
`sold_to_order`; v1 has no such rule.

### 5.6 `NO_FORECAST` is a status, not an error — implemented in contract 1.2

Route A closed the reason vocabulary at `inactive`, `sold_to_order`,
`non_stock_warehouse`, `no_active_warehouse` and `history_only`; v1 has no
`sold_to_order` rule.

Contract 1.2 adds the status. A series the policy declines comes back as
`status: "no_forecast"` with its `reason`, and `summary.no_forecast` counts it
apart from `error`. The policy loader accepts only those five reasons.

A status is not an additive field, so it is served only to a client that
declares 1.2. One that declares 1.0 or 1.1 keeps receiving
`MODEL_NOT_AVAILABLE`, exactly as before, and is answered in 1.1.

Four of the five reasons can now be produced: `inactive` and `sold_to_order` by
a policy rule (policy v1 has no rule for the second), `non_stock_warehouse` and
`history_only` by the request, as 5.7 describes.

Two things remain. `no_active_warehouse` is never produced: it belongs to the
product-to-warehouse allocation, which is not connected to the use case. And
history insufficiency stays an error path until the policy routes it with a
reason of its own, which none of the five is.

### 5.7 How does the service learn a warehouse holds stock?

Resolved: `holds_stock` is administrator-managed configuration per warehouse,
not inferred by Core. EquipMaster is seeded privately from S15; the private
warehouse identifiers must not enter this repository.

Implemented in contract 1.2. The request carries an optional `warehouses[]`
block, each entry a `warehouse_id` and a `holds_stock`, and a series in a
warehouse that holds none comes back as `no_forecast` with the reason
`non_stock_warehouse`. A warehouse the request does not describe is treated as
holding stock, and its series say so in a warning. A series marked
`history_only` is declined with that reason. Both are heard before the policy.

What remains is on the Core side: storing the configuration and sending it.

### 5.8 Export the learned year-end closure

The notebook derives `cierre` from history: business days with no sales between 26 December and 10 January, over 2022–2025. That cannot be evaluated for a future year, so `x in cierre` is false for every horizon week — **the notebook's future year-ends score a full week while its historical ones score nearly nothing**, a skew in a variable DeepAR consumes as known.

Route B's covariate reads only the dates and the tenant's table, so history and horizon use one rule. Core should seed EquipMaster with the 151-date calendar attachment, including 2027. The verified SHA-256 is
`b3df11b6861a95a6d0803d065dc034d80512f2d7ccaa2291a34f1a52b7119c14`.

---

## 6. Resolved since the last report

| Question | Resolution |
|---|---|
| Who computes public holidays | **Core sends every non-operational date.** The weight rule is shared code; the dates are data. A country code was evaluated and rejected for the forecast path: it covers public holidays and misses the company shutdown, Holy Saturday and per-warehouse differences, and it would make parity depend on both routes pinning the same package. It remains useful for drafting a new tenant's calendar at onboarding. |
| Closures per request or per warehouse | **Both**, resolved as a union. A warehouse cannot re-open a day the tenant declared closed. |
| Prediction intervals | Implemented. `ForecastPeriod` validates that the estimate lies inside its interval. |
| Warehouse distribution percentages | Production still forecasts each `product × warehouse` series directly, with no allocation. Since 14 November `domain/allocation.py` holds Route A's top-down arm (A15), so that the 17 November run can compare the two; nothing in the use case calls it, and decision D1 stays open until that run reports. |
| Contract drift | `contract-v1.md` reconciled to v1.1 and its response example is now **captured from the running service**. Four discrepancies closed. |
| Route A classification freeze | 24-month window confirmed, applied to `DomainParameters`, the policy fixture and the documents; the fingerprint is `f5f416da285217b5`. |
| EquipMaster calendar seed | 151 dates verified. It is an external attachment, not repository data, and Core must seed the tenant calendar with it. |

---

## 7. Not built, and why

| Not built | Reason |
|---|---|
| Production policy | The loader exists; the artifact is Route A's to produce. The service still runs `config/development_policy.json`, which is not a statistical decision |
| `seasonal` flag (the fifth routing dimension) | The rule hardcodes October–December, a calibration of one Colombian catalogue. Needs a tenant-scoped quarter before it can be written at all |
| `seasonal_naive_block` | Needs the calendar alignment the S31 contraction removes, so it must not use the contracted series |
| DeepAR training job, runtime image, checkpoint storage and cache | Separate images by design (plan B23, B24). Training never happens inside a forecast request. The artifact boundary and a temporary development trainer exist; the rest waits for Route A's checkpoint and its metadata contract |
| Dependency lock file | Most declared dependencies are lower bounds, so two builds a month apart can resolve differently. Fine for development, not for production. `uv` is not used, so the choice is `pip-tools` or no lock, and belongs to whoever owns deployment |
| Deployment pipeline | CI builds and smoke-tests the image; nothing deploys it |

### Built since the report of 9 November

| Built | Where | Note |
|---|---|---|
| CI | `.github/workflows/ci.yml`, PR #6 | Tests, lint, types, image build and smoke test |
| Policy loader | `domain/policy.py`, PR #7 | Validates the policy against the registry and the parameter fingerprint |
| SBA and calibrated horizon totals | `adapters/outbound/croston.py`, `domain/intervals.py`, PR #8 | Route A's patch |
| StatsForecast wrappers | `adapters/outbound/statsforecast.py`, PR #8 | Route A's plan B keeps them out of policy v1 and records the dependency as a discrepancy for the two routes to settle (`docs/route-a/criteria.md`, annex B) |
| DeepAR artifact boundary | `adapters/outbound/deepar.py` | No runtime image; not validated with a Route A checkpoint |
| Product-to-warehouse mean allocation | `domain/allocation.py`, PR #10 | Route A's top-down arm; not called by the use case |
| Horizon-policy validation | `application/errors.py`, API adapter and tests, merged in `c84dd44` | Requests whose horizon differs from the loaded policy are rejected |
| Model-error isolation | `ports/model.py`, outbound adapters, application and API tests, merged in `7dbe72a` | Configuration and execution failures remain scoped to their series |
| `no_forecast` status and contract 1.2 | `domain/types.py`, `domain/policy.py`, application and API adapter | Served to clients that declare 1.2; the policy loader accepts only the five agreed reasons |
| Rejection of `metadata.made_to_order` | API schemas, merged in `bcfe90e` | `422`, as the contract promised since 1.1 |
| `warehouses[]` and `history_only` | `domain/types.py`, application and API adapter | Decline series by configuration, before the policy is consulted |

---

## 8. Multi-tenancy

PANGO is multi-tenant, and the forecasting numbers are not all of one kind. Eight calibrated values are now fields of a tenant-scoped `DomainParameters`, which travels with the policy artifact and is hashed into `params_version`:

```text
max_stockout_episode_weeks   made_to_order_threshold   min_weeks_with_stock_data
inactivity_weeks             new_history_max_weeks     limited_history_max_weeks
min_observed_weeks           weekday_weights           classification_window_months
```

The defaults are EquipMaster's values, used as a starting point for a tenant that has not been calibrated — not a claim that they hold anywhere else.

The calibration is carried on the **command**, not on the service instance: one request serves one tenant, but one process serves many, so a service holding a single calibration would apply one company's thresholds to another's series.

**Cold start is the normal state for a new tenant**, not an edge case: no demand history, no stock history, no policy. The service already answers sensibly — `INSUFFICIENT_HISTORY`, `NOT_CLASSIFIABLE`, `MODEL_NOT_AVAILABLE` — and that path deserves its own tests. One gap: `pooled_rate` borrows a rate from the category, which needs history *in the category*, and a tenant created yesterday has none anywhere.

---

## 9. Running it

```bash
docker build -f docker/Dockerfile.api -t pango-prediction:dev .
docker run -d -p 8000:8000 -e PANGO_PREDICTION_SERVICE_TOKEN=... pango-prediction:dev
```

No PyTorch and no NeuralForecast. The image does install StatsForecast and NumPy, which `pyproject.toml` declares as runtime dependencies; the 235 MB reported on 9 November predates them and the image has not been measured since. The domain, application and ports layers still import no array library. Runs as an unprivileged user, carries a healthcheck, and **refuses to start without a service credential** rather than starting open.

Request fixtures that match the tenant schema are in `tests/fixtures/`, with the field-to-column mapping in its `README.md`.

---

## 10. Where to read further

| Document | What it holds |
|---|---|
| `docs/domain-rules.md` | Every rule and calibrated parameter, with its notebook reference, its scope (platform rule or tenant calibration), the open decisions and 14 recorded discrepancies |
| `docs/contract-v1.md` | The HTTP contract at v1.1, with a response captured from the service |
| `docs/route-a/criteria.md` | Route A's pre-registered selection and acceptance criteria for policy v1 |
| `docs/worklog/` | One entry per feature branch: reasoning, checks and open questions |
| `AGENTS.md` | Architecture and working method |
