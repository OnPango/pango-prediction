# PANGO Prediction — Domain Rules and Parameters

**Status:** Initial registry
**Scope:** The forecasting rules and numeric parameters shared by Route A (model evaluation) and Route B (production service).

## 1. Why this document exists

The central invariant of the project is:

```text
same data semantics
+ same preprocessing
+ same demand classification
+ same model implementation
= what was evaluated is what gets deployed
```

That invariant is only auditable if the rules and thresholds are written down somewhere other than the analysis that produced them. Today most of them live inside a single Colab notebook of about 1.1 MB:

```text
docs/retail-inventory-forecasting-demand-analysis.ipynb
```

This file extracts those rules, records **where each one comes from**, and states **whether production implements it yet**.

### How to read a disagreement

When this document, the notebook, the other documents in `docs/` and the code disagree, the order of authority is:

1. **The notebook**, for forecasting semantics and numeric thresholds. Its decisions are backed by statistical analysis, sensitivity checks and answers from the company.
2. **The tenant database schema**, for what can be stored and what identifiers exist.
3. **The code**, for what production actually does today.
4. **The other documents** (`README.md`, `architecture.md`, `contract-v1.md`, the two-route plan), which were written earlier and are the most likely to be stale.

A disagreement is never resolved silently. Section 12 lists the open ones.

### Notebook references

`7.3` and similar refer to notebook sections. `S24` and similar refer to the numbered assumption registry in the notebook ("Registro de supuestos", last updated 28-Oct-2026).

---

## 2. Forecast grain

Production forecasts at:

```text
product × warehouse
series_id = product_warehouse_id
```

In the tenant schema this is `product_warehouses.id`, and it is the column every downstream table points at (`forecasts.product_warehouse_id`, `stock_balances.product_warehouse_id`, `alerts.product_warehouse_id`).

**The notebook evaluates at product level, not product × warehouse.** `NIVEL_DEMANDA = "producto"`, per assumption S4:

> No se unen bodegas entre archivos; la demanda se mide por producto.
> Evidencia: 0 bodegas en común. Empresa: bodegas renombradas en 2026; no existe tabla de equivalencias.
> Estado: **Confirmado por la empresa**.

This is a limitation of the legacy World Office export, where warehouse identity is not continuous across the 2023–2025 and 2026 files, not a modelling preference. The new PANGO schema has a stable `product_warehouses` table, so the production grain is viable.

**Consequence for the shared domain:** `series_id` must stay **opaque**. No domain module may parse it, split it or assume it encodes a warehouse. That is what allows one implementation to serve both grains, and it is why the invariant above still holds even though the two routes run at different levels of aggregation.

### Decided: production forecasts each product-warehouse series directly

Route A proposed a hybrid scheme — model at product grain over the 195 weeks of both exports, then disaggregate to product × warehouse using 2026 shares with shrinkage toward the city or category share.

**That disaggregation will not be built.** Production treats each product × warehouse series on its own, which is what `architecture.md` §4 already required:

> There is no global-product forecast followed by allocation.

The consequences, recorded so that nobody has to rediscover them:

- Route B needs **no cross-sectional aggregation and no allocation**. Temporal aggregation (daily to weekly, weekly to monthly) stays ours; summing across warehouses does not exist.
- The contract keeps a **flat `series[]`**. Grouping by product was only needed so the service could verify it had every warehouse of a product before summing; with independent series, a batch split across calls is harmless.
- **Sparse series are not abandoned.** The policy's `INSUFFICIENT` rule routes them to `pooled_rate` with `group: grupo2`, which borrows a rate from the category at the model level. That is the mechanism that covers what allocation would have covered.
- A **non-stock warehouse** has no product-level forecast to fall back on, so it simply returns no forecast with `reason = non_stock_warehouse`.

What this does **not** settle is how Route A evaluates. Warehouse identity is not continuous across the two exports (S4), so a product × warehouse series exists only in the 2026 file: about **39 weeks**, against 195 at product grain. Thirteen evaluation blocks of four weeks need 52 weeks, so they do not fit, and Route A's proposed first cutoff of 2025-09-28 is earlier than that file begins. Choosing the evaluation grain is Route A's call per tenant; it does not change production.

**Consequence for the golden test:** parity can be compared at whichever grain Route A evaluates, because the domain is grain-agnostic. What must match is the transformation code and the parameter fingerprint of §11, not the level of aggregation.

---

## 3. Week definition

```text
Monday → Sunday, labelled by the Monday that starts it
```

Notebook: `fecha.dt.to_period("W-SUN").dt.start_time`, and the exported frequency is `freq = "W-MON"`. The notebook warns explicitly against relying on an ambiguous default `W-MON` resampling.

Grounded in 7.0, which measured which days the company actually invoices:

