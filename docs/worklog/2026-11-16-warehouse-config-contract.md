# Warehouse configuration in the request: `warehouses[]` and `history_only`

**Date:** 2026-11-16 · **Branch:** `feature/warehouse-config-contract` → `dev` (from `bcfe90e`)

## Goal
Two of the five agreed `no_forecast` reasons could not be produced by anything: `non_stock_warehouse` and `history_only`. Both depend on facts that are not in a series' history and cannot be derived from it, namely whether its warehouse holds stock and whether the series is there only to carry history. The owner's proposal of 8 November put them in the request as part of contract 1.2, and Route A confirmed that `holds_stock` belongs to a warehouse block and `history_only` to each series. Backend is writing its client now, so the fields go in before that client exists.

## Commits
| Commit | Files | What and why |
|---|---|---|
| `c61ef21` | `domain/types.py`, `application/forecast_service.py`, `tests/unit/application/test_warehouse_configuration.py`, `tests/api/test_forecast_api.py` | The use case declines the series the request rules out |
| `8f1bcd7` | `adapters/inbound/api/schemas.py`, `adapters/inbound/api/routes.py`, `tests/api/test_warehouse_config_api.py` | The request accepts `warehouses[]` and `history_only` |
| Document warehouses[] and history_only in the contract | `docs/contract-v1.md`, `docs/ROUTE_B_STATUS.md`, `docs/domain-rules.md`, `tests/fixtures/README.md`, this file | The contract, with examples captured from the service |

## Checks
| Check | Result |
|---|---|
| `python -m pytest` | 453 passed (23 new), run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` until `feature/dev-tooling-fixes` is merged |
| `ruff check .` | All checks passed |
| `python -m mypy pango_prediction tests` | No issues in 62 files |
| The first code commit on its own | 444 passed, lint and types clean |
| A series in a warehouse with `holds_stock: false`, declaring 1.2 | `no_forecast`, `non_stock_warehouse` |
| A series with `history_only: true` | `no_forecast`, `history_only` |
| The same, declaring 1.0 or 1.1 | `error`, `MODEL_NOT_AVAILABLE`, response in 1.1 |
| A warehouse described twice | `422` |
| The main response example of the contract, captured again with the block | Unchanged |
| Upstream of the branch before its first commit | `origin/feature/warehouse-config-contract` |

## Decisions
- **The fields follow the agreed specification**, the "Cambios al contrato v1.2" table of the owner's proposal of 8 November: `history_only` on `series[]`, defaulting to false; `holds_stock` in a new `warehouses[]` block; a warehouse that is not described counts as stock, with a warning.
- **The request is heard before the policy.** What an administrator configured stays true however a series' demand changes, so it needs no policy loaded and a policy does not override it. `history_only` is checked before the warehouse because it is about the series itself.
- **Declining a series does not excuse its data.** Invalid input still fails it with `INVALID_SERIES`. Too little history does not: that is no failure for a series nobody meant to forecast.
- **The warning is on every series whose warehouse is not described.** The response has no other place for warnings, and the stockout reading depends on the assumption. It also reaches clients that never send the block, such as the fixtures; one existing API test changed for that reason, from "no warnings" to "exactly that warning".
- **The fields are accepted in any declared version.** They are additive. Only the status they lead to follows the version, like every declined series.
- **A warehouse described twice is refused.** Taking the first or the last entry would settle a contradiction by the order of a list.
- **The message an older client reads changed** from "The policy does not forecast this series" to "This series is not forecast", because the policy is no longer the only thing that declines one.
- **The fixtures were not regenerated.** They carry neither field, which is the proof that a client that knows nothing of them keeps working.

## Open questions
- **`no_active_warehouse` is never produced.** It belongs to the top-down allocation of `domain/allocation.py`, which is not connected to the use case and stays that way until after 20 November.
- **Nothing uses the history of a `history_only` series yet.** The proposal sends it so that `seasonal` and the product-level arm can read a product's whole history; neither exists in the service. Today such a series is only declined.
- **Batching by product is not in the contract.** The proposal asks Core to send all the series of a product, historical ones included, in one call, because `seasonal` needs the whole product. `docs/domain-rules.md` section 2 still says a batch split across calls is harmless, which is true of the code today. The two disagree, and it was left for the owner and Route A to settle with `seasonal`.
- **How much history Core should send** is still not written anywhere.

## Next step
Pango Core stores the warehouse configuration and sends it. On this side, nothing further is blocked until Route A reports the run of 17 November.
