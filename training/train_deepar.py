"""Train a temporary local DeepAR artifact for development.

This script is offline-only. It is not imported by the API and must never be
called from a forecast request. Route A should eventually replace it with the
reproducible training job that produces the production policy artifact.
"""

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from pango_prediction.adapters.outbound.deepar import checkpoint_sha256


def main() -> None:
    arguments = _arguments()
    try:
        import pandas as pd  # type: ignore[import-untyped]
        from neuralforecast import NeuralForecast  # type: ignore[import-untyped]
        from neuralforecast.losses.pytorch import (  # type: ignore[import-untyped]
            DistributionLoss,
        )
        from neuralforecast.models import DeepAR  # type: ignore[import-untyped]
    except ImportError as error:
        raise SystemExit(
            "Install the optional DeepAR development dependencies before running "
            "this script: pip install neuralforecast pandas torch"
        ) from error

    frame = pd.read_csv(arguments.input)
    required = {"unique_id", "ds", "y"}
    missing = required.difference(frame.columns)
    if missing:
        raise SystemExit(f"training CSV is missing columns: {', '.join(sorted(missing))}")
    frame["ds"] = pd.to_datetime(frame["ds"])
    frame["y"] = frame["y"].astype(float)
    if (frame["y"] < 0).any():
        raise SystemExit("training demand cannot be negative")

    model = DeepAR(
        h=arguments.horizon,
        input_size=arguments.input_size,
        loss=DistributionLoss(distribution="StudentT", level=[80, 90]),
        max_steps=arguments.max_steps,
        random_seed=arguments.seed,
        enable_checkpointing=False,
    )
    forecast = NeuralForecast(models=[model], freq="W-MON")
    forecast.fit(df=frame, verbose=False)

    output = arguments.output
    checkpoint = output / "checkpoint"
    checkpoint.mkdir(parents=True, exist_ok=True)
    # NeuralForecast serializes the fitted weights as safetensors. Its
    # DistributionLoss may contain a non-leaf tensor in hparams after fitting,
    # so remove that runtime object; the loader reconstructs the default loss.
    forecast.models[0].hparams.pop("loss", None)
    forecast.save(path=str(checkpoint), overwrite=True)

    metadata: dict[str, Any] = {
        "model_ref": arguments.model_ref,
        "checkpoint_version": arguments.checkpoint_version,
        "training_period": (
            f"{frame['ds'].min().date().isoformat()}/"
            f"{frame['ds'].max().date().isoformat()}"
        ),
        "library_version": "neuralforecast",
        "artifact_sha256": checkpoint_sha256(checkpoint),
        "frequency": "WEEKLY",
        "horizon": arguments.horizon,
        "params_version": arguments.params_version,
        "seed": arguments.seed,
        "input_size": arguments.input_size,
        "max_steps": arguments.max_steps,
        "runtime_format": "neuralforecast_save",
        "created_at": date.today().isoformat(),
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-ref", default="deepar-dev-v1")
    parser.add_argument("--checkpoint-version", default="dev-1")
    parser.add_argument("--params-version", default="f5f416da285217b5")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--input-size", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args()


if __name__ == "__main__":
    main()
