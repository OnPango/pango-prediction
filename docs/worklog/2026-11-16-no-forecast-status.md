# The `no_forecast` status and contract 1.2

**Date:** 2026-11-16 · **Branch:** `feature/no-forecast-status` → `dev` (from `9cd42d4`, tag `v0.2.0`)

## Goal
A series the policy chose not to forecast came back as the error `MODEL_NOT_AVAILABLE`. That made a decision look like a fault: Pango Core could not tell "this broke" from "this was never meant to be forecast", and most of a catalogue is the second kind. Route A closed the vocabulary of reasons on 14 November, which was the last thing this needed. It was done now because Backend is writing its client against the contract, and a status added after that client exists costs them a rewrite.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `6e128c8` | `domain/types.py`, `domain/policy.py`, `tests/unit/domain/test_policy.py`, `config/development_policy.json`, three test modules | `NoForecastReason`, and a policy loader that accepts no other reason |
| `612929c` | `application/forecast_service.py`, `tests/unit/application/test_no_forecast.py` | The use case reports a declined series as an outcome of its own |
| `34fdf00` | `config.py`, `adapters/inbound/api/schemas.py`, `adapters/inbound/api/routes.py`, `tests/api/test_no_forecast_api.py` | Contract 1.2: the status is served to clients that declare it |
| `61855f7` | `docs/contract-v1.md`, `docs/ROUTE_B_STATUS.md`, `docs/domain-rules.md`, fixture notes | The contract and the status report describe 1.2 |
| `87a83fa` | `docs/contract-v1.md` | An answer to a question from the Backend developer, written down where the next reader will look |
| `4bf1d98` | this file, `docs/ROUTE_B_STATUS.md` | This entry, and the date of the status report |
| Correct a commit hash in the no_forecast worklog | this file | The adapter commit was listed under a hash that does not exist |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 428 passed (27 new) |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 60 files |
| Each of the three code commits on its own | 415, 420 and 428 passed, with lint and types clean at each |
| A declined series, declaring 1.2 | `status: "no_forecast"`, `reason: "inactive"`, `summary.no_forecast: 1` |
| The same series, declaring 1.0 or 1.1 | `status: "error"`, `MODEL_NOT_AVAILABLE`, response in 1.1 |
| The request fixture, 1.1 against 1.2 | Identical results and summary; only the declared version differs |
| Rows with a repeated date, a `from`/`to` range, a row after `history_end`, a negative quantity | Summed; 422; `INVALID_SERIES`; `INVALID_SERIES` |

## Decisions
- **The status is served only to a client that declares 1.2** (owner, 16 November). Every earlier change was an added field, so one envelope served everyone. A status is not additive: a client that knows `ok` and `error` may branch on exactly those. A 1.0 or 1.1 client is answered in 1.1 with the error it already handles.
- **The application does not know contract versions.** The use case always reports a declined series as declined. Rendering it for an older client is done in the HTTP adapter, at the edge, which keeps the translation out of the use case.
- **The policy loader is strict about reasons**, as it already was about model names. A misspelt reason would otherwise turn a declined series into something else without anyone being told. Route A's allocation worklog expected this of the loader.
- **A rule cannot state both a model and a reason.** It contradicts itself, and accepting it would mean silently ignoring one of the two.
- **A rule with neither stays valid, and stays an error.** It means the policy has nothing for the series. This is the path history insufficiency travels: Route A's answer is that it remains an error until a rule routes it with a reason of its own, and none of the five is that one. No sixth reason was invented.
- **`domain/allocation.py` was not edited.** It names two of the reasons as plain constants. A test pins them to the vocabulary instead, so Route A's module stays as it was sent.
- **The summary and the request log are counted from the rendered results**, so they describe the response the client received.
- **The development policy keeps `policy_version` `development-1`.** Its routing is unchanged: the same series get no model. Only the text of two reasons changed.

## Open questions
- **`dev` already holds the three code commits.** It was fast-forwarded to `34fdf00` between 14 and 16 November, before this entry existed, so the pull request of this branch carries only the three documentation commits and this correction.
- **`non_stock_warehouse`, `no_active_warehouse` and `history_only` are never produced yet.** They need the `warehouses[]` request block and the per-series `history_only` flag, which is the next branch.
- **The message a 1.0 or 1.1 client sees changed wording.** The code is the same, `MODEL_NOT_AVAILABLE`. An inactive series now says "The policy does not forecast this series: inactive." and a non-evaluable one says "The policy selected no model."; the explanation in words is in `warnings`, as before.
- **How much history Core should send is not written anywhere.** The classification looks at the last 24 months and the series starts at its first sale, so less history changes the class, but the contract states no minimum.
- **`CONTRACT_VERSION` in the fixtures stays `1.0`** on purpose. Raising it belongs to the moment Core moves.
- The docstring of `build_monthly_series` in `domain/profile.py` still says truncating the classification grid is off by default. Since the freeze of 14 November the default is 24 months.

## Next step
`feature/warehouse-config-contract`: the `warehouses[]` block with `holds_stock` and the per-series `history_only`, which produce the three reasons that are not reachable today.
