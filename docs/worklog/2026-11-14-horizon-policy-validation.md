# Request horizon validated against the policy

**Date:** 2026-11-14 · **Branch:** `feature/horizon-policy-validation` → `dev` (from `443a979`)

## Goal
The HTTP schema accepted any `horizon >= 1` while the policy loader required the policy to declare exactly its calibrated horizon, and nothing compared the two. A request for eight weeks would have been answered by a policy evaluated at four, with intervals and totals never calibrated for it. Found in the review of 2026-11-14.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `643d306` | `application/errors.py`, `application/forecast_service.py`, `tests/unit/application/test_horizon_validation.py` | `HorizonMismatchError`, raised by the use case before any series is touched |
| `3ad9fd3` | `adapters/inbound/api/errors.py`, `adapters/inbound/api/routes.py`, `tests/api/test_forecast_api.py`, `docs/contract-v1.md` | The HTTP adapter answers 400; the contract states the rule |
| Add worklog for horizon validation | this file | — |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 384 passed (8 new) |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 58 files |

## Decisions
- **A mismatch refuses the whole request with 400** (owner, 2026-11-14), instead of a per-series `MODEL_NOT_AVAILABLE` or a schema fixed at 4. The horizon is part of the calibrated contract, so a mismatch is an integration fault that should fail early and visibly.
- **The rule lives in the application layer, the status code in the adapter.** The use case is what knows the policy, so it raises; any future inbound adapter inherits the rule and only chooses how to render it.
- **The schema still accepts any `horizon >= 1`.** The valid value belongs to the loaded policy, not to the schema: a policy calibrated for another horizon then needs no schema change.
- **No policy, no refusal.** Without a policy no model runs and every series already reports `MODEL_NOT_AVAILABLE`, with its profile.
- `/v1/forecast-evaluations` is untouched: it scores forecasts that were already produced and does not run a policy.

## Open questions
- **400 or 422.** Contract section 17 defines 400 as a malformed request and 422 as a semantically invalid one, which describes this case more closely. 400 was the owner's choice and matches the existing batch-limit refusal; section 17 was extended to say so. Worth confirming with Pango Core before the contract is frozen.
- Branched from `dev` while `feature/model-error-isolation` is still open. The two are independent; both edit `forecast_service.py` and `tests/api/test_forecast_api.py` in different places.

## Next step
The documentation sync from the 2026-11-14 review: the Dockerfile comment about StatsForecast, the `src/pango_forecast` path in the README, and the stale counts in `ROUTE_B_STATUS.md` and `tests/fixtures/README.md`.
