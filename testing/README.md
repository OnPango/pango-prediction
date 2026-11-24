# Final policy validation

Base: main af43b159ab652261abcd24a3b6f3baa39061f41c. Core contract inspected on dev bd52a657a4808d3c3ab5f8594062cc2490252a83. Actual Railway deployment SHA and policy environment remain to be independently confirmed.

From repository root, with Python 3.12 and existing project/dev dependencies:

```powershell
.venv\Scripts\python.exe -m pytest tests/api/test_route_a_policy.py tests/api/test_warehouse_config_api.py tests/integration/test_backend_request_fixtures.py
.venv\Scripts\python.exe -m testing.benchmark_final
```

The script verifies the frozen policy SHA, version, MA4 model, v1.2 envelope, request identifiers and one result per series. It measures in-process TestClient requests, not real network or Core persistence. Results: validation-results-final/benchmark-final.json. Keep the earlier testing branch results separate. If SHA differs on Windows, verify Git checkout line endings and restore exact artifact bytes before running.

For actual integration evidence, generate a forecast from a disposable organization in the deployed frontend, verify successful persistence and four weekly periods, and collect sanitized Core/Prediction logs with matching request_id, forecast_run_id and policy_version. Confirm Railway's deployed commit and PANGO_PREDICTION_POLICY_PATH (never disclose credentials). Do not equate the local benchmark with deployed latency. Record date, batch size and end-to-end elapsed time separately.
