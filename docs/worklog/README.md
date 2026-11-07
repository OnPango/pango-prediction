# Worklog

The in-repository history of the work on PANGO Prediction: one entry per feature branch.

Git history and pull requests record *what* changed. The worklog keeps the reasoning, the checks that were run and the decisions left open, next to the code they describe. It survives any single session, PR page or local checkout.

## Rules

- One file per feature branch, added in the branch's last commit before its PR into `dev`.
- File name: `YYYY-MM-DD-<slug>.md`, where the date is the day the PR is opened and `<slug>` is the part of `feature/<slug>` after the slash. Sorting by name gives the chronological order.
- Entries are not rewritten after merge. A later correction goes into the entry of the branch that makes it.

## Entry format

```markdown
# <Feature title>

**Date:** YYYY-MM-DD · **Branch:** `feature/<slug>` → `dev`

## Goal
What the feature does and why it was needed now.

## Commits
| Commit | Files | What and why |
|---|---|---|

## Checks
| Check | Result |
|---|---|

## Decisions
Choices made during the work, with their reason.

## Open questions
What the owner or Route A still has to decide. "None" when there is nothing.

## Next step
The next feature branch this one enables.
```

The last commit of a branch cannot list its own hash, so the entry names it by its subject line.
