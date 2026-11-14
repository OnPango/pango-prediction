# Per-series isolation of model failures

**Date:** 2026-11-14 · **Branch:** `feature/model-error-isolation` → `dev` (from `443a979`)

## Goal
A review of the service found that the per-series error contract had a hole: the outbound model adapters raised bare `ValueError` and `RuntimeError`, which no layer caught. A single policy rule with a parameter the model could not read answered 500 for the entire request and lost the series that were fine. Partial results are the reason the contract reports errors per series, so this was a defect in the thing the contract promises.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `2a244ce` | `ports/model.py`, `adapters/outbound/baselines.py`, `adapters/outbound/croston.py`, `adapters/outbound/statsforecast.py`, their three test modules | `ModelUnavailableError` in the port, and the adapters raising it instead of builtins |
| `fd9c00d` | `application/forecast_service.py`, `tests/unit/application/test_forecast_service.py`, `tests/api/test_forecast_api.py` | The use case catches it, and the regression is pinned at both the use-case and HTTP levels |
| Add worklog for per-series model error isolation | this file | — |

Where each fault now goes:

| Fault | Type | Contract code |
|---|---|---|
| A parameter this adapter rejects | `ModelUnavailableError` | `MODEL_NOT_AVAILABLE` |
| A parameter the library rejects when building the model | `ModelUnavailableError` | `MODEL_NOT_AVAILABLE` |
| `statsforecast` absent, or missing the named model | `ModelUnavailableError` | `MODEL_NOT_AVAILABLE` |
| The library raising while forecasting | `ModelError` | `MODEL_FAILED` |
| Fitted values that do not line up with the series | `ModelError` | `MODEL_FAILED` |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 393 passed (17 new) |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 57 files |
| The regression test against the unfixed adapters | Fails with the original `ValueError: moving_average needs an integer 'window' parameter`, which is what makes it a regression test |
| Remaining `raise ValueError` / `RuntimeError` in `adapters/outbound` | Two, both verified unreachable from the model port: `fit_sba`'s range guard, now checked earlier where the parameter is read, and DeepAR's `_column`, which is raised inside a `try` that already converts it |

## Decisions
- **`ModelUnavailableError` is deliberately not a `ModelError`**, following the rationale already written for `ModelNotRegisteredError`: a `ModelError` means a model tried to forecast a series and could not, while this is a configuration fault affecting every series routed to that model. The two map to different contract codes, and merging them would point Pango Core at the data when the policy is what has to change.
- **It is a plain `Exception`, not a subclass of `ValueError`.** Inheriting from a builtin would have kept the old tests passing untouched, which is exactly why it was not done: the tests state the contract, so they had to change with it.
- **The foreign-library boundary is wrapped whole.** Both StatsForecast's `forecast` call and the construction of its model are guarded, because every exception type a third-party library may raise is the adapter's to translate. DeepAR already worked this way and is untouched; the earlier review that listed its `KeyError` as an escape was wrong, since it is raised inside the `try` that converts it.
- **What stays uncaught stays uncaught.** The `build_demand_profile` call in the service is still unguarded, for the reason written beside it: a `ValueError` there would be our own bug, and reporting it as `INVALID_SERIES` would blame Pango Core for our fault.
- **`fit_sba` keeps its `ValueError`** for direct callers such as Route A's notebooks. The policy path can no longer reach it because the alpha range is validated where the parameter is read.

## Open questions
- `CHECKPOINT_NOT_AVAILABLE` is defined in `application/errors.py` and raised by nothing: DeepAR reports a missing checkpoint as a plain `ModelError`, so it reaches Core as `MODEL_FAILED`. Deciding when a checkpoint counts as unavailable touches the artifact loading path and was left out of this branch.
- `compute_warehouse_shares` in `domain/allocation.py` does not check that all exposures share one `window_weeks`. Noted while reviewing the Route A patch; unrelated to this branch.

## Next step
The horizon validation: a request whose `horizon` does not match the loaded policy's must be rejected with 400, which the owner decided on 2026-11-14 and which nothing implements yet.
