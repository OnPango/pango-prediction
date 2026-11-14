# Product-to-warehouse mean allocation (A15)

**Date:** 2026-11-14 · **Branch:** `feature/route-a-allocation` → `dev` (from `4a2c8d0`)

## Goal
The top-down arm of T4 (Route A decision D1): forecast the product as a whole and split its mean across its warehouses. The single reporting run of 17-Nov compares it against forecasting each warehouse on its own, and that comparison is only valid if the allocation it evaluates is the code that ships. A15 fixes the minimal scope: shares and the mean, no per-warehouse intervals, no connection to `application`.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `f8b1643` | `domain/allocation.py`, `tests/unit/domain/test_allocation.py` | Exposure, prior, shares and the split of the mean, as pure standard-library functions |
| `ef244c0` | `docs/worklog/2026-11-14-route-a-allocation.md` | This entry, as Route A sent it |
| Correct the worklog commit hash and date | this file | The hashes of the sent patches change when `git am` replays them, and the entry is dated the day its PR opens |

The module, step by step (Route A specification "Desagregación producto → bodega", steps 1–4):

| Function | Step |
|---|---|
| `allocation_window(cutoff, window_weeks)` | The complete weeks that end at the cutoff |
| `measure_exposure(window, first_movement, series, availability)` | Weeks the warehouse could sell (from its first movement, without `CENSORED` / `OUT_OF_ASSORTMENT`) and the units they sold. `WindowExposure.projected_units` projects them to the whole window |
| `build_prior(exposures, prior_strength)` | Company and group (grupo2) patterns from every series of the catalogue |
| `restrict_prior(prior, group, active_warehouses)` | The product's `prior_share` per active warehouse |
| `compute_warehouse_shares(exposures, prior_shares, prior_strength)` | `warehouse_share = (projected_units + prior_strength · prior_share) / (product_projected_units + prior_strength)` |
| `allocate_mean(product_estimates, warehouse_shares, stock_warehouses)` | `warehouse_share · product_estimate`; non-stock warehouses return their part as `non_stock_warehouse`; no active warehouse returns `no_active_warehouse` |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 376 passed (58 new) |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 57 files |
| Worked example of the specification (`prior_strength` 1.18 / 0 / 0.59 / 2.36, and very large) | Matches its published shares to 3 decimals |
| Against the reference implementation (EDA v2 `proporciones()`, cells 12 B/12 D), block-8 cutoff 12-Apr-2026, 8 weeks, `prior_strength` 1.18 | 3,071 warehouse-product rows of 1,765 products: same exposed weeks and units, shares within 2.2e-16. The reference reproduces `prior_strength` = 1.18 (719 degrees of freedom, 344 products) |

The reference check runs in Route A's workspace on the real ledger and is not part of this repository; it prints counts and differences only.

## Decisions
- **`prior_strength` (k) and the window length are arguments, not `DomainParameters` fields** (A14, criteria DEC-2): Route A freezes them in `freeze.json`. `parameters.py` is untouched, so the fingerprint does not change.
- **`first_movement` is an argument.** It cannot be derived from `RegularizedSeries`, whose grid starts at the first sale; the weeks between the two had the product on hand, so they count as exposed with zero units.
- **Edge cases follow the reference implementation**, which is where `prior_strength` was estimated: the prior uses only series with exposed weeks; a warehouse never exposed is imputed from the prior (zero when the observed warehouses carry no prior); fallbacks group → company → uniform; a product without projected units takes the prior; products without a group are pooled under `None`.
- **Guard against look-ahead:** `measure_exposure` rejects a series that does not end at the last window week, i.e. one built with data from after the cutoff.
- **The `NO_FORECAST` reasons are module constants** (`NON_STOCK_WAREHOUSE`, `NO_ACTIVE_WAREHOUSE`) until the strict policy loader defines the reason set; then they should come from there.

## Open questions
- Who supplies `first_movement` in production (Core or the World Office adapter): open, after 20-Nov.
- `active_warehouses` and `stock_warehouses` are inputs. In evaluation they come from Route A's data (warehouses with a 2026 movement up to the cutoff; S15). In production, warehouse configuration is administrator-managed: `holds_stock` belongs to the warehouse block and `history_only` is carried on each series.
- Per-warehouse intervals (simulated paths) remain out of scope (A15).

## Next step
Route B: review and merge before the `v0.2.0` tag. Route A: build the top-down arm in the evaluation harness with this module.
