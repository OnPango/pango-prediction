import pytest

from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    FINGERPRINT_LENGTH,
    DomainParameters,
)


def test_the_defaults_are_the_calibration_from_the_demand_analysis() -> None:
    """These are EquipMaster's values (S24, S26), not universal rules."""
    assert DEFAULT_PARAMETERS.max_stockout_episode_weeks == 13
    assert DEFAULT_PARAMETERS.made_to_order_threshold == 0.5
    assert DEFAULT_PARAMETERS.min_weeks_with_stock_data == 4


def test_parameters_are_immutable() -> None:
    """A tenant's calibration cannot drift halfway through a forecast run."""
    with pytest.raises(AttributeError):
        DEFAULT_PARAMETERS.max_stockout_episode_weeks = 4  # type: ignore[misc]


# --- validation, so a policy cannot ship a nonsensical calibration ---


def test_a_stockout_threshold_below_one_week_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_stockout_episode_weeks"):
        DomainParameters(max_stockout_episode_weeks=0)


def test_a_made_to_order_threshold_of_zero_is_rejected() -> None:
    """At zero every series with stock data would be read as sold to order."""
    with pytest.raises(ValueError, match="made_to_order_threshold"):
        DomainParameters(made_to_order_threshold=0.0)


def test_a_made_to_order_threshold_above_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="made_to_order_threshold"):
        DomainParameters(made_to_order_threshold=1.5)


def test_a_made_to_order_threshold_of_one_is_allowed() -> None:
    """Every selling week must open empty: strict, but meaningful."""
    assert DomainParameters(made_to_order_threshold=1.0)


def test_requiring_fewer_than_one_week_of_stock_data_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_weeks_with_stock_data"):
        DomainParameters(min_weeks_with_stock_data=0)


# --- the fingerprint, which is what makes parity auditable per tenant ---


def test_the_fingerprint_is_stable_across_instances() -> None:
    assert DomainParameters().fingerprint() == DomainParameters().fingerprint()


def test_the_fingerprint_has_the_documented_length() -> None:
    assert len(DEFAULT_PARAMETERS.fingerprint()) == FINGERPRINT_LENGTH


def test_changing_any_value_changes_the_fingerprint() -> None:
    """Otherwise two different calibrations could claim the same parity hash."""
    baseline = DomainParameters().fingerprint()

    assert DomainParameters(max_stockout_episode_weeks=8).fingerprint() != baseline
    assert DomainParameters(made_to_order_threshold=0.75).fingerprint() != baseline
    assert DomainParameters(min_weeks_with_stock_data=6).fingerprint() != baseline


def test_the_default_fingerprint_matches_the_documented_one() -> None:
    """Pinned on purpose.

    `docs/domain-rules.md` §11 prints this value, and Route A audits parity
    against it. If a default changes, this test fails and the document has to
    change in the same commit, instead of the hash drifting unnoticed.
    """
    assert DEFAULT_PARAMETERS.fingerprint() == "8f7bb9c36953951c"


def test_the_fingerprint_does_not_depend_on_how_it_was_built() -> None:
    explicit = DomainParameters(
        min_weeks_with_stock_data=4,
        made_to_order_threshold=0.5,
        max_stockout_episode_weeks=13,
    )

    assert explicit.fingerprint() == DEFAULT_PARAMETERS.fingerprint()
