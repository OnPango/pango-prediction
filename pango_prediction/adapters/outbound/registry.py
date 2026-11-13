"""Which concrete models this build ships.

Composition, not logic. This is the one module that knows the baselines exist,
which is why it lives in `adapters/outbound`: the application resolves models
through `ports.registry` and must never import an implementation.
"""

from pango_prediction.adapters.outbound.baselines import (
    MovingAverageModel,
    NaiveModel,
    ZeroModel,
)
from pango_prediction.adapters.outbound.croston import SbaModel
from pango_prediction.adapters.outbound.statsforecast import statsforecast_models
from pango_prediction.ports.model import ForecastModel
from pango_prediction.ports.registry import ModelRegistry


def default_registry(*, deepar: ForecastModel | None = None) -> ModelRegistry:
    """The models available in the CPU-only service.

    The baselines, SBA and the local StatsForecast candidates. DeepAR remains
    separate because it requires a versioned checkpoint and a neural runtime.

    A function rather than a module-level constant, so each caller gets its own
    instance and a test can build a different registry without having to undo a
    global one.
    """
    models: list[ForecastModel] = [
        ZeroModel(),
        NaiveModel(),
        MovingAverageModel(),
        SbaModel(),
        *statsforecast_models(),
    ]
    if deepar is not None:
        models.append(deepar)
    return ModelRegistry(*models)
