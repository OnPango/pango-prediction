# Simplify the README and document service startup

**Date:** 2026-11-17 · **Branch:** `feature/readme-quickstart` → `dev`

## Goal

Make the README a concise entry point for installing, starting and checking the
service with the delivered Route A policy. Replace repeated architecture and
roadmap detail with links to the existing documentation.

## Commits

| Commit | Files | What and why |
|---|---|---|
| Simplify the README and document service startup | `README.md`, this worklog | Document Python 3.12/pip setup, PowerShell/bash startup, explicit policy selection, API verification, Docker and quality gates without changing runtime behavior. |

## Checks

| Check | Result |
|---|---|
| `python -m pytest` | 461 passed; existing Starlette/httpx deprecation warning. |
| `ruff check .` | Passed. |
| `python -m mypy pango_prediction tests` | Passed, 63 source files. |
| Actual Uvicorn factory startup with explicit Route A policy | Passed; GET /health and authenticated minimal-fixture POST succeed, response identifies ruta-a-baseline-v1-inactive. Process stopped after verification. |
| `git diff --check` | Passed. |
| Configuration and Docker commands | Checked against config.py, main.py and docker/Dockerfile.api; no new local Docker build for this documentation-only change. |

## Decisions

Keep instructions in Spanish with PowerShell and bash examples. Read the token
interactively, select the Route A policy explicitly, and explain development
fallback, contract 1.2 and warehouse stock semantics. Keep extended details in
existing docs rather than duplicating them in the README.

## Open questions

No new decisions. Actual deployment, Core integration and service dependency
locking remain outside this documentation change.

## Next step

Owner reviews and merges the PR, then follows the startup guide to run the
service and verify the active policy.
