# Backend integration contract

**Date:** 2026-11-14 · **Branch:** `feature/backend-integration-contract` → `dev`

## Goal

Make the HTTP contract directly usable by the Pango Core Backend developer and
bring the Route B status report up to the latest `dev` state. Route A decisions
and the `made_to_order` contract decision remain explicitly open.

## Commits

| Commit | Files | What and why |
|---|---|---|
| Add Backend integration contract | `docs/contract-v1.md`, `tests/fixtures/README.md`, `docs/ROUTE_B_STATUS.md`, this file | Document request construction, stock reconstruction, persistence, retries, error handling and the current implementation state |

## Checks

| Check | Result |
|---|---|
| `.venv\Scripts\python.exe -m pytest` | 401 passed; 1 existing Starlette deprecation warning |
| `ruff check .` | All checks passed |
| `.venv\Scripts\python.exe -m mypy pango_prediction tests` | No issues in 58 files |

## Decisions

- The contract now gives Backend an implementation checklist.
- `metadata.made_to_order` must be omitted, but explicit rejection inside free-form metadata remains open.
- Route A decisions 3 and 4 remain open.

## Open questions

- Route A production policy and model-routing decisions.
- Formal handling of `metadata.made_to_order`.
- StatsForecast versus the Route A policy scope.
- DeepAR production artifact and runtime delivery.

## Next step

Backend implements the client and persistence flow from the contract. The next
Route B work can prepare deployment and DeepAR runtime requirements once Route A
provides the production policy and checkpoint metadata.
