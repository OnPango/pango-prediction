# Document the historical backtesting implementation plan

**Date:** 2026-11-17 · **Branch:** `feature/backtesting-plan` → `dev`

## Goal

Provide an executable handoff for Claude to implement offline historical
backtesting of the existing policy throughout the necessary repository paths.

## Commits

| Commit | Files | What and why |
|---|---|---|
| Document the historical backtesting implementation plan | `docs/BACKTESTING_IMPLEMENTATION_PLAN.md`, this worklog | Specify architecture, temporal isolation, labels, metrics, CLI exports, tests and acceptance criteria without implementing or changing forecasting behavior. |

## Checks

| Check | Result |
|---|---|
| Plan reviewed against live service, evaluation module, availability and existing evaluation tests | Completed; partial-total compatibility discrepancy explicitly recorded. |
| `git diff --check` | Passed. |
| Local runtime tests/static checks | Not rerun: documentation only. CI outcome is tracked on the PR. |

## Decisions

Keep the first implementation offline with explicit cuts and policy selection.
Separate retrospective evaluation labels from training data. Preserve official
Route A evaluation and HTTP semantics. The plan proposes an explicitly named
retrospective label method, requiring reconciliation with authoritative domain
documents before a behavior-changing implementation. Do not treat undefined
metrics, failures or no_forecast as perfect predictions.

## Open questions

Historical stock/calendar configuration and data completeness must be confirmed
for real datasets. The observability method must be checked against Route A's
specification. No real dataset is required to finish the synthetic implementation.

## Next step

Owner hands `docs/BACKTESTING_IMPLEMENTATION_PLAN.md` to Claude for implementation
from current dev, following AGENTS.md and the plan's acceptance criteria.
