# Route B — Status

PANGO Prediction, the production forecasting service. Prepared for the Route A owner and for whoever tracks this project.

**Last updated:** 9 November 2026 · **Contract served:** v1.1 · **Domain parameters fingerprint:** `bd4371bcce546b17`

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
| Prediction intervals from empirical residuals | `domain/intervals.py` | Route A: 0.10 / 0.90 |
| Tenant-scoped calibration and its parity hash | `domain/parameters.py` | §11 of `domain-rules.md` |
| Model port | `ports/model.py` | — |
| Model registry | `ports/registry.py` | — |
| Baselines: `zero`, `naive`, `moving_average` | `adapters/outbound/baselines.py` | — |
| Forecast use case | `application/forecast_service.py` | — |
| HTTP contract v1.1, authentication, batching, logging | `adapters/inbound/api/` | `contract-v1.md` |
| CPU container | `docker/Dockerfile.api` | plan B22 |

Source: 1,409 lines of domain, 266 of application, 177 of ports, 624 of adapters.

### Verified by

| Suite | Tests |
|---|---|
| Domain | 141 |
| Models and the port | 26 |
| Application | 14 |
| API | 31 |
| Integration against the request fixtures | 14 |
| Registry, config, assembly, architecture, packaging | 42 |
| **Total** | **276** |

`ruff check .` clean · `mypy --strict` clean across 44 files · architecture tests assert that no inner layer imports a web framework and that the domain imports nothing outward.

---

## 3. What the service answers today

A successful response contains weekly periods and model metadata:

```json
{
  "series_id": 1198,
  "status": "ok",
  "model_name": "naive",
  "model_version": "development",
  "policy_version": "development-1",
  "periods": [
    {
      "period_start": "2026-11-02",
      "estimate": 3.0,
      "lower": 0.0,
      "upper": 6.0
    }
  ]
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
      model_name=naive model_version=development duration_ms=5.8 status=partial
```

No part of the payload is logged.

---

## 4. What remains for end-to-end integration

Pango Core still needs to implement the consumer side of the contract:

- build the request from movements already marked as demand-generating and daily stock observations;
- call `POST /v1/forecasts` with authentication and a unique `request_id`;
- persist `ForecastRun`, successful `Forecast` rows and their `ForecastPeriod` rows;
- persist per-series errors and represent partial runs;
- make retries idempotent using `request_id`;
- use the forecast output to calculate alerts and reorder points.

The following are intentionally outside the current integration bridge:

- administrator-managed closure dates;
- DeepAR training and checkpoint serving;
- the final Route A model-selection policy;
- direct database access from this service.

The current policy is explicitly a development artifact and must not be
interpreted as the final statistical model decision.

---

## 5. Decisions Route B took that Route A has to confirm

Each of these changes a number or a routing outcome, so none is cosmetic.

### 5.1 The classification window — a contradiction inside the notebook

The agreed parameter block (cell 13.2) declares `ventana_cuadrante_meses: 24`. The cell that actually computes the quadrant (7.4) truncates nothing: it builds `period_range(first_month_of_sale, ULT_MES)`, which over data running from January 2023 to August 2026 is up to **44 months**, not 24.

The two give different ADI and CV², therefore different classes and different models.

**Route B reproduces what was executed**, because those are the classes every downstream decision was made from. The window exists as `DomainParameters.classification_window_months`, defaulting to `None`. Switching it to 24 is a one-line change. **Which did you mean?**

### 5.2 The fingerprint recipe

`params_version` is meant to prove the evaluation run and the production run used the same calibration. That only works if both routes hash the same way. Route B's recipe:

```text
the DomainParameters fields as JSON, keys sorted, no whitespace
  -> SHA-256 -> first 16 hex characters
```

For the current defaults: **`bd4371bcce546b17`**. If Route A hashes differently the two values will never match and the audit is worthless. This needs agreeing, not assuming.

### 5.3 `zero` is not in the model enum

Route B ships `zero` as the benchmark every other model must beat. It is absent from the policy's model enum, so no rule can route to it — yet forecasting zero is the natural answer for an inactive series (26 weeks without a sale, S29), and the enum currently offers only `pooled_rate` for that. **Should `zero` be selectable?**

### 5.4 `INSUFFICIENT` against `NOT_CLASSIFIABLE`

The notebook distinguishes two failures that the earlier five-class proposal conflated. Route B maps them onto the two values the enum already has:

