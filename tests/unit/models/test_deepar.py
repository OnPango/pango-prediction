import hashlib
import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from pango_prediction.adapters.outbound.deepar import (
    DeepARArtifactError,
    DeepARForecast,
    DeepARModel,
    load_deepar_artifact,
)
from pango_prediction.domain.types import (
    AvailabilityState,
    RegularizedSeries,
    StockHandling,
    WeeklyAvailability,
)
from pango_prediction.ports.model import ModelRequest


def artifact(tmp_path: Path) -> Path:
    root = tmp_path / "deepar-equipmaster-v1"
    checkpoint = root / "checkpoint"
    checkpoint.mkdir(parents=True)
    (checkpoint / "model.safetensors").write_bytes(b"development-checkpoint")
    digest = hashlib.sha256()
    digest.update(b"model.safetensors\0")
    digest.update(b"development-checkpoint")
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "model_ref": "deepar-equipmaster-v1",
                "checkpoint_version": "1",
                "training_period": "2023-01/2026-08",
                "library_version": "neuralforecast-test",
                "artifact_sha256": digest.hexdigest(),
                "frequency": "WEEKLY",
                "horizon": 4,
                "params_version": "params-1",
            }
        ),
        encoding="utf-8",
    )
    return root


def test_artifact_loader_validates_metadata_and_checksum(tmp_path: Path) -> None:
    loaded = load_deepar_artifact(
        artifact(tmp_path),
        expected_model_ref="deepar-equipmaster-v1",
        expected_params_version="params-1",
    )

    assert loaded.metadata.horizon == 4
    assert loaded.metadata.model_ref == "deepar-equipmaster-v1"


def test_artifact_loader_rejects_tampering(tmp_path: Path) -> None:
    root = artifact(tmp_path)
    (root / "checkpoint" / "model.safetensors").write_bytes(b"tampered")

    with pytest.raises(DeepARArtifactError, match="checksum mismatch"):
        load_deepar_artifact(root)


class FakeRunner:
    def predict(
        self,
        *,
        series_id: str,
        dates: Sequence[object],
        values: Sequence[float],
        horizon: int,
    ) -> tuple[DeepARForecast, ...]:
        assert series_id == "PW-001"
        assert tuple(dates) == (
            date(2026, 1, 5),
            date(2026, 1, 19),
        )
        assert tuple(values) == (4.0, 6.0)
        return (DeepARForecast(estimate=5.0, lower=4.0, upper=6.0),) * horizon


def test_deepar_model_passes_only_observable_history_to_runner() -> None:
    dates = tuple(date(2026, 1, 5) + timedelta(weeks=index) for index in range(3))
    series = RegularizedSeries("PW-001", dates, (4.0, 0.0, 6.0))
    availability = WeeklyAvailability(
        series_id="PW-001",
        dates=dates,
        states=(
            AvailabilityState.AVAILABLE,
            AvailabilityState.CENSORED,
            AvailabilityState.AVAILABLE,
        ),
        available_mask=(True, False, True),
        closing_stock=(1.0, 0.0, 1.0),
        stock_handling=StockHandling.STOCK_MANAGED,
    )

    periods = DeepARModel(FakeRunner()).predict(
        ModelRequest(series=series, availability=availability, horizon=2)
    )

    assert [period.estimate for period in periods] == [5.0, 5.0]
