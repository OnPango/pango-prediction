# StatsForecast adapters and DeepAR boundary

**Date:** 2026-11-12 · **Branch:** `feature/statsforecast-deepar` → `dev`

## Goal
Integrate the Route A SBA and horizon-total work, add the remaining local
StatsForecast candidates behind the model port, and preserve the separate
DeepAR training/runtime boundary.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `Add Route A SBA and horizon-total calibration` | `domain/intervals.py`, baselines, SBA adapter, registry, tests, worklog | Apply the supplied four-commit Route A patch without rewriting its reference implementation. |
| `Add StatsForecast model adapters` | `pyproject.toml`, `adapters/outbound/statsforecast.py`, registry, tests | Register thin adapters for ETS, classic Croston, TSB, ADIDA, IMAPA and AutoARIMA using StatsForecast 2.1.1. |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 304 passed; one existing deprecation warning from the installed FastAPI/Starlette test client |
| `ruff check .` | Passed |
| `python -m mypy pango_prediction tests` | Passed |
| StatsForecast native execution on this Windows workstation | Blocked: Application Control rejects SciPy/statsmodels DLL loading; registry assembly remains lazy and tests pass |

## Decisions
- Statistical algorithms remain in StatsForecast rather than being rewritten.
- Models consume `observed_demand`, preserving the S31 masking rule.
- The default registry exposes statistical model names without importing native
  StatsForecast modules during application startup.

## Open questions
- DeepAR still needs the Route A checkpoint, artifact metadata/checksum,
  NeuralForecast version and the deployment-owned GPU runtime before its
  inference adapter can be implemented and validated.
- Route A must confirm the final policy names and parameters for the statistical
  candidates.

## Next step
Create the DeepAR artifact-store/runtime feature once Route A supplies a
versioned checkpoint and its metadata contract.