| Day | Share of units |
|---|---|
| Monday | 14.3 % |
| Tuesday | 19.3 % |
| Wednesday | 18.7 % |
| Thursday | 17.7 % |
| Friday | 18.2 % |
| Saturday | 11.8 % |
| Sunday | 0.0 % (23 rows in four years) |

A Monday–Friday week would discard the 11.8 % of demand invoiced on Saturdays. The low Monday is explained in 11.3 by public holidays moved to Mondays.

**Implemented:** `week_start()` in `pango_prediction/domain/series.py`. This is the single definition of the week; availability and calendar consume it instead of recomputing it.

---

## 4. Demand semantics

### Demand is gross, and never negative

Only sales count as demand (S1, confirmed by the company and by data). Customer returns are **not** subtracted.

Notebook 5.3 B measured the alternative: returns are 2.0 % of gross demand (4,904 of 242,150.5 units), stable per year between 1.5 % and 2.6 %. Using net demand would move the level by about 2 % and would produce 38 series with demand ≤ 0, which is invalid for the Negative Binomial likelihood (S18).

> **Decisión:** la demanda se modela con la bruta (fv); la neta se reporta como sensibilidad.

**Consequence:** a negative demand quantity is a contract violation, not data to accumulate. `build_weekly_series` raises on it.

### Demand is an integer count

Of 65,333 sales rows, exactly **one** has a decimal quantity (0.5), in one product. The notebook rounds that single value up in the model series and leaves the original grid untouched. This is what justifies the Negative Binomial likelihood (S18).

### Who applies business semantics

Pango Core decides which movements are demand before calling this service. In the tenant schema this is explicit:

```sql
movement_types.counts_as_demand boolean NOT NULL DEFAULT false
```

The Forecast Service must not know document types. The `FV / DMC / REM / SA` rules in `contract-v1.md` §9 are World Office specifics of one tenant's history; the schema generalises them through the column above.

### Time zone

Demand arrives as a calendar date, so Pango Core must convert timestamps before sending. The authority is `settings.time_zone` (default `America/Bogota`). A movement stored as `occurred_at timestamptz` can otherwise land in the wrong week.

---

## 5. Series construction

The model series (7.5):

```text
starts at the first week with positive demand   (prim)
ends at the last complete Monday-Sunday week    (fin_completa)
every week in between exists, zero-filled
```

**Start at the first sale.** The notebook's raw grid started at each product's first *movement*, which produced 99,205 weeks (14.7 % of the grid) before the first sale, 74,173 of them with no stock or no stock data. Section 7.3 concludes: *"La serie del modelo debería empezar en la primera venta."*

**End at the last complete week.** Partial weeks are flagged (`semana_parcial`) and excluded. Both ends of the notebook's dataset were partial (2 of 7 days each). A week measured over fewer than seven days understates demand.

**Fill the tail.** The grid runs to the end of the history, not to the last week with demand. Without the tail, inactivity is invisible to the profile.

**Implemented:** `build_weekly_series(series, history_end)` in `pango_prediction/domain/series.py`.

### Operational note on `history_end`

`history_end` should be a **Sunday**. If it falls mid-week, the final partial week is dropped, and the demand inside it is discarded — the notebook's behaviour, but silent. Observations dated after `history_end` are rejected rather than dropped, so that a mismatch surfaces instead of quietly changing the forecast.

---

## 6. Availability

Availability is derived from stock history, never received as a resolved backend flag.

### Weekly closing stock (7.2)

```text
daily closing   = last observation of that day
weekly closing  = closing of the latest day with data inside the week
week with no observation = carry the previous balance forward (ffill)
negative balance = nothing available (clip to 0)
```

Stock is a level, not a flow: a week's stock is never a sum. The notebook chose the **closing** balance rather than the weekly minimum because the two give nearly the same answer (62.7 % vs 63.6 % of weeks at zero), and S10 confirms the convention:

> Un saldo ≤ 0 no implica ausencia de venta; el stock se mide al cierre.

### States

| State | Rule | `available_mask` |
|---|---|---|
| `AVAILABLE` | Sold, or did not sell while stock was on hand | 1 |
| `CENSORED` | No sales and zero closing stock, in an episode of at most 13 weeks | 0 |
| `OUT_OF_ASSORTMENT` | The same, in an episode longer than 13 weeks | 0 |
| `UNKNOWN` | Availability undetermined: no reliable stock data, outside the selling window, or series not evaluable | 1 |

`OUT_OF_ASSORTMENT` is the notebook's `sin_surtido`. It is **not** an independent state: it is a refinement of a stockout, decided by the length of the consecutive episode the week belongs to.

```python
q["quiebre"]       = base & q["u_venta"].eq(0) & q["stock_cierre"].le(TOL)
q["largo_ep"]      = q.groupby(["producto", "ep"])["quiebre"].transform("sum").where(q["quiebre"])
q["sin_surtido"]   = q["quiebre"] & q["largo_ep"].gt(LARGO_MAX)
q["quiebre_corto"] = q["quiebre"] & ~q["sin_surtido"]
```

