# SBA model and horizon totals from real sums

**Date:** 2026-11-11 · **Branch:** `feature/sba-horizon-totals` → `dev` (built on `feature/policy_loader`, `9ae4b4f`, which is not merged yet)

## Goal
Two pieces Route A needs before the freeze of 16-Nov:

1. **Horizon total interval (D4).** `predict_total` scaled the weekly residuals by the horizon. That assumes the four weekly errors are identical and over-widens the interval for intermittent demand. The total interval now comes from real 4-week sums.
2. **SBA.** Route A's policy sends the `INTERMITTENT` and `LUMPY` quadrants to `sba`, which did not exist.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `56489d0` | `domain/intervals.py`, `tests/unit/domain/test_intervals.py` | `one_step_residuals`, `horizon_residuals`, `horizon_total_interval`, with a fallback below 8 horizon sums |
| `4d6324f` | `adapters/outbound/baselines.py`, `tests/unit/models/test_baselines.py` | `zero`, `naive`, `moving_average` totals read from horizon sums; weekly periods unchanged |
| `f8c12f0` | `adapters/outbound/croston.py`, `adapters/outbound/registry.py`, `tests/unit/models/test_sba.py`, `tests/unit/test_registry.py` | Plain-Python SBA, registered as `sba` |
| Add worklog for SBA and horizon totals | this file | — |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 304 passed |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 48 files |
| SBA vs StatsForecast 2.1.1 `CrostonSBA` (4 fixed + 500 random series, mean and in-sample fitted) | Max absolute difference 3.6e-15 |

Example of the change on a 40-week intermittent series (upper bound of the 4-week total): `zero` 16.0 → 7.0, `naive` 24.0 → 15.0, `moving_average(4)` 21.5 → 11.8, `sba` 13.7 → 5.0.

## Decisions
- **Recipe for the total.** At every origin `t` with a forecast, residual = `sum(y[t:t+h]) - h * f[t]`, overlapping windows, over the contracted observable series (S31). Interval = `h * f_T` + empirical quantiles 0.10 / 0.90 of those residuals, with `empirical_interval`'s existing rules (contains the estimate, lower bound ≥ 0). Point total `h * f_T` is unchanged.
- **Minimum of 8 horizon sums**, a platform constant (`MIN_HORIZON_RESIDUALS`), not a `DomainParameters` field, so the fingerprint does not change.
- **Fallback below 8 sums:** weekly residuals × horizon (the previous behaviour), declared as a deviation from D4 for short series. It is not always wider: when a model over-forecasts in sample, every residual is negative and the upper bound equals the estimate (the 9-week SBA reference shows it). Per warehouse it applies to almost every series in blocks 5–7 (3 to 11 weeks of 2026), since a total from sums needs 12 observable weeks (`naive`, `sba`) or 15 (`moving_average`, window 4). Coverage is reported separately for totals from sums and from the fallback.
- **SBA recipe = StatsForecast `CrostonSBA`:** size starts at the first sale, interval at its position + 1, one `alpha` for both (default 0.1, policy param `alpha`, 0 < alpha ≤ 1), forecast `(1 - alpha/2) * size / interval`. Plain Python: no new dependency.
- **SBA minimum: 2 weeks with a sale.** Always met by the `INTERMITTENT` and `LUMPY` quadrants (≥ 2 months with sales).

## Open questions
- Route A to confirm the minimum of 8 sums and the fallback (pendiente 9).
- Route B to decide whether to take the baselines commit as is or rewrite it.
- SBA starts the interval at 1 (StatsForecast convention), so on short series it forecasts high until it sees a few intervals. Route A evaluates it as is, with `alpha` fixed at 0.1 and untuned (declared). Since `alpha` is a policy parameter, tuning it later needs no code change.

## Next step
Route B: merge into `dev` before the `v0.2.0` tag (Friday 13-Nov). Route A: run the baselines and SBA through the evaluation harness on blocks 1–7.
