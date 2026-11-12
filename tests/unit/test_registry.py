"""Tests for the model registry."""

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.domain.types import ForecastPeriod
from pango_prediction.ports.model import ModelRequest
from pango_prediction.ports.registry import (
    ModelNotRegisteredError,
    ModelRegistry,
)


class StubModel:
    """A `ForecastModel` that forecasts nothing.

    It satisfies the port by shape alone, inheriting from nothing, which is the
    point of the port being a `Protocol`: these tests exercise the registry
    without dragging in a real model's arithmetic.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        return ()


# --- resolving a name ---


def test_resolve_returns_the_model_registered_under_that_name() -> None:
    naive = StubModel("naive")
    registry = ModelRegistry(StubModel("zero"), naive)

    assert registry.resolve("naive") is naive


def test_resolving_an_unknown_name_raises() -> None:
    registry = ModelRegistry(StubModel("naive"))

    with pytest.raises(ModelNotRegisteredError):
        registry.resolve("croston")


def test_the_error_names_what_is_registered() -> None:
    """A policy typo is one character; the message has to make it visible."""
    registry = ModelRegistry(StubModel("moving_average"), StubModel("naive"))

    with pytest.raises(ModelNotRegisteredError) as raised:
        registry.resolve("moving-average")

    message = str(raised.value)
    assert "'moving-average'" in message
    assert "moving_average" in message
    assert "naive" in message


def test_an_empty_registry_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(ModelNotRegisteredError, match="no models"):
        ModelRegistry().resolve("naive")


def test_a_missing_model_is_not_a_model_error() -> None:
    """It is a build/policy mismatch, and maps to a different contract code.

    If it were a `ModelError`, a future ``except ModelError`` around the
    forecast call would report MODEL_FAILED for every series, hiding the fact
    that the deployment and the policy disagree about what exists.
    """
    from pango_prediction.ports.model import ModelError

    assert not issubclass(ModelNotRegisteredError, ModelError)


# --- refusing an ambiguous registry ---


def test_two_models_cannot_share_a_name() -> None:
    """Silent shadowing would make the policy's name mean whichever won."""
    with pytest.raises(ValueError, match="two models are registered"):
        ModelRegistry(StubModel("naive"), StubModel("naive"))


@pytest.mark.parametrize("name", ["", " ", "naive ", " naive"])
def test_a_blank_or_padded_name_is_rejected(name: str) -> None:
    """``"naive "`` would never match the policy's ``"naive"``."""
    with pytest.raises(ValueError, match="non-empty and unpadded"):
        ModelRegistry(StubModel(name))


# --- the registry as a collection ---


def test_names_are_sorted() -> None:
    registry = ModelRegistry(
        StubModel("zero"), StubModel("moving_average"), StubModel("naive")
    )

    assert registry.names == ("moving_average", "naive", "zero")


def test_membership_and_size() -> None:
    registry = ModelRegistry(StubModel("naive"), StubModel("zero"))

    assert "naive" in registry
    assert "croston" not in registry
    assert len(registry) == 2


def test_a_registry_cannot_be_edited_after_it_is_built() -> None:
    """What a name resolves to is fixed at composition, not per request."""
    registry = ModelRegistry(StubModel("naive"))

    with pytest.raises(TypeError):
        registry._models["croston"] = StubModel("croston")  # type: ignore[index]


# --- what this build ships ---


def test_the_default_registry_ships_the_baselines_and_sba() -> None:
    assert default_registry().names == ("moving_average", "naive", "sba", "zero")


def test_each_default_model_answers_to_the_name_it_is_registered_under() -> None:
    """The guard against a rename touching one of the two places only."""
    registry = default_registry()

    for name in registry.names:
        assert registry.resolve(name).name == name


# Route A's policy enum, from its reply on the policy artifact. A model the
# policy cannot name is a model no series will ever be routed to.
ROUTE_A_POLICY_MODELS = frozenset(
    {
        "naive",
        "seasonal_naive_block",
        "moving_average",
        "croston",
        "sba",
        "tsb",
        "adida",
        "imapa",
        "ets",
        "theta",
        "pooled_rate",
    }
)


def test_only_the_benchmark_is_unreachable_from_the_policy() -> None:
    """`zero` is registered but absent from Route A's enum, on purpose.

    It is the benchmark every other model has to beat, so this build needs it
    whether or not a policy can select it. Whether the policy *should* be able
    to route inactive series (26 weeks without a sale, notebook S29) to a zero
    forecast is an open question with Route A: `pooled_rate` is the only option
    the enum currently offers them.

    This assertion is exact so that it fails the moment that question is
    answered either way, instead of letting the two lists drift apart quietly.
    """
    unreachable = set(default_registry().names) - ROUTE_A_POLICY_MODELS

    assert unreachable == {"zero"}, (
        "the models this build ships no longer line up with Route A's policy "
        f"enum: {unreachable}"
    )