### Partial censoring stays observed (S25)

A week that sold and closed at zero is **observed**, not censored: the product was demonstrably available. 4,194 such weeks. The known cost is that those weeks' demand is understated, which the notebook records as a limitation.

### The evaluable window

Censoring is only evaluated for weeks that are:

- in a series classified `con_stock` (see S24 below), **and**
- between the product's first and last sale.

Weeks after the last sale are **inactivity, not censoring**, and are left to the demand profile. This is why a stockout that runs to the end of the history is not detected — limitation **S37**.

### Episodes are broken by any non-stockout week

Including an `UNKNOWN` week. In the notebook `quiebre` is a per-week boolean and episodes are runs of `True`.

### Validation of the rule (7.3 B)

The rule was not assumed, it was tested:

- 85 % of short stockout episodes end with a purchase within 2 weeks, against 27.2 % for weeks with stock on hand (control sample). The gap holds inside every segment.
- Short stockouts are not concentrated in the semiannual inventory-adjustment months (19.1 % vs 17.8 % of weeks), so they are not paperwork artefacts (S21).
- Sensitivity: using the weekly minimum gives 8.4 % more stockouts; using "any warehouse at zero" gives 16.2 % more.

### Observed distribution

| Measure | Value |
|---|---|
| Evaluable weeks | 161,805 |
| Short stockouts (`quiebre_corto`) | 8,436 (5.2 %) |
| Out of assortment (`sin_surtido`) | 17,123 weeks in 516 episodes, median 24 weeks |
| Partial censoring | 4,194 |
| Masked in the final model series | 25,559 (4.5 %) |

**Implemented:** `build_weekly_availability(series, stock)` in `pango_prediction/domain/availability.py`.

### Stock handling — made-to-order (S24)

The notebook **does** define the made-to-order rule, derived from history rather than received as a flag:

```python
UMBRAL_PEDIDO = 0.5   # >=50 % of weeks WITH sales opened with zero stock -> bajo_pedido
MIN_SEM_DATO  = 4     # fewer weeks with stock data -> cannot classify
```

| Class | Products | Share of units |
|---|---|---|
| `con_stock` | 1,215 | 88.2 % |
| `bajo_pedido` (made to order) | 332 | 4.5 % |
| `sin_dato` (not classifiable) | 3,218 | 7.3 % |

Moving the threshold to 25 % or 75 % only moves 3.7 % of units. A `bajo_pedido` or `sin_dato` series is **not evaluated for censoring** at all.

The opening balance of a week is the previous week's closing — the notebook's `shift()` on the stock column. Only weeks that both sold and have a known opening balance count towards the decision, and `sin_dato` takes precedence over `bajo_pedido`, because the notebook applies that assignment last.

Where a stock-managed series would be `CENSORED`, a `bajo_pedido` or `sin_dato` series is `UNKNOWN` instead: zero stock is either the product's normal operating state or a situation that cannot be read, so the week says nothing about demand. The mask is unaffected, which keeps parity with the notebook.

**Consequence for the contract:** `contract-v1.md` carries `metadata.made_to_order` as a boolean sent by Pango Core. The tenant schema has **no such column** on `products` or `product_warehouses`, and the notebook computes it from history. The field should be removed from the contract.

**Implemented:** `classify_stock_handling(series, stock)` in `pango_prediction/domain/availability.py`, and exposed on the result as `WeeklyAvailability.stock_handling` because the demand profile needs it too.

---

## 7. Calendar

One known-future covariate summarises holidays and closures: **equivalent business days per week** (`dias_habiles`).

```text
Monday-Friday    = 1.0
Saturday         = 0.6
Sunday           = 0.0
public holiday   = 0.0
Holy Saturday    = 0.0
1-6 January      = 0.0
closed date      = 0.0
```

A full week is therefore **5.6**.

- **Saturday 0.6** comes from 11.3: Saturday sells about 60 % of a weekday.
- **Holy Saturday** is not an official holiday in Colombia, but the company does not invoice (11.2 B). Dates confirmed in the data: 16-Apr-2022, 08-Apr-2023, 30-Mar-2024, 19-Apr-2025, 04-Apr-2026.
- **Public holidays:** the company invoiced on 0 of 37 holiday Mondays. Weeks containing a holiday sell 23.8 % less, and the lost sales are not recovered later.
- **1–6 January** is the year-start closure. Observed every year in the data (invoicing stops around 30-Dec to 6-Jan) and consistent with the semiannual physical inventory the company confirmed (S21). The notebook flags the future-year rule as *"a confirmar con la empresa"*.

Correlation of `dias_habiles` with total weekly sales: Spearman ρ = 0.41, p < 0.0001.

