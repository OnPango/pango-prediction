# Holdout validation in /v1/forecasts

**Date:** 2026-11-18 · **Branch:** `feature/forecast-validation` → `dev`

## Goal

Return an error per series from the first forecast run. A forecast's own error
only exists once its four-week horizon closes, so the first operational figure
would arrive with the second monthly run. The owner decided the service must
show one from day one, by splitting each series' history into a training part
and a validation part.

A request that sets `include_validation: true` now receives, per series, the
deployed policy's score on the last four complete weeks of the history it sent.
The service forecasts those weeks from everything before them, with the
unchanged production pipeline, and scores them with Route A's series-block
convention. Without the flag the response is byte for byte what it was.

## Commits

| Commit | Files | What and why |
|---|---|---|
| `2c85825` Add Route A series-block metrics to the evaluation domain | `domain/evaluation.py`, `tests/unit/domain/test_evaluation.py` | `evaluate_block` implements criteria §1.3 and §1.7: `e = f − y` on the sums over evaluable weeks, blocks with fewer than two evaluable weeks discarded, total coverage only with every week evaluable. It returns components so a set aggregates from sums. `evaluate_forecast` (contract §19) is untouched. |
| `c84c9cc` Add the holdout validation use case | `application/validation.py`, `application/forecast_service.py`, `tests/unit/application/test_validation.py`, `tests/unit/application/test_forecast_service.py` | `HoldoutValidation` cuts the command's history at `train_end`, runs the same `ForecastService`, labels the held-out weeks with `build_weekly_series` and `build_weekly_availability`, and scores with `evaluate_block`. `SeriesError.model_history_insufficient` lets it tell `InsufficientHistoryError` apart while the wire code stays `MODEL_FAILED`. |
| `b515554` Serve the holdout validation from /v1/forecasts on request | `adapters/inbound/api/schemas.py`, `adapters/inbound/api/routes.py`, `pyproject.toml`, `tests/api/test_forecast_validation_api.py` | Strict boolean `include_validation`, the `validation` block per result, omitted rather than null when not asked for. The pydantic floor moves to 2.12, the first release that honours `exclude_if`. |
| Document the holdout validation | `docs/contract-v1.md`, `README.md`, `docs/BACKTESTING_IMPLEMENTATION_PLAN.md`, this worklog | Contract §3 (why no version bump), §7 (the field), §14 (method, labels, score, statuses, captured example), §19 (its conventions differ). The plan records what this supersedes. |

## Checks

| Check | Result |
|---|---|
| `python -m pytest` | 504 passed; `origin/dev` collects 461, so 43 are new. One pre-existing warning: Starlette's deprecation of `httpx` in `TestClient`. |
| `ruff check .` | All checks passed. |
| `python -m mypy pango_prediction tests` | No issues in 66 source files. |
| `/v1/forecasts` without the flag, against `origin/dev` | 8 responses (2 fixtures × development and Route A policies × envelopes 1.1 and 1.2) compared byte for byte before and after the change: all identical. |
| Real `uvicorn` with the Route A policy | With the flag, 3 of the 5 fixture series evaluated, 1403 `no_history_at_cutoff`, 1500 `forecast_failed`. Without the flag, no `validation` key. Duration 1.8 ms against 0.9 ms. |
| `exclude_if` across pydantic versions | 2.11.0 writes `"validation": null` without error; 2.12.0 omits it. Hence the 2.12 floor. |
| README PowerShell snippet | Run under Windows PowerShell 5.1: sets `include_validation` to `true`. |
| CI on the PR | Tracked on the PR. |

## Decisions

- **Opt-in inside `/v1/forecasts`** rather than a separate endpoint or an
  offline CLI. Core already sends the full history in this call, so the
  validation needs no second payload, and the opt-in keeps the delivered
  envelope unchanged for the 20-nov delivery and the Route A forecast parity.
- **Route A's conventions**, by the owner's decision, using what Route A
  delivered (`criteria.md`): one error per block, `f − y`, so positive means
  over-forecast; fewer than two evaluable weeks discards the block; series the
  policy declined, or whose model refused a short history, are scored as f = 0
  (DEC-1) and flagged `counted_as_zero`.
- **Not scored**, with a reason: request-level declines (`history_only`,
  non-stock warehouse), series with no sale before `train_end`, any other
  failure at the cut, and held-out weeks that cannot be built. A failure is not
  a forecast of zero.
- **Labels from the history up to the end of the held-out weeks.** Inside a
  request there is no later data, and it keeps every series equally informed.
- **No contract version bump.** Raising the served version would have changed
  `contract_version` in every response, including those of clients that never
  ask for the block. The opt-in field is the signal.
- **The block's `forecast_status` is not translated for 1.0/1.1 clients.** The
  block is opt-in; a client that asks for it has read §14.
- **`ROUTE_B_STATUS.md` is not edited.** It is a dated snapshot of another
  branch, and changing one table would make its header untrue.

## Open questions

1. **A stockout covering the end of the held-out weeks reads as zero demand.**
   Weeks after a series' last sale are inactivity to the availability rules, so
   they are `UNKNOWN` and evaluable. It is pinned by a test as a known
   limitation. Route A has not said with which history it labelled its
   validation weeks.
2. **Total coverage mixes two interval methods.** Below eight horizon sums, the
   total interval falls back to weekly residuals × 4. `intervals.py` and DEC-4
   require coverage reported apart for each, but `ForecastTotal` does not
   record which one was used.
3. **Contract §19 uses other conventions** (weekly, `actual − estimate`). The two
   figures must not be mixed until they are reconciled with Route A.
4. **Showing the block at the 20-nov delivery is the owner's call.** Route A
   warned against a second error figure. This one follows Route A's convention,
   but it scores a different period (the last four weeks of each request), not
   the official blocks 8–13.
5. **Not computed:** RMSSE, Route A's selection metric, and MASE.
6. **Cost:** the flag doubles the forecasting work for the series of the
   request. Negligible for MA4; to measure before a heavier policy is deployed.

## Next step

Core sends `include_validation: true` and stores the block per
`(forecast_run_id, series_id)` together with `policy_version`. After 20-nov, the
offline multi-cut backtest of `docs/BACKTESTING_IMPLEMENTATION_PLAN.md` can be
built on `HoldoutValidation` and `evaluate_block`, once the open questions above
are settled with Route A.
