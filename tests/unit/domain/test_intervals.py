import pytest

from pango_prediction.domain.intervals import (
    DEFAULT_QUANTILES,
    IntervalQuantiles,
    empirical_interval,
    quantile,
)

# --- the quantile specification ---


def test_the_median_of_an_odd_sample_is_its_middle_value() -> None:
    assert quantile([3.0, 1.0, 2.0], 0.5) == 2.0


def test_the_median_of_an_even_sample_interpolates() -> None:
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_the_extremes_are_the_order_statistics() -> None:
    values = [5.0, 1.0, 9.0, 3.0]

    assert quantile(values, 0.0) == 1.0
    assert quantile(values, 1.0) == 9.0


def test_interpolation_matches_the_documented_method() -> None:
    """Position = q x (n - 1), then linear between the neighbours.

    With four values and q = 0.10 the position is 0.3, so the answer sits three
    tenths of the way from 0 to 10.
    """
    assert quantile([0.0, 10.0, 20.0, 30.0], 0.10) == pytest.approx(3.0)


def test_a_single_value_is_its_own_quantile() -> None:
    assert quantile([7.0], 0.10) == 7.0
    assert quantile([7.0], 0.90) == 7.0


def test_an_empty_sample_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one value"):
        quantile([], 0.5)


def test_a_quantile_outside_the_unit_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        quantile([1.0, 2.0], 1.5)


# --- the quantile pair the policy supplies ---


def test_the_defaults_are_route_as_choice() -> None:
    assert DEFAULT_QUANTILES.lower == 0.10
    assert DEFAULT_QUANTILES.upper == 0.90


def test_a_reversed_pair_is_rejected() -> None:
    with pytest.raises(ValueError, match="below the upper"):
        IntervalQuantiles(lower=0.9, upper=0.1)


def test_an_equal_pair_is_rejected() -> None:
    with pytest.raises(ValueError, match="below the upper"):
        IntervalQuantiles(lower=0.5, upper=0.5)


def test_a_quantile_of_zero_or_one_is_rejected() -> None:
    """Zero and one are the sample extremes, not an interval."""
    with pytest.raises(ValueError, match="between 0 and 1"):
        IntervalQuantiles(lower=0.0, upper=0.9)

    with pytest.raises(ValueError, match="between 0 and 1"):
        IntervalQuantiles(lower=0.1, upper=1.0)


# --- turning residuals into an interval ---


def test_the_interval_spreads_the_estimate_by_the_residual_quantiles() -> None:
    residuals = [-4.0, -2.0, 0.0, 2.0, 4.0]

    lower, upper = empirical_interval(10.0, residuals, DEFAULT_QUANTILES)

    # position 0.4 of [-4,-2,0,2,4] is -3.2; position 3.6 is 3.2
    assert lower == pytest.approx(6.8)
    assert upper == pytest.approx(13.2)


def test_the_lower_bound_is_clamped_at_zero() -> None:
    """Demand is gross: a negative bound would be meaningless."""
    lower, _ = empirical_interval(1.0, [-9.0, -8.0, -7.0], DEFAULT_QUANTILES)

    assert lower == 0.0


def test_symmetric_residuals_around_zero_keep_the_estimate_centred() -> None:
    lower, upper = empirical_interval(5.0, [-1.0, 1.0], DEFAULT_QUANTILES)

    assert 5.0 - lower == pytest.approx(upper - 5.0)


def test_no_residuals_is_refused_rather_than_given_a_zero_width_interval() -> None:
    """A zero-width interval would satisfy the schema while saying nothing."""
    with pytest.raises(ValueError, match="at least one residual"):
        empirical_interval(5.0, [], DEFAULT_QUANTILES)
