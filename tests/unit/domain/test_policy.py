import json

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.policy import PolicyError, load_policy
from pango_prediction.domain.types import (
    DemandClass,
    DemandProfile,
    HistoryClass,
    StockHandling,
)


def profile(
    demand_class: DemandClass = DemandClass.SMOOTH,
    *,
    inactive: bool = False,
    evaluable: bool = True,
) -> DemandProfile:
    return DemandProfile(
        series_id="series-1",
        demand_class=demand_class,
        history_class=HistoryClass.MATURE,
        stock_handling=StockHandling.STOCK_MANAGED,
        adi=1.0,
        cv2=0.1,
        observable_months=12,
        nonzero_months=12,
        history_weeks=52,
        observed_weeks=52,
        weeks_since_last_sale=0,
        inactive=inactive,
        evaluable=evaluable,
    )


def policy_data() -> dict[str, object]:
    return {
        "policy_version": "test-1",
        "frequency": "WEEKLY",
        "horizon": 4,
        "params_version": DEFAULT_PARAMETERS.fingerprint(),
        "rules": [
            {"when": {"inactive": True}, "reason": "inactive"},
            {"when": {"demand_class": "SMOOTH"}, "model": "naive"},
        ],
        "default": {"model": "zero"},
    }


def names() -> frozenset[str]:
    return frozenset(default_registry().names)


def test_loads_and_resolves_ordered_rules() -> None:
    policy = load_policy(policy_data(), registry_names=names(), parameters=DEFAULT_PARAMETERS)

    decision = policy.resolve(profile())
    inactive = policy.resolve(profile(inactive=True))

    assert decision.model is not None
    assert decision.model.name == "naive"
    assert inactive.model is None
    assert inactive.reason == "inactive"


def test_default_rule_handles_unmatched_profile() -> None:
    policy = load_policy(policy_data(), registry_names=names(), parameters=DEFAULT_PARAMETERS)

    decision = policy.resolve(profile(DemandClass.INSUFFICIENT))

    assert decision.model is not None
    assert decision.model.name == "zero"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frequency", "DAILY", "frequency"),
        ("horizon", 3, "horizon"),
        ("params_version", "wrong", "params_version"),
    ],
)
def test_rejects_incompatible_policy(
    field: str,
    value: object,
    message: str,
) -> None:
    data = policy_data()
    data[field] = value

    with pytest.raises(PolicyError, match=message):
        load_policy(data, registry_names=names(), parameters=DEFAULT_PARAMETERS)


def test_rejects_unregistered_model() -> None:
    data = policy_data()
    data["rules"] = [{"model": "not_a_model"}]

    with pytest.raises(PolicyError, match="not registered"):
        load_policy(data, registry_names=names(), parameters=DEFAULT_PARAMETERS)


def test_loads_json_text() -> None:
    serialized = json.dumps(policy_data())

    policy = load_policy(serialized, registry_names=names(), parameters=DEFAULT_PARAMETERS)

    assert policy.policy_version == "test-1"
