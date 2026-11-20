"""Versioned model-policy loading and resolution.

The policy is an artifact produced by Route A and consumed by Route B. It
selects a model from an already computed demand profile; it does not change
the domain classification itself.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pango_prediction.domain.parameters import DomainParameters
from pango_prediction.domain.types import (
    DemandClass,
    DemandProfile,
    HistoryClass,
    ModelSpec,
    NoForecastReason,
    StockHandling,
)


class PolicyError(ValueError):
    """The policy artifact is invalid or cannot resolve a series."""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The result of applying policy rules to one demand profile.

    Three shapes, and the difference matters downstream:

    - a ``model``: forecast the series with it;
    - no model and a ``reason``: the policy declines the series on purpose;
    - neither: the policy has nothing for the series and does not say why,
      which is reported as a missing model rather than as a decision.
    """

    model: ModelSpec | None
    reason: NoForecastReason | None = None


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One ordered policy rule.

    Missing conditions are wildcards. Rules are evaluated in declaration order
    so a specific rule can precede a broader fallback.
    """

    model: ModelSpec | None
    reason: NoForecastReason | None
    demand_class: DemandClass | None = None
    history_class: HistoryClass | None = None
    stock_handling: StockHandling | None = None
    inactive: bool | None = None
    evaluable: bool | None = None

    def matches(self, profile: DemandProfile) -> bool:
        return all(
            (
                self.demand_class is None
                or profile.demand_class is self.demand_class,
                self.history_class is None
                or profile.history_class is self.history_class,
                self.stock_handling is None
                or profile.stock_handling is self.stock_handling,
                self.inactive is None or profile.inactive is self.inactive,
                self.evaluable is None or profile.evaluable is self.evaluable,
            )
        )


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    """A validated policy artifact ready for per-series resolution."""

    policy_version: str
    frequency: str
    horizon: int
    params_version: str
    rules: tuple[PolicyRule, ...]
    default: PolicyRule | None

    def resolve(self, profile: DemandProfile) -> PolicyDecision:
        for rule in self.rules:
            if rule.matches(profile):
                return PolicyDecision(rule.model, rule.reason)

        if self.default is not None:
            return PolicyDecision(self.default.model, self.default.reason)

        raise PolicyError(
            f"policy {self.policy_version!r} has no rule for series "
            f"{profile.series_id!r}"
        )


def load_policy(
    source: str | Path | Mapping[str, object],
    *,
    registry_names: frozenset[str],
    parameters: DomainParameters,
    expected_horizon: int = 4,
) -> ModelPolicy:
    """Load and validate a JSON policy from a path, JSON string or mapping."""
    raw = _read_source(source)

    policy_version = _required_string(raw, "policy_version")
    frequency = _required_string(raw, "frequency")
    if frequency != "WEEKLY":
        raise PolicyError(f"unsupported policy frequency {frequency!r}")

    horizon = _required_positive_int(raw, "horizon")
    if horizon != expected_horizon:
        raise PolicyError(
            f"policy horizon {horizon} does not match expected horizon "
            f"{expected_horizon}"
        )
    params_version = _required_string(raw, "params_version")
    if params_version != parameters.fingerprint():
        raise PolicyError(
            f"policy params_version {params_version!r} does not match domain "
            f"parameters {parameters.fingerprint()!r}"
        )

    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise PolicyError("policy 'rules' must be a non-empty array")

    rules = tuple(
        _parse_rule(item, registry_names, f"rules[{index}]")
        for index, item in enumerate(raw_rules)
    )

    raw_default = raw.get("default")
    default = (
        None
        if raw_default is None
        else _parse_rule(raw_default, registry_names, "default")
    )

    return ModelPolicy(
        policy_version=policy_version,
        frequency=frequency,
        horizon=horizon,
        params_version=params_version,
        rules=rules,
        default=default,
    )


def _read_source(
    source: str | Path | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(source, Mapping):
        return source

    if isinstance(source, str) and source.lstrip().startswith("{"):
        try:
            value = json.loads(source)
        except json.JSONDecodeError as error:
            raise PolicyError("policy source is not valid JSON") from error
        return _object(value, "policy")

    path = Path(source)

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PolicyError(f"could not read policy {path}") from error
    except json.JSONDecodeError as error:
        raise PolicyError(f"policy {path} is not valid JSON") from error

    return _object(value, "policy")


def _parse_rule(
    raw: object,
    registry_names: frozenset[str],
    location: str,
) -> PolicyRule:
    value = _object(raw, location)
    when = value.get("when", {})
    conditions = _object(when, f"{location}.when")

    model_name = value.get("model")
    reason = value.get("reason")

    if model_name is not None:
        if not isinstance(model_name, str) or not model_name:
            raise PolicyError(f"{location}.model must be a non-empty string")
        if model_name not in registry_names:
            raise PolicyError(
                f"{location}.model {model_name!r} is not registered; "
                f"available models: {', '.join(sorted(registry_names)) or 'none'}"
            )

    no_forecast_reason = _reason(reason, location)

    if model_name is not None and no_forecast_reason is not None:
        raise PolicyError(
            f"{location} states both a model and a reason; a rule either "
            "forecasts a series or declines it"
        )

    params = value.get("params", {})
    if not isinstance(params, Mapping):
        raise PolicyError(f"{location}.params must be an object")

    return PolicyRule(
        model=None if model_name is None else ModelSpec(model_name, dict(params)),
        reason=no_forecast_reason,
        demand_class=_enum_condition(
            conditions, "demand_class", DemandClass, location
        ),
        history_class=_enum_condition(
            conditions, "history_class", HistoryClass, location
        ),
        stock_handling=_enum_condition(
            conditions, "stock_handling", StockHandling, location
        ),
        inactive=_bool_condition(conditions, "inactive", location),
        evaluable=_bool_condition(conditions, "evaluable", location),
    )


def _reason(raw: object, location: str) -> NoForecastReason | None:
    """Read a rule's ``reason`` from the closed no-forecast vocabulary.

    Checked as strictly as a model name, and for the same purpose: a misspelt
    reason would otherwise turn a declined series into something else without
    anyone being told. It surfaces here, when the artifact is loaded.
    """
    if raw is None:
        return None

    allowed = ", ".join(reason.value for reason in NoForecastReason)

    if not isinstance(raw, str):
        raise PolicyError(f"{location}.reason must be one of: {allowed}")

    try:
        return NoForecastReason(raw)
    except ValueError as error:
        raise PolicyError(
            f"{location}.reason {raw!r} is not a no-forecast reason; "
            f"expected one of: {allowed}"
        ) from error


def _enum_condition(
    conditions: Mapping[str, object],
    key: str,
    enum_type: type[DemandClass] | type[HistoryClass] | type[StockHandling],
    location: str,
) -> Any:
    value = conditions.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyError(f"{location}.when.{key} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise PolicyError(
            f"{location}.when.{key} has unsupported value {value!r}"
        ) from error


def _bool_condition(
    conditions: Mapping[str, object],
    key: str,
    location: str,
) -> bool | None:
    value = conditions.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PolicyError(f"{location}.when.{key} must be a boolean")
    return value


def _object(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{location} must be an object")
    return value


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise PolicyError(f"policy {key!r} must be a non-empty string")
    return result


def _required_positive_int(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 1:
        raise PolicyError(f"policy {key!r} must be a positive integer")
    return result
