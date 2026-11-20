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
    assert DEFAULT_PARAMETERS.inactivity_weeks == 26
    assert DEFAULT_PARAMETERS.new_history_max_weeks == 13
    assert DEFAULT_PARAMETERS.limited_history_max_weeks == 52
    assert DEFAULT_PARAMETERS.min_observed_weeks == 8
    assert DEFAULT_PARAMETERS.weekday_weights == (1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.0)


def test_the_classification_window_uses_route_a_calibration() -> None:
    """Route A fixed the S30/A5 classification window at 24 months."""
    assert DEFAULT_PARAMETERS.classification_window_months == 24


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
    assert DomainParameters(inactivity_weeks=13).fingerprint() != baseline
    assert DomainParameters(new_history_max_weeks=8).fingerprint() != baseline
    assert DomainParameters(limited_history_max_weeks=78).fingerprint() != baseline
    assert DomainParameters(min_observed_weeks=12).fingerprint() != baseline
    assert DomainParameters(classification_window_months=12).fingerprint() != baseline
    assert (
        DomainParameters(weekday_weights=(1.0,) * 7).fingerprint() != baseline
    )


def test_the_default_fingerprint_matches_the_documented_one() -> None:
    """Pinned on purpose.

    `docs/domain-rules.md` §11 prints this value, and Route A audits parity
    against it. If a default changes, this test fails and the document has to
    change in the same commit, instead of the hash drifting unnoticed.
    """
    assert DEFAULT_PARAMETERS.fingerprint() == "f5f416da285217b5"


def test_the_fingerprint_does_not_depend_on_how_it_was_built() -> None:
    explicit = DomainParameters(
        min_observed_weeks=8,
        min_weeks_with_stock_data=4,
        made_to_order_threshold=0.5,
        limited_history_max_weeks=52,
        new_history_max_weeks=13,
        max_stockout_episode_weeks=13,
        inactivity_weeks=26,
        classification_window_months=24,
        weekday_weights=(1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.0),
    )

    assert explicit.fingerprint() == DEFAULT_PARAMETERS.fingerprint()


def test_an_inactivity_window_below_one_week_is_rejected() -> None:
    with pytest.raises(ValueError, match="inactivity_weeks"):
        DomainParameters(inactivity_weeks=0)


def test_limited_history_must_be_longer_than_new_history() -> None:
    """Otherwise the LIMITED class could never be reached."""
    with pytest.raises(ValueError, match="greater than"):
        DomainParameters(new_history_max_weeks=20, limited_history_max_weeks=20)


def test_requiring_fewer_than_one_observed_week_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_observed_weeks"):
        DomainParameters(min_observed_weeks=0)


def test_a_classification_window_of_zero_months_is_rejected() -> None:
    """Zero would mean classifying on no monthly history."""
    with pytest.raises(ValueError, match="classification_window_months"):
        DomainParameters(classification_window_months=0)


def test_a_full_week_is_derived_from_the_weight_table() -> None:
    """Stored separately, the two could disagree about what 5.6 means."""
    assert sum(DEFAULT_PARAMETERS.weekday_weights) == pytest.approx(5.6)
