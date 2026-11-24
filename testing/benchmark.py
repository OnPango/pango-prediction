"""Local in-process API benchmark. No production requests or external credentials."""
import copy
import json
import platform
import statistics
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from pango_prediction.config import Settings
from pango_prediction.main import create_app

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "local-benchmark-only"


def main():
    fixture = json.loads(
        (ROOT / "tests/fixtures/forecast_request_minimal.json").read_text(encoding="utf-8")
    )
    output = []
    with TestClient(create_app(Settings(service_token=TOKEN, max_series_per_request=500))) as client:
        for size in (1, 10, 100):
            payload = copy.deepcopy(fixture)
            payload["series"] = []
            for i in range(size):
                series = copy.deepcopy(fixture["series"][0])
                series["series_id"] = 10000 + i
                series["product_id"] = 10000 + i
                payload["series"].append(series)
            times = []
            errors = 0
            policies = set()
            for iteration in range(23):
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
                    and len(results) == size
                    and all(r.get("status") == "ok" for r in results)
                )
                if not valid:
                    errors += 1
                if iteration >= 3:
                    times.append(elapsed)
                policies.add(str(body.get("policy_version")))
            output.append({
                "series": size, "warmups": 3, "measured_runs": 20,
                "failed_runs_including_warmups": errors,
                "median_ms": statistics.median(times),
                "p95_ms": sorted(times)[18],
                "series_per_second": size * 1000 / statistics.mean(times),
                "policy_versions": sorted(policies),
            })
    report = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(), "python": platform.python_version(),
        "scope": "local TestClient, repeated fixture, no network or persistence",
        "results": output,
    }
    out = ROOT / "validation-results"
    out.mkdir(exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if any(r["failed_runs_including_warmups"] for r in output):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
