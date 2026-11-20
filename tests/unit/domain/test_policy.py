import json

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.domain.allocation import (
    NO_ACTIVE_WAREHOUSE,
    NON_STOCK_WAREHOUSE,
)
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.policy import PolicyError, load_policy
from pango_prediction.domain.types import (
    DemandClass,
    DemandProfile,
    HistoryClass,
    NoForecastReason,
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


# --- the no-forecast reasons are a closed vocabulary -------------------------
#
# A rule that names no model declines the series, and its `reason` says why.
# Route A closed the set of reasons, so the loader treats it like the model
# names: a value outside it is a broken artifact, refused when the policy is
# loaded rather than discovered one series at a time in production.


def policy_with_rule(rule: dict[str, object]) -> dict[str, object]:
    data = policy_data()
    data["rules"] = [rule]
    return data


def test_the_vocabulary_is_the_five_reasons_route_a_closed() -> None:
    assert {reason.value for reason in NoForecastReason} == {
        "inactive",
        "sold_to_order",
        "non_stock_warehouse",
        "no_active_warehouse",
        "history_only",
    }


@pytest.mark.parametrize("reason", list(NoForecastReason))
def test_every_agreed_reason_loads_as_its_enum_member(
    reason: NoForecastReason,
) -> None:
    policy = load_policy(
        policy_with_rule({"when": {"inactive": True}, "reason": reason.value}),
        registry_names=names(),
        parameters=DEFAULT_PARAMETERS,
    )

    decision = policy.resolve(profile(inactive=True))

    assert decision.model is None
    assert decision.reason is reason


@pytest.mark.parametrize(
    "reason",
    ["Inactive series are not forecast.", "inactve", "INACTIVE", "", 7],
)
def test_a_reason_outside_the_vocabulary_is_rejected(reason: object) -> None:
    """A typo must not turn a declined series into something else quietly."""
    with pytest.raises(PolicyError, match="reason"):
        load_policy(
            policy_with_rule({"when": {"inactive": True}, "reason": reason}),
            registry_names=names(),
            parameters=DEFAULT_PARAMETERS,
        )


def test_a_rule_cannot_both_forecast_and_decline() -> None:
    """A model and a reason on one rule contradict each other."""
    rule: dict[str, object] = {
        "when": {"inactive": True},
        "model": "naive",
        "reason": "inactive",
    }

    with pytest.raises(PolicyError, match="both"):
        load_policy(
            policy_with_rule(rule),
            registry_names=names(),
            parameters=DEFAULT_PARAMETERS,
        )


def test_a_rule_with_neither_a_model_nor_a_reason_still_loads() -> None:
    """Declining without saying why stays possible, and stays an error path."""
    policy = load_policy(
        policy_with_rule({"when": {"evaluable": False}}),
        registry_names=names(),
        parameters=DEFAULT_PARAMETERS,
    )

    decision = policy.resolve(profile(evaluable=False))

    assert decision.model is None
    assert decision.reason is None


def test_the_allocation_reasons_belong_to_the_vocabulary() -> None:
    """Route A's allocation module names two reasons as plain constants.

    They are left as Route A wrote them, so this is what keeps the two
    spellings from drifting apart.
    """
    assert NON_STOCK_WAREHOUSE == NoForecastReason.NON_STOCK_WAREHOUSE
    assert NO_ACTIVE_WAREHOUSE == NoForecastReason.NO_ACTIVE_WAREHOUSE
