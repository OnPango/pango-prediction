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
from pango_prediction.ports.registry import ModelRegistry


def default_registry() -> ModelRegistry:
    """The models available in the CPU-only service.

    The baselines and SBA so far. Further candidates (tsb, adida, ets, theta,
    ...) and the seasonal block model get registered here as they are
    implemented; a neural runtime only in a build that carries a checkpoint.

    A function rather than a module-level constant, so each caller gets its own
    instance and a test can build a different registry without having to undo a
    global one.
    """
    return ModelRegistry(
        ZeroModel(),
        NaiveModel(),
        MovingAverageModel(),
        SbaModel(),
    )
