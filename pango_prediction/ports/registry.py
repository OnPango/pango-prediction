"""Binding a policy's model name to something that can forecast.

The policy artifact names a model as a string: ``{"model": "moving_average",
"params": {"window": 26}}``. The registry is the single place where that string
becomes an implementation. That matters twice: a name cannot mean two different
things in one deployment, and a model's own name cannot drift from the name it
answers to.

It sits beside the port rather than in `adapters/outbound` because it holds no
forecasting logic and imports no concrete model: it is a lookup over whatever
satisfies `ForecastModel`. What varies between deployments is its *contents*,
supplied at the composition root. A CPU build registers the baselines and the
StatsForecast wrappers; a build carrying a checkpoint also registers the neural
runtime. Neither variation reaches the application.
"""

from collections.abc import Mapping
from types import MappingProxyType

from pango_prediction.ports.model import ForecastModel


class ModelNotRegisteredError(LookupError):
    """The policy named a model this build does not contain.

    Deliberately *not* a `ModelError`. A `ModelError` means a model tried to
    forecast a series and could not. This means the deployed build and the
    policy disagree about what exists, which is a configuration fault that
    affects every series routed to that name. Keeping the types apart stops a
    future ``except ModelError`` from relabelling a misconfiguration as a
    modelling failure, and the two map to different contract codes:
    MODEL_NOT_AVAILABLE rather than MODEL_FAILED.
    """


class ModelRegistry:
    """An immutable name -> model lookup.

    Models are keyed by their own ``name``, never by a string written out again
    at registration time: a second spelling is a second chance to disagree with
    the policy.
    """

    def __init__(self, *models: ForecastModel) -> None:
        index: dict[str, ForecastModel] = {}

        for model in models:
            name = model.name

            if not name or name != name.strip():
                raise ValueError(
                    f"a model name must be non-empty and unpadded, got {name!r}"
                )

            if name in index:
                raise ValueError(
                    f"two models are registered under the name {name!r}; a "
                    "policy naming it could not say which one it meant"
                )

            index[name] = model

        # A proxy over a dict nothing else holds, so the registry cannot be
        # edited after it is built: a request cannot change what a name means.
        self._models: Mapping[str, ForecastModel] = MappingProxyType(index)

    @property
    def names(self) -> tuple[str, ...]:
        """Every registered name, sorted. Used in errors and in diagnostics."""
        return tuple(sorted(self._models))

    def __contains__(self, name: object) -> bool:
        return name in self._models

    def __len__(self) -> int:
        return len(self._models)

    def __repr__(self) -> str:
        return f"ModelRegistry({', '.join(self.names)})"

    def resolve(self, name: str) -> ForecastModel:
        """The model registered under ``name``.

        Raises `ModelNotRegisteredError` naming what *is* registered, because
        the usual cause is a policy referring to a model this build does not
        ship and the difference is often a single character.
        """
        try:
            return self._models[name]
        except KeyError:
            raise ModelNotRegisteredError(
                f"no model is registered under {name!r}; this build ships "
                f"{', '.join(self.names) or 'no models'}"
            ) from None