| Value | Meaning | Notebook |
|---|---|---|
| `INSUFFICIENT` | There is a monthly series; its statistics cannot be computed | `insuficiente` (44 % of products) |
| `NOT_CLASSIFIABLE` | There is no monthly series: every month was spent in a stockout | `sin serie mensual` |

**Confirm the mapping.** Separately: `history_class` and `demand_class` both having an `INSUFFICIENT`-shaped value is a name collision waiting to confuse — Route B named the history classes `NEW` / `LIMITED` / `MATURE` to avoid it.

### 5.5 `MADE_TO_ORDER` means two different things

The notebook's S24 classes **332 series** as sold to order, while only **13** are routed to an aggregated forecast. If the policy's rules match on `MADE_TO_ORDER` expecting the 13, they will catch 332. Route B reports the S24 class in `profile.stock_handling`; the routing decision needs its own name.

### 5.6 `NO_FORECAST` is a status, not an error

Route A's reply sketches a third outcome with a reason (`non_stock_warehouse`). Route B has not created it, because the status set belongs to the contract and the policy format. Today those series come back as `MODEL_NOT_AVAILABLE` with warnings. **The contract needs a third status.**

### 5.7 How does the service learn a warehouse holds stock?

S15 says stock is measured only in the main warehouses, and that is **derived** in the notebook. The tenant schema's `warehouse_type` does not carry it. Without an answer, the service cannot tell a genuine stockout from a warehouse that never holds stock.

### 5.8 Export the learned year-end closure

The notebook derives `cierre` from history: business days with no sales between 26 December and 10 January, over 2022–2025. That cannot be evaluated for a future year, so `x in cierre` is false for every horizon week — **the notebook's future year-ends score a full week while its historical ones score nearly nothing**, a skew in a variable DeepAR consumes as known.

Route B's covariate reads only the dates and the tenant's table, so history and horizon use one rule. Matching the notebook on history requires Core to be seeded with the dates `cierre` contained, so **that learned set should be exported** rather than re-derived.

---

## 6. Resolved since the last report

| Question | Resolution |
|---|---|
| Who computes public holidays | **Core sends every non-operational date.** The weight rule is shared code; the dates are data. A country code was evaluated and rejected for the forecast path: it covers public holidays and misses the company shutdown, Holy Saturday and per-warehouse differences, and it would make parity depend on both routes pinning the same package. It remains useful for drafting a new tenant's calendar at onboarding. |
| Closures per request or per warehouse | **Both**, resolved as a union. A warehouse cannot re-open a day the tenant declared closed. |
| Prediction intervals | Implemented. `ForecastPeriod` validates that the estimate lies inside its interval. |
| Warehouse distribution percentages | Not done, by decision: series are `product × warehouse` and forecast bottom-up, with no allocation. |
| Contract drift | `contract-v1.md` reconciled to v1.1 and its response example is now **captured from the running service**. Four discrepancies closed. |

---

## 7. Not built, and why

| Not built | Reason |
|---|---|
| Policy loader | Blocked on §4 above |
| `seasonal` flag (the fifth routing dimension) | The rule hardcodes October–December, a calibration of one Colombian catalogue. Needs a tenant-scoped quarter before it can be written at all |
| StatsForecast adapters (`croston`, `sba`, `tsb`, `ets`, `theta`, …) | The port and the registry are ready; these are mechanical once the policy names them |
| `seasonal_naive_block` | Needs the calendar alignment the S31 contraction removes, so it must not use the contracted series |
| DeepAR training and runtime | Separate images by design (plan B23, B24). Training never happens inside a forecast request |
| Dependency lock file | Declared dependencies are lower bounds, so two builds a month apart can resolve differently. Fine for development, not for production; the choice between `uv` and `pip-tools` belongs to whoever owns deployment |
| CI | Not started |

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

235 MB. No PyTorch, no NeuralForecast, no StatsForecast, and no NumPy or pandas either — the domain needed no array library. Runs as an unprivileged user, carries a healthcheck, and **refuses to start without a service credential** rather than starting open.

Request fixtures that match the tenant schema are in `tests/fixtures/`, with the field-to-column mapping in its `README.md`.

---

## 10. Where to read further

| Document | What it holds |
|---|---|
| `docs/domain-rules.md` | Every rule and calibrated parameter, with its notebook reference, its scope (platform rule or tenant calibration), the open decisions and 14 recorded discrepancies |
| `docs/contract-v1.md` | The HTTP contract at v1.1, with a response captured from the service |
| `AGENTS.md` | Architecture and working method |
