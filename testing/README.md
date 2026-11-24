# Validation (Python 3.12)
From repository root, in the existing virtual environment:
```powershell
New-Item -ItemType Directory -Force validation-results
python -m pytest --cov=pango_prediction --cov-report=term-missing --cov-report=html --cov-report=xml:validation-results/coverage.xml --junitxml=validation-results/junit.xml 2>&1 | Tee-Object validation-results/tests.txt
python -m ruff check pango_prediction tests
python -m mypy pango_prediction tests
python -m testing.benchmark
```
Install dependencies from pyproject.toml (project and dev groups), following existing CI instructions. Benchmark uses TestClient in-process, identical fixture histories with distinct IDs, 3 warmups and 20 measurements per batch size. It records policy version and validates per-series success. This is not network or production latency; if the development policy is loaded, do not claim MA4 deployment performance. Confirm policy and hardware before reporting. Keep reports private.
