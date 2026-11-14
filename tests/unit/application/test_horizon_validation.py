"""The request horizon has to be the one the policy was calibrated for.

Intervals and horizon totals are fitted for the policy's horizon, so a policy
evaluated at four weeks has nothing honest to say about eight. A mismatch is an
integration fault on the caller's side and concerns the whole request, so it is
refused as a whole rather than reported series by series, where it could be
skimmed past.
"""

from datetime import date

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.application.errors import HorizonMismatchError
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastService,
)
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.policy import ModelPolicy, load_policy
from pango_prediction.domain.types import DemandObservation, ForecastSeriesInput

HISTORY_END = date(2026, 11, 1)
POLICY_HORIZON = 4


def policy() -> ModelPolicy:
    return load_policy(
        {
            "policy_version": "test-1",
            "frequency": "WEEKLY",
            "horizon": POLICY_HORIZON,
            "params_version": DEFAULT_PARAMETERS.fingerprint(),
            "rules": [{"when": {"inactive": True}, "reason": "Not forecast."}],
            "default": {"model": "naive"},
        },
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )


def command(horizon: int) -> ForecastCommand:
    series = ForecastSeriesInput(
        series_id="1042",
        product_id="318",
        warehouse_id="1",
        demand=(DemandObservation(date=date(2026, 10, 28), quantity=5.0),),
        stock=(),
    )
    return ForecastCommand(history_end=HISTORY_END, horizon=horizon, series=(series,))


@pytest.mark.parametrize("horizon", [1, 3, 5, 8])
def test_a_horizon_the_policy_was_not_calibrated_for_refuses_the_request(
    horizon: int,
) -> None:
    service = ForecastService(policy(), default_registry())

    with pytest.raises(HorizonMismatchError) as raised:
        service.run(command(horizon))

    assert raised.value.requested == horizon
    assert raised.value.expected == POLICY_HORIZON
    assert "test-1" in str(raised.value)


def test_the_policy_horizon_is_served() -> None:
    service = ForecastService(policy(), default_registry())

    batch = service.run(command(POLICY_HORIZON))

    assert len(batch.outcomes) == 1


def test_without_a_policy_there_is_no_horizon_to_disagree_with() -> None:
    """No policy means no model runs, so no calibration can be violated.

    Each series already answers MODEL_NOT_AVAILABLE in that state; refusing the
    request as well would hide the profile the service can still report.
    """
    batch = ForecastService().run(command(8))

    assert len(batch.outcomes) == 1
