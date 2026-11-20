"""DeepAR artifact and runtime boundary.

The CPU API does not import PyTorch. A GPU runtime can use this module to
validate a NeuralForecast artifact and inject the actual NeuralForecast
runner. Keeping the runner behind a small protocol lets the forecasting
application remain independent of the neural framework.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from pango_prediction.adapters.outbound.baselines import _history
from pango_prediction.domain.series import future_weeks
from pango_prediction.domain.types import ForecastPeriod, ForecastTotal
from pango_prediction.ports.model import ModelError, ModelRequest


class DeepARArtifactError(ValueError):
    """A DeepAR artifact is missing, malformed or fails integrity checks."""


@dataclass(frozen=True, slots=True)
class DeepARArtifactMetadata:
    """Metadata required to identify and validate one neural artifact."""

    model_ref: str
    checkpoint_version: str
    training_period: str
    library_version: str
    artifact_sha256: str
    frequency: str
    horizon: int
    params_version: str
    runtime_format: str = "neuralforecast_save"


@dataclass(frozen=True, slots=True)
class DeepARArtifact:
    """A validated local artifact directory."""

    root: Path
    metadata: DeepARArtifactMetadata


@dataclass(frozen=True, slots=True)
class DeepARForecast:
    """One probabilistic prediction returned by the neural runtime."""

    estimate: float
    lower: float
    upper: float


def load_deepar_artifact(
    root: str | Path,
    *,
    expected_model_ref: str | None = None,
    expected_frequency: str = "WEEKLY",
    expected_horizon: int = 4,
    expected_params_version: str | None = None,
) -> DeepARArtifact:
    """Load and verify a NeuralForecast artifact directory.

    The directory must contain ``metadata.json`` and a ``checkpoint/``
    directory. The checksum covers every file below ``checkpoint/`` in sorted
    relative-path order, so metadata can be updated without changing the model
    bytes.
    """
    path = Path(root)
    metadata_path = path / "metadata.json"
    checkpoint = path / "checkpoint"

    if not metadata_path.is_file():
        raise DeepARArtifactError(f"DeepAR metadata is missing: {metadata_path}")
    if not checkpoint.is_dir():
        raise DeepARArtifactError(f"DeepAR checkpoint directory is missing: {checkpoint}")

    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DeepARArtifactError(f"could not read DeepAR metadata: {metadata_path}") from error
    except json.JSONDecodeError as error:
        raise DeepARArtifactError("DeepAR metadata is not valid JSON") from error

    if not isinstance(raw, Mapping):
        raise DeepARArtifactError("DeepAR metadata must be a JSON object")

    metadata = _parse_metadata(raw)
    if metadata.frequency != expected_frequency:
        raise DeepARArtifactError(
            f"DeepAR frequency {metadata.frequency!r} does not match "
            f"{expected_frequency!r}"
        )
    if metadata.horizon != expected_horizon:
        raise DeepARArtifactError(
            f"DeepAR horizon {metadata.horizon} does not match {expected_horizon}"
        )
    if expected_model_ref is not None and metadata.model_ref != expected_model_ref:
        raise DeepARArtifactError(
            f"DeepAR model_ref {metadata.model_ref!r} does not match "
            f"{expected_model_ref!r}"
        )
    if (
        expected_params_version is not None
        and metadata.params_version != expected_params_version
    ):
        raise DeepARArtifactError(
            f"DeepAR params_version {metadata.params_version!r} does not match "
            f"{expected_params_version!r}"
        )

    actual_sha256 = checkpoint_sha256(checkpoint)
    if actual_sha256 != metadata.artifact_sha256:
        raise DeepARArtifactError(
            f"DeepAR checkpoint checksum mismatch: expected "
            f"{metadata.artifact_sha256}, got {actual_sha256}"
        )

    return DeepARArtifact(root=path, metadata=metadata)


class DeepARRunner(Protocol):
    """Framework-specific inference capability supplied by the GPU runtime."""

    def predict(
        self,
        *,
        series_id: str,
        dates: Sequence[object],
        values: Sequence[float],
        horizon: int,
    ) -> tuple[DeepARForecast, ...]:
        """Return point predictions and neural prediction intervals."""
        ...


class DeepARModel:
    """Common model-port adapter around an injected DeepAR runner.

    The runner is deliberately injected because NeuralForecast belongs to the
    GPU deployment, not the CPU API process. The neural runner must return
    prediction intervals; the CPU adapter does not invent them from residuals.
    """

    name = "deepar"

    def __init__(self, runner: DeepARRunner) -> None:
        self._runner = runner

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        values = _history(request, minimum=1, model=self.name)
        dates = tuple(
            date
            for date, available in zip(
                request.series.dates,
                request.availability.available_mask,
                strict=True,
            )
            if available
        )
        forecasts = self._runner.predict(
            series_id=request.series.series_id,
            dates=dates,
            values=values,
            horizon=request.horizon,
        )
        if len(forecasts) != request.horizon:
            raise ModelError(
                f"DeepAR returned {len(forecasts)} periods for horizon "
                f"{request.horizon}"
            )

        return tuple(
            ForecastPeriod(
                period_start=week,
                estimate=max(forecast.estimate, 0.0),
                lower=max(forecast.lower, 0.0),
                upper=max(forecast.upper, forecast.estimate, 0.0),
            )
            for week, forecast in zip(
                future_weeks(request.series.dates[-1], request.horizon),
                forecasts,
                strict=True,
            )
        )

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del request
        total = sum(period.estimate for period in periods)
        lower = sum(period.lower for period in periods)
        upper = sum(period.upper for period in periods)
        return ForecastTotal(estimate=total, lower=lower, upper=upper)


def _parse_metadata(raw: Mapping[str, object]) -> DeepARArtifactMetadata:
    values = {
        key: _required_string(raw, key)
        for key in (
            "model_ref",
            "checkpoint_version",
            "training_period",
            "library_version",
            "artifact_sha256",
            "frequency",
            "params_version",
        )
    }
    horizon = raw.get("horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise DeepARArtifactError("DeepAR metadata 'horizon' must be positive")
    runtime_format = raw.get("runtime_format", "neuralforecast_save")
    if not isinstance(runtime_format, str) or not runtime_format:
        raise DeepARArtifactError(
            "DeepAR metadata 'runtime_format' must be a non-empty string"
        )
    return DeepARArtifactMetadata(
        horizon=horizon,
        runtime_format=runtime_format,
        **values,
    )


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise DeepARArtifactError(f"DeepAR metadata {key!r} must be a non-empty string")
    return value


def checkpoint_sha256(checkpoint: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in checkpoint.rglob("*") if path.is_file())
    if not files:
        raise DeepARArtifactError(f"DeepAR checkpoint is empty: {checkpoint}")
    for path in files:
        relative = path.relative_to(checkpoint).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_neuralforecast_runner(artifact: DeepARArtifact) -> DeepARRunner:
    """Load NeuralForecast lazily in the GPU runtime.

    This function is intentionally not called by the CPU composition root.
    """
    try:
        module = import_module("neuralforecast")
    except ImportError as error:
        raise DeepARArtifactError(
            "DeepAR runtime requires the optional neuralforecast dependency"
        ) from error

    try:
        neural_forecast = module.NeuralForecast.load(
            path=str(artifact.root / "checkpoint"),
            verbose=False,
            allow_pickle=False,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise DeepARArtifactError(
            f"could not load NeuralForecast checkpoint: {error}"
        ) from error
    return _NeuralForecastRunner(neural_forecast)


class _NeuralForecastRunner:
    def __init__(self, neural_forecast: Any) -> None:
        self._neural_forecast = neural_forecast

    def predict(
        self,
        *,
        series_id: str,
        dates: Sequence[object],
        values: Sequence[float],
        horizon: int,
    ) -> tuple[DeepARForecast, ...]:
        try:
            pandas = import_module("pandas")
            frame = pandas.DataFrame(
                {
                    "unique_id": [series_id] * len(values),
                    "ds": pandas.to_datetime(list(dates)),
                    "y": list(values),
                }
            )
            result = self._neural_forecast.predict(df=frame, h=horizon)
            columns = {str(column).lower(): column for column in result.columns}
            point_column = _column(columns, "deepar", "deepar-median")
            lower_column = _column(
                columns,
                "deepar-lo-80",
                "deepar-lo-90",
            )
            upper_column = _column(
                columns,
                "deepar-hi-80",
                "deepar-hi-90",
            )
            tail = result.tail(horizon)
            return tuple(
                DeepARForecast(
                    estimate=float(row[point_column]),
                    lower=float(row[lower_column]),
                    upper=float(row[upper_column]),
                )
                for _, row in tail.iterrows()
            )
        except (ImportError, KeyError, StopIteration, TypeError, ValueError) as error:
            raise ModelError(f"DeepAR inference failed: {error}") from error


def _column(columns: Mapping[str, object], *names: str) -> object:
    for name in names:
        if name in columns:
            return columns[name]
    raise KeyError(f"DeepAR prediction is missing columns: {', '.join(names)}")
