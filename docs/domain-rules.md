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

**Consequence for the golden test:** parity between the two routes can only be compared at product grain, using the notebook's aggregation.

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

**Implemented:** `build_weekly_availability(series, stock)` in `pango_prediction/domain/availability.py`, except the S24 gate below.

### Stock handling — made-to-order (S24) — NOT YET IMPLEMENTED

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

**Two consequences:**

1. `contract-v1.md` carries `metadata.made_to_order` as a boolean sent by Pango Core. The tenant schema has **no such column** on `products` or `product_warehouses`, and the notebook computes it from history. The field should be removed from the contract.
2. Until this gate exists in code, production treats every series as `con_stock`, so a made-to-order series will be labelled `CENSORED` where the notebook would leave it unevaluated.

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

**Not yet implemented.** See the open decision in §12 about who computes Colombian public holidays.

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

Classification window: the 24 months before the cutoff, recomputed at every cutoff.

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

The current `DemandProfile` has `observed_periods`, `nonzero_periods`, `adi`, `cv2`, `demand_class`, `inactive`, `short_history`. The routing in §9 additionally needs:

- `stock_handling` — the S24 class (`con_stock` / `bajo_pedido` / `sin_dato`)
- `seasonal` — the `temporada` flag

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

This is Route A's decision to finalise. Route B only loads and executes it.

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

## 11. Parameter registry

| Parameter | Value | Source | Implemented |
|---|---|---|---|
| Week start | Monday | 7.0, 7.1 | `week_start()` |
| Frequency | weekly (`W-MON`) | 13.2 | implicit |
| Horizon | 4 weeks | 8.2 | not yet |
| Stock tolerance | `1e-6` | notebook `TOL` | `STOCK_TOLERANCE` |
| Max stockout episode | 13 weeks | S26 | `MAX_STOCKOUT_EPISODE_WEEKS` |
| Made-to-order threshold | 0.5 | S24 | not yet |
| Min weeks with stock data | 4 | S24 | not yet |
| Inactivity window | 26 weeks | 7.5 | not yet |
| New product | ≤ 13 weeks | E5 | not yet |
| Limited history | 14–52 weeks | E5 | not yet |
| Min observed weeks to forecast | 8 (= 2 × H) | E1 | not yet |
| ADI cutoff | 1.32 | Syntetos & Boylan, 9.1 | not yet |
| CV² cutoff | 0.49 | Syntetos & Boylan, 9.1 | not yet |
| Classification grain | monthly | S27 | not yet |
| Classification window | 24 months | 13.2 | not yet |
| Saturday weight | 0.6 | 11.3, 11.4 | not yet |
| Full week business days | 5.6 | 11.4 | not yet |
| Year-start closure | 1–6 January | 11.2, 11.4 | not yet |
| Seasonal rescue | ≥ 2 years and ≥ 50 % in Oct–Dec | 7.5 C | not yet |
| Evaluation blocks | 13 (1–7 selection, 8–13 report) | 13.2 | Route A only |
| Likelihood | NegativeBinomial | S18 | Route A only |

---

## 12. Open decisions

1. **Who computes Colombian public holidays.** The notebook used the `holidays` package. Options: Pango Core sends every non-operational date in `calendar.closed_dates` (keeps the domain dependency-free, and the contract already has the field), or the service takes on the dependency. *Recommended:* the former.
2. **Year-start closure for future years.** The notebook assumes 1–6 January and marks it as pending confirmation by the company.
3. **`demand_class` and `model` enum values.** Per-tenant enums, not yet created. §8 carries a proposal.
4. **What happens to the `INSUFFICIENT` class in production.** 44 % of products, 3.8 % of units.
5. **Policy format.** Must express the five-dimension routing of §9 and a "no forecast" outcome.
6. **`UNKNOWN` and the mask.** Resolved in favour of the notebook (`mask = 1`, treated as observed) to preserve parity. Revisit only together with Route A.
7. **Prediction intervals.** `forecast_periods.lower_bound` and `upper_bound` are `NOT NULL`, so intervals are mandatory. `ForecastPeriod` does not carry them yet.

---

## 13. Discrepancies with the other documents

Listed rather than silently fixed, per the source-of-truth rule in `AGENTS.md`.

| # | Discrepancy | Assessment |
|---|---|---|
| 1 | `architecture.md` §7 and the two-route plan describe `pango-forecast-service/` with `src/pango_forecast/` and `api/`, `models/`, `artifacts/`. The repository is `pango-prediction/` with a hexagonal layout. | Stale documentation. The live repository is authoritative. |
| 2 | `README.md` shows the hexagonal layout in "Repository Structure" but `src/pango_forecast/models/` under "Models". | Internally inconsistent README. |
| 3 | `README.md` development setup says `uv sync`, `uv run pytest`, `uv run mypy src`. `uv` is not installed and `src/` does not exist. | Stale. `AGENTS.md` has the working commands. |
| 4 | `contract-v1.md` §11 lists four availability states including `SIN_SURTIDO`; `AGENTS.md` and `PREDICTION_CONTEXT.md` list three. | Resolved: four states, see §6. Identifier anglicised to `OUT_OF_ASSORTMENT`. |
| 5 | `contract-v1.md` §8 carries `metadata.made_to_order`. No such column exists, and the notebook derives it. | Remove from the contract. |
| 6 | `contract-v1.md` §13 lists only Mon–Fri / Sat / Sun / closed dates. Holy Saturday and the 1–6 January closure are missing. | Contract is incomplete. |
| 7 | `contract-v1.md` §9 encodes World Office document types. The schema generalises this via `movement_types.counts_as_demand`. | Contract should reference the column. |
| 8 | `contract-v1.md` §10 assumes Pango Core can send daily closing stock. `stock_balances` holds current state only — there is no stock history table. | Real integration gap: Core must reconstruct daily stock by replaying `movements` and `movement_lines`. |
| 9 | Documents describe 4 demand classes; the notebook produces 5. | See §8. |
| 10 | `forecasts` and `forecast_runs` have no column for `policy_version` or `model_version`, which `contract-v1.md` §14 returns and `architecture.md` §26 requires for traceability. | Schema gap to raise with Pango Core. |
| 11 | Notebook cell 164 (a narrative cell) reports 2,222 products and 18.4 % stockouts, contradicting its own output and cell 166 (1,215 products, 5.2 %). | Stale narrative inside the notebook. The executed outputs and cell 166 are authoritative. |

---

## 14. Limitations inherited from Route A

These are the notebook's own caveats (13.4), and they bound what production can claim.

- **Few years.** With three complete years, monthly seasonality cannot be confirmed (Friedman p = 0.29).
- **Censoring.** Weeks that ran out mid-week have understated demand (S25), and stockouts at the end of a series are not detected at all (S37).
- **Warehouse discontinuity.** Warehouses cannot be joined across the two source files (S4); stock is measured only in the main warehouses (S15).
- **Valuation at cost.** The ledger has no sale prices, so ABC and inventory value are at cost (S36).
- **Thresholds are reasoned, not exact.** Made-to-order (50 %), inactivity (26 weeks) and out-of-assortment (13 weeks) are decisions with reported sensitivity, not laws.

---

## 15. Provenance

Every rule here was read from the executed outputs of `docs/retail-inventory-forecasting-demand-analysis.ipynb` (255 cells) and cross-checked against the tenant database schema. Where the notebook's narrative text disagreed with its own computed output, the output was used.

When a rule changes, it must change in the same commit across: this file, the domain implementation, the domain tests, and the contract version if the change is observable from outside.
