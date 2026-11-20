"""Guard that the project metadata is parseable.

Added because it was not: `[dependency-groups]` was declared three times, which
is invalid TOML, and none of pytest, ruff or mypy complained because each reads
only its own table. An installer does not have that luxury, so the file is
parsed here the way one would parse it.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def test_pyproject_is_valid_toml() -> None:
    """tomllib rejects a table declared twice, which is the failure seen."""
    assert tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_the_web_framework_is_a_runtime_dependency_and_the_test_client_is_not() -> None:
    """FastAPI ships; httpx exists only to drive it from the tests."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    runtime = " ".join(config["project"]["dependencies"])
    dev = " ".join(config["dependency-groups"]["dev"])

    assert "fastapi" in runtime
    assert "httpx" not in runtime
    assert "httpx" in dev
