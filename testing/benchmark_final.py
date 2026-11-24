"""Benchmark the frozen Route A policy through the Core v1.2 HTTP contract."""
import copy
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from pango_prediction.config import Settings
from pango_prediction.main import create_app

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "model_policy.json"
EXPECTED_SHA = "326e0b3586477c9a00529e305b87267f1afbdedb14595981a63fbfb9c7503842"
EXPECTED_VERSION = "ruta-a-baseline-v1-inactive"
TOKEN = "local-benchmark-only"


def main():
    # Preserve exact bytes even on Windows checkouts with CRLF conversion.
    raw = POLICY.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA:
        raise RuntimeError(f"Unexpected policy SHA-256: {digest}. Check Git line endings.")
    fixture = json.loads(
        (ROOT / "tests/fixtures/forecast_request_minimal.json").read_text(encoding="utf-8")
    )
    fixture["contract_version"] = "1.2"
    fixture.pop("calendar", None)
    fixture["warehouses"] = [
        {"warehouse_id": fixture["series"][0]["warehouse_id"], "holds_stock": True}
    ]
    settings = Settings(
        service_token=TOKEN, max_series_per_request=500,
        policy_path=str(POLICY), log_level="WARNING",
    )
    output = []
    with TestClient(create_app(settings)) as client:
        for size in (1, 10, 100):
            payload = copy.deepcopy(fixture)
            payload["series"] = []
            for i in range(size):
                series = copy.deepcopy(fixture["series"][0])
                series["series_id"] = 10000 + i
                series["product_id"] = 10000 + i
                payload["series"].append(series)
            times = []
            failures = []
            for iteration in range(23):
                payload["request_id"] = f"benchmark-final-{size}-{iteration}"
                start = time.perf_counter()
                response = client.post(
                    "/v1/forecasts", json=payload,
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                elapsed = (time.perf_counter() - start) * 1000
                body = response.json() if response.status_code == 200 else {}
                results = body.get("results", [])
                valid = (
                    response.status_code == 200
                    and body.get("policy_version") == EXPECTED_VERSION
                    and body.get("contract_version") == "1.2"
                    and body.get("request_id") == payload["request_id"]
                    and body.get("forecast_run_id") == payload["forecast_run_id"]
                    and [r.get("series_id") for r in results]
                    == [s["series_id"] for s in payload["series"]]
                    and all(
                        r.get("status") == "ok"
                        and r.get("model_name") == "moving_average"
                        and len(r.get("periods", [])) == 4
                        and r.get("total") is not None
                        for r in results
                    )
                )
                if not valid:
                    failures.append({"iteration": iteration, "http_status": response.status_code,
                                     "policy": body.get("policy_version"),
                                     "series_statuses": sorted({str(r.get("status")) for r in results})})
                elif iteration >= 3:
                    times.append(elapsed)
            output.append({
                "series": size, "warmups": 3, "planned_measured_runs": 20,
                "successful_measured_runs": len(times),
                "failed_runs_including_warmups": len(failures),
                "failures": failures, "samples_ms": times,
                "median_ms": statistics.median(times) if times else None,
                "p95_ms": sorted(times)[math.ceil(.95 * len(times)) - 1] if times else None,
                "series_per_second": size * 1000 / statistics.mean(times) if times else None,
            })
    report = {
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(), "python": platform.python_version(),
        "processor": platform.processor(), "logical_cpus": os.cpu_count(),
        "policy_version": EXPECTED_VERSION, "policy_sha256": digest,
        "contract_version": "1.2",
        "scope": "local TestClient, repeated fixture, Core-compatible envelope, no actual Core, network or persistence",
        "results": output,
    }
    out = ROOT / "validation-results-final"
    out.mkdir(exist_ok=True)
    (out / "benchmark-final.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if any(r["failed_runs_including_warmups"] for r in output):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