Distribution over the weeks of history: 134 complete (5.6), 51 with one holiday (4.6), and the rest reduced by Easter week and the year-start closure, down to 0.

The variable must be computable for **future** weeks, since DeepAR consumes it as a known covariate (S34). The notebook exported it through the end of 2027 (66 weeks, 24 of them below 5.6).

### Implemented as a rule plus data

`domain/calendar.py` owns the **weight rule**: each weekday carries a weight, a non-operational date carries none, and a week is the sum. **Which dates are non-operational** is tenant data and arrives with the request, per warehouse. The service imports no holiday package and knows no country.

The whole weekday table is a parameter (`DomainParameters.weekday_weights`), not a Saturday weight with the rest hardcoded, because a tenant that trades on Sundays needs a different table and not a different number. A full week is the sum of the table, derived rather than stored, so the two cannot disagree about what 5.6 means.

### Would a country code be enough?

Evaluated, and no - though it is worth having for a different job. A country answers only one of the four reasons the notebook gave a day zero weight:

| Source of a closed day | Does a country code give it? |
|---|---|
| Public holidays (`co`) | **Yes** |
| Year-start closure (`cierre`) | No. Company-specific; the notebook had to learn it from the data |
| Holy Saturday | No. Explicitly not an official Colombian holiday (11.2 B) |
| Differences between warehouses | No. Route A's own reply asks for `closed_dates` per `warehouse_id` |

What it misses is also what matters most: a public holiday takes a week from 5.6 to 4.6, while the year-start closure takes it to 0. Three further costs:

1. **Sub-national holidays.** Colombia's are national, so a country code happens to work for this tenant. Spain, Mexico or the United States need a subdivision, and warehouses in different regions need different calendars.
2. **Parity becomes a dependency.** If the service derives dates from a package, Route A must use the same package, the same version and the same subdivision flags; any drift changes the covariate silently. With dates as data, parity is "did both read the same list", auditable with a hash like `params_version`.
3. **Holiday law changes.** A pinned package is reproducible but ages; an unpinned one breaks parity.

**Where a country code does belong:** onboarding. A new tenant has no configured calendar - §11 notes that cold start is the normal state - so `country` plus `subdivision` can *draft* its closed-date list, which the company then corrects by adding its own shutdown and removing days it does work. The forecast service reads the final list. Drafting a calendar and reading one are different jobs.

### An asymmetry in the notebook's covariate

The notebook derived `cierre` from history: business days with no sales between 26 December and 10 January, over 2022-2025. That cannot be evaluated for a future year, so `x in cierre` is false for every horizon week. Its future year-ends therefore scored a full week while its historical ones scored nearly nothing - a skew in a variable DeepAR consumes as *known*.

Route B's value depends only on the week and the supplied dates, so history and horizon are computed by one rule. Matching the notebook on history then requires Core to be seeded with the dates `cierre` contained, which is why **that learned set should be exported from the notebook** rather than re-derived.

---

## 8. Demand profile and classification

### Classification uses the MONTHLY series, not the weekly one (S27)

This is the least obvious rule in the whole analysis, and skipping it would change every model assignment.

| Level | Periods with zero sales | Median ADI | Units in "smooth" series |
|---|---|---|---|
| Week × product | 91.9 % | 37.5 | 1.2 % |
| Month × product | 79.0 % | 9.5 | 49.8 % |

At weekly grain almost nothing looks like stable demand. Aggregated monthly, half the demand becomes smooth. The notebook therefore classifies on the monthly series while forecasting on the weekly one, and evaluates on the 4-week total.

A month counts as unobservable when half or more of its days fall in a stockout; 4.4 % of months.

Classification window: **24 months from each series' first month of sale**,
recomputed at every cutoff (S30, A5).

### Quadrants — Syntetos & Boylan

```text
ADI cutoff = 1.32      CV² cutoff = 0.49
```

| Notebook class | ADI | CV² | Proposed enum value |
|---|---|---|---|
| `suave` | < 1.32 | < 0.49 | `SMOOTH` |
| `errática` | < 1.32 | ≥ 0.49 | `ERRATIC` |
| `intermitente` | ≥ 1.32 | < 0.49 | `INTERMITTENT` |
| `irregular` | ≥ 1.32 | ≥ 0.49 | `LUMPY` |
| `insuficiente` | not computable | | `INSUFFICIENT` |

There are **five** classes, not four. `insuficiente` is not an edge case to ignore: 44 % of products sold in a single period and cannot be classified (3.8 % of units).

The enum names in the right-hand column are a **Route B proposal, not a decision**. `forecasts.demand_class` is a per-tenant enum that has not been created yet, so Route B gets to propose its values. `LUMPY` is the standard Syntetos & Boylan term for the high-ADI, high-CV² quadrant that the notebook calls `irregular`.

### History and activity

