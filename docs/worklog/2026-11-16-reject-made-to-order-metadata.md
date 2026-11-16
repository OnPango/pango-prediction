# Reject `made_to_order` inside `metadata`

**Date:** 2026-11-16 · **Branch:** `feature/reject-made-to-order-metadata` → `dev` (from `34fdf00`)

## Goal
The contract said that sending `metadata.made_to_order` fails validation. It did not: `metadata` is free-form, so the key was accepted there and never read, and only the series-level field was refused. Found on 14 November and recorded as discrepancy 15. A flag that is silently ignored lets Pango Core believe it decided something, which is the kind of drift the request schema refuses everywhere else.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `2c015c6` | `adapters/inbound/api/schemas.py`, `tests/api/test_forecast_api.py` | A validator refuses the one key; the rest of `metadata` stays free-form |
| Document that made_to_order is rejected in metadata | `docs/contract-v1.md`, `docs/domain-rules.md`, this file | The contract states the behaviour again, and discrepancy 15 is closed |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 430 passed (2 new), run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 60 files |
| The new test before the validator | Failed with `assert 200 == 422` |
| The minimal fixture with the key inside `metadata` | `422`, with a message naming `metadata.made_to_order` |
| Trial merge with `feature/no-forecast-status` and `feature/route-a-criteria-worklog` | No conflicts |

The plain `python -m pytest` fails on the owner's workstation before collecting: Windows Application Control blocks a `pyarrow` DLL imported by a pytest plugin that arrives with StatsForecast. It is unrelated to this change and CI is not affected.

## Decisions
- **The service rejects the key** (owner, 16 November), instead of the contract being weakened to say it is ignored. That is what the contract promised Pango Core from version 1.1.
- **Only that key.** `metadata` is otherwise free-form on purpose, and a second test pins that: nested values and a key that merely starts with `made_to_order` are accepted.
- **In the HTTP adapter, as a schema validator.** The domain never reads `metadata`, so there is nothing for it to refuse; this is a rule about the shape of the request.

## Open questions
None.

## Next step
`feature/warehouse-config-contract`, once `feature/no-forecast-status` is merged.
