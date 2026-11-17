# Deliver the selected Route A baseline policy

**Date:** 2026-11-17 · **Branch:** `feature/route-a-baseline-policy` → `dev`

## Goal

Prepare a reviewable service version and method diff for Route A after its
single-run baseline selection, without altering forecasting algorithms.

## Commits

| Commit | Files | What and why |
|---|---|---|
| `654920a` Add the delivered Route A baseline policy | `.gitattributes`, `config/model_policy.json`, `pango_prediction/config.py`, `pango_prediction/main.py`, `tests/api/test_route_a_policy.py`, `tests/unit/test_config.py`, four files in `docs/route-a/baseline-2026-11-16/` | Preserve supplied policy and evidence, wire explicit startup selection, test the actual HTTP path, document provenance and limits. |
| Document the Route A deployment candidate | `docs/route-a/baseline-2026-11-16/handoff-andres.md`, `docs/route-a/baseline-2026-11-16/v0.2.0-to-654920a.patch`, this worklog | Deliver a concrete implementation SHA and the requested scoped diff; record validation and remaining deployment work. |

## Checks

| Check | Result |
|---|---|
| `python -m pytest` | 461 passed; one existing Starlette/httpx deprecation warning. Sandbox temp-folder access required escalation. |
| `python -m ruff check .` | Passed. |
| `python -m mypy pango_prediction tests` | Passed, 63 source files. |
| `git diff --check` | Passed before adding the verbatim patch artifact. |
| SHA-256 of four committed supplied artifacts | Matches Route A's four announced digests, including Git blob bytes. |
| Diff `v0.2.0..654920a` on domain and outbound adapters | No outbound changes; domain reason vocabulary/validation plus profile docstring. No model, interval, allocation or preprocessing changes. |

## Decisions

Select the production artifact explicitly via `PANGO_PREDICTION_POLICY_PATH`;
keep the development default. Invalid explicit paths fail startup. This is
assembly configuration, not domain selection logic. Keep StatsForecast and
defer insufficient-history status changes until after November 20.

The aggregate CSV and manifest are retained as supplied evidence, not as an
independent rerun of the evaluation. Source parquets, report and calendars were
not supplied. The CSV has no MODEL_FAILED column. Its 408 later-selling inactive
cases are retained as a limitation of the cutoff-based rule.

## Open questions

Owner review/merge and actual deployment SHA remain pending. A candidate is not
a running deployment. Service dependency locking, Core contract 1.2 adoption,
and the after-November-20 history-insufficiency treatment remain open. CI outcome
is recorded on the PR, rather than predicted in this worklog.

## Next step

Have Route A review the candidate diff. After owner merge and deployment,
confirm the running policy and deployed commit, regenerating the diff if the
method-related paths change.
