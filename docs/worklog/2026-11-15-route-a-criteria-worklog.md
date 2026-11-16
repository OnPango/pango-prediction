# Route A selection and acceptance criteria

**Date:** 2026-11-15 · **Branch:** `feature/route-a-criteria-worklog` → `dev`

## Goal
Record the merge of Route A's pre-registered criteria (`docs/route-a/criteria.md`, PR #9). Per A16 that PR carried only the criteria document, so it had no worklog entry; this entry is the one it deferred.

The document fixes, before any candidate was compared, how the policy v1 and the best baseline are selected (blocks 1–7) and how the policy v1 is accepted or rejected against the baseline in the single reporting run (blocks 8–13).

## Commits
| Commit | Files | What and why |
|---|---|---|
| `887fc28` (merged in PR #9, `4a2c8d0`, 13-Nov 14:23) | `docs/route-a/criteria.md` | The criteria document, alone, as A16 requires |
| Add worklog for the Route A criteria | this file | The deferred worklog entry |

## Checks
| Check | Result |
|---|---|
| SHA-256 of `docs/route-a/criteria.md` in `dev` and in the tag `v0.2.0` | `a4f96d474794cd291ece472b2a24f4ce762316b167e80f38ccf34571501d88f5`, the value confirmed by Route A on 13-Nov |
| The criteria file is unchanged since its merge (DEC-8) | Same SHA-256 in `4a2c8d0`, `v0.2.0` and the current `dev` |
| Route A's `freeze.json` (generated 15-Nov) | Records the same SHA-256; the single-run runner reads the file from the tag with `git show` and refuses to run if it differs |

## Decisions
- **Separate worklog (A16).** The criteria PR had to contain only the document, so that its review could not be mixed with any code change; the worklog follows in its own PR.
- **The document is immutable since its merge (DEC-8).** An error found later is declared in Route A's report, not fixed in the file.
- **Comparisons before the merge are exploratory (DEC-5).** Route A's selection on blocks 5–7 ran after the merge.
- **Fingerprint exceptions (DEC-2).** `DomainParameters.fingerprint()` does not cover the allocation strength k = 1.18 nor its 8-week window, which Route A freezes in `freeze.json` (A14), nor the platform constant `MIN_HORIZON_RESIDUALS = 8`.

## Open questions
- **Erratum to DEC-1, declared in Route A's report.** DEC-1 says that series without a forecast "still come out as no forecast" in production. In v1 a series whose model lacks history comes out as `MODEL_FAILED`, as agreed on 15-Nov. The erratum changes no evaluation rule: counting such series as f = 0 remains the evaluation convention (A13).
- **How Route A applied what the document leaves open** is recorded in Route A's evaluation rules, frozen in `freeze.json`: the history used to mark the evaluated weeks, products without history at the cutoff, the origin of the 4-week total for C3, and the f = 0 safeguards.

## Next step
Route A runs the single reporting run on 17-Nov with the frozen policy v1 and baseline, and reports the result of C1–C5 and T4.
