# Route A calibration freeze

**Date:** 2026-11-14 · **Branch:** `feature/route-a-freeze` → `dev`

## Goal

Apply the Route A confirmation that demand classification uses a 24-month
window and update the development artifact and documentation to the resulting
parameter fingerprint. Keep tenant-private warehouse identifiers out of the
repository while recording the externally supplied calendar seed.

## Decisions applied

- `classification_window_months=24` is now the default calibration.
- `DomainParameters.fingerprint()` produces `f5f416da285217b5`.
- The development policy and DeepAR training default use that fingerprint.
- EquipMaster's warehouse-stock mapping remains external administrator
  configuration.
- The calendar attachment contains 151 dates and its verified SHA-256 is
  `b3df11b6861a95a6d0803d065dc034d80512f2d7ccaa2291a34f1a52b7119c14`.

## Open decisions

- The executable `warehouses[]` contract block and `NO_FORECAST` response
  behavior still need implementation.
- Route A's top-down allocation is not yet connected to the production use case.
- The private warehouse mapping must be seeded in Core, not committed here.

## Next step

Implement and test the warehouse configuration contract, then run the Route A
harness before creating the production policy tag.