```python
NUEVO_MAX      = 13   # E5: new                      (<= 13 weeks of history)
LIMITADA_MAX   = 52   # E5: limited history          (14-52 weeks)
                      #     mature                   (> 52 weeks)
VENTANA_ACTIVO = 26   # inactive if no sale in the last 26 weeks (~6 months)
MIN_OBS        = 8    # E1: 2 x H observed weeks to forecast individually
```

With 8 observed weeks, 99.8 % of units are evaluable. The notebook's conclusion is worth keeping in mind: *"La longitud de la historia no limita el modelado: lo limita la escasez de ventas."*

### Seasonal rescue (7.5 C)

Because the data ends in September, a product that only sells in October–December would look inactive right before its season. Rule: an inactive product that sold in Oct–Dec in **at least 2 years** and makes **≥ 50 %** of its sales in that quarter is reclassified as active, flagged `temporada`. Only 70 of 3,092 inactive products qualify (1.0 % of last year's demand), which is why the 26-week rule is kept.

### Fields the profile must carry

`DemandProfile` carries `demand_class`, `history_class`, `stock_handling`, `adi`, `cv2`, `observable_months`, `nonzero_months`, `history_weeks`, `observed_weeks`, `weeks_since_last_sale`, `inactive` and `evaluable`, computed by `domain/profile.py`.

Four of the five routing dimensions of §9 are therefore available: activity, history class, monthly quadrant and stock handling. The fifth is still missing:

- `seasonal` — the `temporada` flag of the seasonal rescue (7.5 C). Not implemented, and not only for want of time: the rule hardcodes October-December, which is a calibration of one Colombian catalogue rather than a platform rule. It needs a tenant-scoped quarter before it can be written.

Two classes answer the two different ways classification fails, which the earlier five-class proposal conflated:

| Class | Meaning | Notebook |
|---|---|---|
| `INSUFFICIENT` | There is a monthly series; its statistics cannot be computed from it | `insuficiente` |
| `NOT_CLASSIFIABLE` | There is no monthly series: every month was spent in a stockout | `sin serie mensual` |

Both values exist in Route A's `demand_class` enum. Route A confirmed this
mapping rather than introducing new enum values.

---

## 9. Model routing (input to the policy)

The notebook's `destino` is the decision the production policy has to represent. It depends on five dimensions — activity, history class, monthly quadrant, stock handling and seasonality — not on `demand_class` alone.

| Destination | Products | Share of last year's demand |
|---|---|---|
| Individual forecast | 1,329 | 91.9 % |
| Short history (DeepAR, learns from similar products) | 397 | 3.3 % |
| Made to order / aggregated by product group | 13 | 0.3 % |
| **No forecast (inactive)** | 3,022 | 4.6 % |

Monthly quadrants inside the individual-forecast group: 750 intermittent, 258 lumpy, 174 smooth, 77 erratic.

**Two implications for `model_policy.json`:**

1. The policy format sketched in `README.md` maps `demand_class → model`. That is not expressive enough.
2. There must be a **"no forecast"** outcome. Three quarters of the catalogue falls into it, and the current format cannot represent it.

Route A confirmed the v1 scope: `zero` is a reference only, the policy has no
`sold_to_order` rule, and the five no-forecast reasons are
`inactive`, `sold_to_order`, `non_stock_warehouse`, `no_active_warehouse` and
`history_only`. They are `NoForecastReason` in `domain/types.py`, and the policy
loader accepts no other value. Contract 1.2 returns a declined series with
status `no_forecast` and its reason; see `contract-v1.md` sections 3 and 14.
The service states only the reasons a policy rule names: the three that depend
on warehouse configuration wait for the `warehouses[]` request block.

---

## 10. Horizon and masking

### Horizon is derived, not chosen

```text
H = R + L
R = 19-24 days   (replenishment interval, measured from purchases)
L = 3-5 days     (lead time confirmed by the company, S19)
H = 22-29 days = 3.1-4.1 weeks  ->  horizon = 4 weeks
```

Four weeks covers the replenishment cycle of the products that concentrate demand, and equals a month. With this horizon, 37 of 4,765 products (0.8 %) have less than 4 weeks of history and do not enter DeepAR training.

### Masking convention (S28)

```python
available_mask = y_deepar.notna()       # 0 exactly on stockout weeks
y              = ceil(y_deepar.fillna(0))
```

A masked week carries **`y = 0` and `mask = 0`**, not `NaN`, because that is what NeuralForecast expects. The notebook asserts that no week with sales is ever masked.

In the domain, `available_mask` is a `bool`: the set of masked weeks is identical to the notebook's, but the 0/1 encoding belongs to the Nixtla adapter, not to the domain.

### Evaluation protocol (Route A)

```text
13 evaluation blocks of 4 weeks
blocks 1-7   selection
blocks 8-13  reporting  (used once, never for tuning)
expanding origin, never a random split
likelihood: NegativeBinomial
```

---

## 11. Parameter scope: platform rule or tenant calibration

PANGO is multi-tenant, so the numbers in this document are not all of one kind. Some are rules that hold everywhere; others are **calibrations of one company's behaviour**, and applying them to a different tenant would be wrong rather than merely imprecise.

The clearest example is the Saturday weight of 0.6. It exists because EquipMaster invoices 11.8 % of its units on Saturdays. A tenant closed on Saturdays needs 0, and one that trades on Sundays needs a different table altogether.

| Kind | Examples | Why |
|---|---|---|
| **Platform rule** | Monday-start week, gross non-negative demand, the four availability states, the episode logic, the ADI and CV² cutoffs of 1.32 and 0.49, the float tolerance | Semantics or published literature, not fitted to any catalogue |
| **Tenant calibration** | stockout threshold, made-to-order thresholds, inactivity window, history classes, minimum observed weeks, Saturday weight, year-start closure, public holidays, horizon | Derived from one company's replenishment interval, catalogue, operating days or country |

### Where the calibration lives

`pango_prediction/domain/parameters.py` holds a frozen `DomainParameters`. Its defaults are EquipMaster's values, used as the platform starting point for a tenant that has not been calibrated yet — not a claim that they hold everywhere.

The parameters travel with the versioned policy artifact, which Route A defined as `model_policy.json` plus hyperparameters plus **a hash of the domain parameters**, surfaced as `params_version`. That hash is what makes parity auditable per tenant: it proves the evaluation run and the production run used the same calibration.

`DomainParameters.fingerprint()` computes it, with a recipe simple enough for both routes to reproduce:

```text
the fields as JSON, keys sorted, no whitespace -> SHA-256 -> first 16 hex characters
```

For the current defaults that is `f5f416da285217b5`. **Both routes must agree on this recipe**; if Route A hashes differently, the two fingerprints will never match and the audit is worthless.

### Parameters still to join it

Only the three the code reads today are fields. The rest join `DomainParameters` as the module that reads them is built, and the policy artifact should plan for them:

`profile.py` added four of them plus the classification window, so eight of the calibrated values are now fields. What is left:

`profile.py` and `calendar.py` absorbed the rest. Two entries left, and neither is simply pending:

| Parameter | Value | Source | Status |
|---|---|---|---|
| Seasonal rescue quarter | Oct-Dec, 2+ years, 50 %+ | 7.5 C | Needs a tenant-scoped quarter before the rule can be written at all |
| Horizon | 4 weeks | 8.2 | Already carried by the policy as `horizon_weeks` |

The year-start closure and the public holidays left this table rather than joining it: they are **dates**, which are request data, not calibration. Only the weight rule is calibration.

### Cold start is the normal state for a new tenant

Every tenant begins with no demand history, no stock history — `stock_balances` keeps no history, so movements only start at onboarding — and no policy. That is not an edge case: it is what every new customer sees first.

The service already answers sensibly: a series with no observed demand gives `INSUFFICIENT_HISTORY`, fewer than four selling weeks with stock data gives `NOT_CLASSIFIABLE`, and no policy gives `MODEL_NOT_AVAILABLE`. That behaviour is currently incidental rather than designed, and it deserves its own tested path.

One gap this opens: `pooled_rate` borrows a rate from the category, which needs history **in the category**. A tenant created yesterday has none anywhere, which is deeper than the cold start Route A's `demand_class: NOT_CLASSIFIABLE` describes (Core cannot reconstruct stock yet).

---

## 12. Parameter registry

Scope is from §11: a platform rule holds for every tenant, a calibration does not.

| Parameter | Value | Source | Scope | Implemented |
|---|---|---|---|---|
| Week start | Monday | 7.0, 7.1 | platform | `week_start()` |
| Frequency | weekly (`W-MON`) | 13.2 | platform | implicit |
| Stock tolerance | `1e-6` | notebook `TOL` | platform | `STOCK_TOLERANCE` |
| ADI cutoff | 1.32 | Syntetos & Boylan, 9.1 | platform | `ADI_CUTOFF` |
| CV² cutoff | 0.49 | Syntetos & Boylan, 9.1 | platform | `CV2_CUTOFF` |
| Classification grain | monthly | S27 | platform | `build_monthly_series()` |
| Max stockout episode | 13 weeks | S26 | tenant | `DomainParameters.max_stockout_episode_weeks` |
| Made-to-order threshold | 0.5 | S24 | tenant | `DomainParameters.made_to_order_threshold` |
| Min weeks with stock data | 4 | S24 | tenant | `DomainParameters.min_weeks_with_stock_data` |
| Horizon | 4 weeks | 8.2 | tenant | the policy's `horizon` |
| Inactivity window | 26 weeks | S29 | tenant | `DomainParameters.inactivity_weeks` |
| New product | ≤ 13 weeks | E5 | tenant | `DomainParameters.new_history_max_weeks` |
| Limited history | 14–52 weeks | E5 | tenant | `DomainParameters.limited_history_max_weeks` |
| Min observed weeks to forecast | 8 (= 2 × H) | E1 | tenant | `DomainParameters.min_observed_weeks` |
| Classification window | 24 months | S30, A5 | tenant | `DomainParameters.classification_window_months`, default `24` |
| Weekday weight table | Mon-Fri 1.0, Sat 0.6, Sun 0 | 11.3, 11.4 | tenant | `DomainParameters.weekday_weights` |
| Full week business days | 5.6 | 11.4 | derived | `full_week_business_days()`, the sum of the table |
| Year-start closure | 30 Dec - 6 Jan | S34 | tenant **data** | arrives as `closed_dates`; see §7 |
| Public holidays | per tenant and warehouse | S34 | tenant **data** | arrives as `closed_dates`; see §7 |
| Seasonal rescue | 2+ years and 50 %+ in Oct-Dec | 7.5 C | tenant | not yet, and needs a tenant-scoped quarter |
| Interval quantiles | 0.10 / 0.90 | Route A | tenant | `DEFAULT_QUANTILES` in `domain/intervals.py`; not yet a policy field |
| Evaluation blocks | 13 (1–7 selection, 8–13 report) | 13.2 | tenant | Route A only |
| Likelihood | NegativeBinomial | S18 | tenant | Route A only |

Every parameter marked `tenant` that is not yet a field of `DomainParameters` is a value currently written into code or into this document as though it were universal. Each one has to move into the calibration before a second tenant is onboarded.

---

## 13. Open decisions

1. **Who computes public holidays — reopened by multi-tenancy.** Route A's answer was that the service computes Colombian holidays with a version-pinned `holidays` package. That does not survive a second tenant: the service would need the tenant's country, and the schema has none — only `settings.time_zone` and a free-text `warehouses.address`. A tenant in Mexico or Spain would be served Colombian holidays.

   The resolution is to split the two things that were conflated. The **weight table** (Monday–Friday 1.0, Saturday per tenant, Sunday 0, closed 0) is a domain rule and stays in shared code. **Which dates are closed** is tenant data and comes in the request. Parity is preserved by sharing the rule, not by sharing the holiday computation, so Route A's notebook reads the same closed-date list Core sends.

   *Decided and implemented.* Core sends every non-operational date; the service takes no holiday dependency. A country code was evaluated as an alternative and rejected for the forecast path, because it covers only public holidays and misses the company closure, Holy Saturday and per-warehouse differences; it is useful instead for drafting a new tenant's calendar at onboarding. The full evaluation is in §7.
2. **Year-start closure for future years.** The notebook assumed 1-6 January; Route A's reply says 30 December - 6 January. Immaterial to the service now that Core sends the dates, but the company still has to confirm the rule so Core can generate them. Separately, the notebook's learned `cierre` set should be exported so Core's history matches what was evaluated; see §7.
3. **`demand_class` and `model` enum values.** Per-tenant enums, not yet created. §8 carries a proposal.
4. **What happens to the `INSUFFICIENT` class in production.** 44 % of products, 3.8 % of units.
5. **Policy format.** Must express the five-dimension routing of §9 and a "no forecast" outcome. The outcome exists since contract 1.2: a rule states a `reason` from the closed vocabulary instead of a `model`. The `seasonal` dimension is still not expressible.
6. **`UNKNOWN` and the mask.** Resolved in favour of the notebook (`mask = 1`, treated as observed) to preserve parity. Revisit only together with Route A.
7. ~~**Prediction intervals.**~~ Closed. `ForecastPeriod` carries `lower` and `upper`, validated to contain the estimate, and `domain/intervals.py` computes them from empirical residual quantiles at Route A's 0.10 / 0.90.
8. **The classification window.** Route A confirmed 24 months (S30, A5).
9. ~~**`INSUFFICIENT` against `NOT_CLASSIFIABLE`.**~~ Closed. Route A confirmed the mapping of §8 on 14 November.
10. ~~**`zero` is not in Route A's model enum.**~~ Closed. Route A confirmed on 14 November that `zero` is a reference only, not a candidate or a policy model in v1. An inactive series is declined with the reason `inactive` instead of being forecast as zero.

---

## 14. Discrepancies with the other documents

Listed rather than silently fixed, per the source-of-truth rule in `AGENTS.md`.

| # | Discrepancy | Assessment |
|---|---|---|
| 1 | `architecture.md` §7 and the two-route plan describe `pango-forecast-service/` with `src/pango_forecast/` and `api/`, `models/`, `artifacts/`. The repository is `pango-prediction/` with a hexagonal layout. | Stale documentation. The live repository is authoritative. Since 14 November `architecture.md` opens with a note saying so; its body is unchanged. |
| 2 | `README.md` shows the hexagonal layout in "Repository Structure" but `src/pango_forecast/models/` under "Models". | **Resolved** on 14 November. Both sections now describe the live repository, and the roadmap marks what is built. |
| 3 | `README.md` development setup says `uv sync`, `uv run pytest`, `uv run mypy src`. `uv` is not installed and `src/` does not exist. | Resolved: `uv` is not used. `README.md` and `AGENTS.md` share one `pip` and virtual-environment setup and the same checks. |
| 4 | `contract-v1.md` §11 lists four availability states including `SIN_SURTIDO`; `AGENTS.md` and `PREDICTION_CONTEXT.md` list three. | Resolved: four states, see §6. Identifier anglicised to `OUT_OF_ASSORTMENT`. |
| 5 | `contract-v1.md` §8 carried `metadata.made_to_order`. No such column exists, and the notebook derives it. | **Resolved.** Removed from the contract in 1.1, and the service rejects the field rather than ignoring it. |
| 6 | `contract-v1.md` §13 listed only Mon-Fri / Sat / Sun / closed dates. Holy Saturday and the year-start closure were missing. | **Resolved.** §13 now states that Core must send *every* non-operational date and names those two, and the weight table became tenant calibration. |
| 7 | `contract-v1.md` §9 encodes World Office document types. The schema generalises this via `movement_types.counts_as_demand`. | Contract should reference the column. |
| 8 | `contract-v1.md` §10 assumes Pango Core can send daily closing stock. `stock_balances` holds current state only — there is no stock history table. | Real integration gap: Core must reconstruct daily stock by replaying `movements` and `movement_lines`. |
| 9 | Documents described 4 demand classes; the notebook produces 5, and the two ways classification fails need 6. | **Resolved.** `DemandClass` has the six values of Route A's enum; see §8. |
| 10 | `forecasts` and `forecast_runs` have no column for `policy_version` or `model_version`, which `contract-v1.md` §14 returns and `architecture.md` §26 requires for traceability. | Schema gap to raise with Pango Core. |
| 11 | Notebook cell 164 (a narrative cell) reports 2,222 products and 18.4 % stockouts, contradicting its own output and cell 166 (1,215 products, 5.2 %). | Stale narrative inside the notebook. The executed outputs and cell 166 are authoritative. |
| 12 | The notebook's parameter block and Route A decision record fix `ventana_cuadrante_meses` at 24. | **Resolved.** Route B uses `classification_window_months=24`; the resulting parameter fingerprint is `f5f416da285217b5`. |
| 13 | `contract-v1.md` §14's profile block had `observed_periods`, `nonzero_periods` and `short_history`, conflating the monthly classification grain with the weekly forecast grain and reducing three history classes to a boolean. | **Resolved.** 1.1 returns the implemented `DemandProfile`, with the two grains counted separately. |
| 14 | `contract-v1.md` showed string identifiers and `frequency: "weekly"`; the tenant schema has integers and `WEEKLY`. | **Resolved** in 1.1. |
| 15 | `contract-v1.md` §3 and §8 say that sending `metadata.made_to_order` fails validation. It does not: `metadata` is free-form, so the key is accepted there and never read. Only `made_to_order` at series level is refused, as an unknown field. Checked on 14 November by posting the minimal fixture with the key inside `metadata`: `200`. | **Resolved** on 16 November, by the owner's decision: the schema refuses the key inside `metadata` with `422`, and the contract says so again. Every other key of `metadata` stays free-form. |
| 16 | `contract-v1.md` §7 gives the supported `frequency` as `weekly`, while its own corrections in §3 and the schema say `WEEKLY`. | Stale line in the contract. Left for the branch that next edits §7. |

---

## 15. Limitations inherited from Route A

These are the notebook's own caveats (13.4), and they bound what production can claim.

- **Few years.** With three complete years, monthly seasonality cannot be confirmed (Friedman p = 0.29).
- **Censoring.** Weeks that ran out mid-week have understated demand (S25), and stockouts at the end of a series are not detected at all (S37).
- **Warehouse discontinuity.** Warehouses cannot be joined across the two source files (S4); stock is measured only in the main warehouses (S15).
- **Valuation at cost.** The ledger has no sale prices, so ABC and inventory value are at cost (S36).
- **Thresholds are reasoned, not exact.** Made-to-order (50 %), inactivity (26 weeks) and out-of-assortment (13 weeks) are decisions with reported sensitivity, not laws.

---

## 16. Provenance

Every rule here was read from the executed outputs of `docs/retail-inventory-forecasting-demand-analysis.ipynb` (255 cells) and cross-checked against the tenant database schema. Where the notebook's narrative text disagreed with its own computed output, the output was used.

When a rule changes, it must change in the same commit across: this file, the domain implementation, the domain tests, and the contract version if the change is observable from outside.
