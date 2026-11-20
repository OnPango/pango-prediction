# Feature-branch workflow for working from a phone

**Date:** 2026-11-07 · **Branch:** `feature/dev-workflow` → `dev`

## Goal

Fix how work is done in this repository when the owner drives it from a phone through Claude Code cloud sessions, with no local checkout:

- one branch per feature, created from `dev`;
- commits grouped by files that change for the same reason;
- a detailed PR into `dev` for every feature;
- a durable history of everything an agent does.

A cloud container is discarded after inactivity, so these rules had to live in the repository itself, where every new session reads them.

## Commits

| Commit | Files | What and why |
|---|---|---|
| `24aa244` Document the feature-branch workflow for agents | `AGENTS.md`, `CLAUDE.md` | `AGENTS.md` gains a "Git workflow" section and the `uv` commands for the quality gates in a cloud session. `CLAUDE.md` is new and imports `AGENTS.md`, because Claude Code loads `CLAUDE.md` automatically. |
| `f2a8d29` Add a pull request template for feature PRs into dev | `.github/pull_request_template.md` | The sections every feature PR carries, so it can be reviewed without opening the diff. |
| Add the worklog and its first entry | `docs/worklog/README.md`, `docs/worklog/2026-11-07-dev-workflow.md` | The format of the worklog, and this entry. |

## Checks

Run on `feature/dev-workflow` in the cloud session:

| Check | Result |
|---|---|
| `uv run python -m pytest` | 109 passed |
| `uv run ruff check .` | All checks passed |
| `uv run python -m mypy pango_prediction tests` | No issues in 29 source files |

The change is documentation only. The checks confirm that `dev` is green in a cloud session with nothing more than `uv sync`.

## Decisions

- **Branch prefix `feature/`.** The existing branches (`feature/file-structure`, `feature/domain-structure`, `feature/model-port-baselines`) already use it.
- **The automatic `claude/*` branch is not used.** Cloud sessions are assigned one, but it is created from `main`, which holds only the README.
- **The worklog has one file per branch rather than a single log.** Parallel branches appending to one file would conflict on every merge.
- **`AGENTS.md` stays the single source of the rules.** `CLAUDE.md` only imports it, so the rules are not written twice.
- **The agent never merges.** The owner reviews and merges every PR.

## Open questions

- **The default branch is `main`, which holds only the README.** A cloud session clones the default branch, so it does not load `AGENTS.md` or `CLAUDE.md` until it switches to `dev`. Making `dev` the default branch on GitHub would remove this gap.
- **`feature/model-port-baselines` has no PR.** It is 5 commits ahead of `dev` and holds most of Route B: profile, calendar, intervals, the model port, baselines and the v1.1 API, with 269 tests. Branches started from `dev` do not see it until it is merged.

## Next step

Merge `feature/model-port-baselines` into `dev`, then start the next feature from `dev`. According to `docs/ROUTE_B_STATUS.md`, the next feature is the policy loader, which is blocked on Route A agreeing the `model_policy.json` format.
