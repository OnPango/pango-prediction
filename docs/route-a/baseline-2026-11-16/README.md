# Route A baseline delivery

Received from Route A after the single run on 2026-11-16. These are supplied
artifacts, not instructions to rerun the evaluation or alter its freeze.

## Artifact identity

| Artifact | SHA-256 |
|---|---|
| `config/model_policy.json` (repository root) | `326e0b3586477c9a00529e305b87267f1afbdedb14595981a63fbfb9c7503842` |
| `regla_baseline_inactivas_2026-11-16.md` | `9035f7f5360237ba62b3349ad2d180ee2f6dd3c0e2680b799931e46f420927c8` |
| `MANIFIESTO_6a7f43c25ba78541.json` | `da742e8eb797c64109e60b32b95037a4177b221719f375b03d9d603971951e02` |
| `respaldo_inactivas.csv` | `8118471ec50a5d40a059961a8330b301d50e4e67ca0001b6021f6b01673442c4` |

Original bytes are preserved, including line endings, through `.gitattributes`.
The manifest references freeze
`6a7f43c25ba78541685e21f5651a0883b1b4303571a4bf8afea4f5b62cf3b3bd`
and five output files. The parquet files and `resultado.json` are not included
in this delivery. Their contents have not been independently checked here.
Route A reports a later derived `reporte.json` with SHA-256
`28a78a2b3cd512b13d6db7f8a8c9ae406de5907f961c37f225c6bd8b27a23562`;
that file is also not included. Do not add it to the original manifest.

## Decision and limits

Route A reports that v1 failed C1 and C5, so the baseline is selected, with
bottom-up forecasting at product × warehouse level. The delivered policy is
`ruta-a-baseline-v1-inactive`, with parameter fingerprint `f5f416da285217b5`:
first decline inactive series, then use `moving_average(window=4)`.

The CSV's blocks 8–13 sum to 17,352 inactive series-blocks, all with zero point
forecasts in all four weeks, 562 with a positive interval ceiling, and 408
with sales in the validation block. Those future sales are a limitation of the
inactivity rule, not evidence that the profile was computed with future data.
The reported equivalence uses evaluation's convention that no forecast counts
as zero. It does not make an operational absence of forecast a numerical zero
for Core. The CSV does not contain a MODEL_FAILED count; absence of those
failures is Route A's statement, not an independently verified CSV field.

Route A also declares: the run was advanced to Monday 16 without timely prior
notice; the freeze used the 149-date calendar, while Core will use the 151-date
calendar; dependencies were frozen with the harness lock, not a service lock.
This delivery does not resolve the missing service dependency lock or verify
either calendar, neither of which was supplied here.

## Starting the service with this policy

The development policy remains the default for local development. Select the
delivered artifact explicitly before starting the application:

```powershell
$env:PANGO_PREDICTION_POLICY_PATH = 'config/model_policy.json'
```

In the existing Docker image, configure
`PANGO_PREDICTION_POLICY_PATH=/app/config/model_policy.json`.
The image already copies `config/`. The service credential is still required.
An explicit missing/invalid policy fails startup; it never silently falls back
to the development policy. Check that the response's `policy_version` is
`ruta-a-baseline-v1-inactive`. Core must request contract `1.2` to receive
`no_forecast`; older contracts retain their existing error representation.

Less than five observable weeks still yields MODEL_FAILED. Handling insufficient
history separately is deferred until after November 20, in coordination with
Core. No alternative model is selected. StatsForecast is retained for future
evaluation; its availability in the registry does not put it in this policy.

## Method comparison

The implementation base is `77843bde04883b2921547e6a7b7ec11c76e4d082`.
Against `v0.2.0`, outbound adapters have no changes. Domain differences are the
closed no-forecast vocabulary and validation in `types.py` / `policy.py`, plus
a documentation-only change in `profile.py`. Series, availability, calendar,
parameters, intervals, allocation and model implementations are unchanged.
Application and HTTP changes are outside that scoped diff; warehouse and
history-only exclusions must still be respected by Core.

The reviewable feature commit and its scoped patch are identified in
`handoff-andres.md`. This package prepares deployment; it does not assert a
running deployment, a merged PR, or a reproducible service dependency lock.
