# Development tooling fixes

**Date:** 2026-11-16 · **Branch:** `feature/dev-tooling-fixes` → `dev` (from `bcfe90e`)

## Goal
Three small things that got in the way of the work on 16 November, none of them a feature: the test command stopped running on the owner's workstation, the documented way of starting a branch let commits reach `dev` without a pull request, and one docstring contradicted the code since the Route A freeze.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `b1e668d` | `pyproject.toml` | `python -m pytest` runs again on Windows |
| `c72130e` | `AGENTS.md` | A new branch can no longer be synced into `dev` |
| `7d66fbc` | `domain/profile.py` | A docstring left behind by the freeze |
| Add worklog for the development tooling fixes | this file | — |

## Checks
| Check | Result |
|---|---|
| `python -m pytest`, plain, with no environment variable | 430 passed. Before the change it failed three times in a row before collecting |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 60 files |
| Upstream of this branch before its first commit | `origin/feature/dev-tooling-fixes`, not `origin/dev` |

## Decisions
- **The plugins are switched off in `addopts`, not uninstalled.** `fugue_test` and `fugue_test_fixtures` arrive with StatsForecast through its `fugue` dependency. pytest loads every installed plugin, and loading these imports `pyarrow`, whose DLLs Windows Application Control blocked. The project uses neither plugin, so `-p no:` costs nothing and needs no machine-specific setup. Naming a plugin that is not installed is harmless, so CI and the image are unaffected.
- **`--no-track` plus an immediate `push -u`.** `git checkout -b feature/x origin/dev` makes the new branch track `origin/dev`. Anything that syncs the checkout then pulls `dev` into the branch and pushes the branch into `dev`. The reflog of 16 November shows it: a `pull --tags origin dev` seven seconds after a commit, followed by `dev` moving to that branch.
- **`dev` was not rewritten.** The commits that reached it this way are sound and are listed below; removing them would mean force-pushing a shared branch.

## Open questions
- **Two changes are on `dev` without a pull request or a review.** The three code commits of the `no_forecast` status (`6e128c8`, `612929c`, `34fdf00`) on 14 November, and the rejection of `metadata.made_to_order` (`2c015c6`, `c00d299`, merged by `bcfe90e`) on 16 November. Each has its worklog entry and passed the three checks locally. Whether CI ran green on those pushes was not verified from this workstation.
- **Why Application Control started blocking the DLL** is not known. The same command passed earlier the same day. The block is a property of the workstation; this branch only stops the project from depending on it.

## Next step
`feature/warehouse-config-contract`: the `warehouses[]` block with `holds_stock` and the per-series `history_only`.
