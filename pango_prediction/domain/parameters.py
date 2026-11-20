"""Calibrated domain parameters.

PANGO is multi-tenant, and not every number in the forecasting domain is a
universal rule. Some are calibrations of one company's behaviour:

- the stockout threshold is about four replenishment cycles, so it follows the
  tenant's replenishment interval;
- the made-to-order thresholds were fitted to one catalogue's distribution;
- the history and activity windows are this catalogue's product life cycle;
- the weekday weights describe which days this company trades, and how hard.

Universal values stay as module constants where they are used: a week has seven
days everywhere, the ADI and CV squared cutoffs come from Syntetos and Boylan,
and the float tolerance is not a business rule.

These parameters travel with the versioned policy artifact, which carries a
``params_version`` and a hash of the domain parameters. That hash is what makes
parity auditable per tenant: it proves the evaluation and the production run
used the same calibration.

Parameters that do not exist yet, because the module that reads them is not
built, are listed in `docs/domain-rules.md` so that the policy artifact can
plan for them.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from math import isfinite

from pango_prediction.domain.series import DAYS_PER_WEEK

FINGERPRINT_LENGTH = 16


@dataclass(frozen=True, slots=True)
class DomainParameters:
    """Tenant-scoped calibration of the forecasting domain.

    The defaults are the values the EquipMaster demand analysis arrived at. They
    are the platform's starting point for a tenant that has not been calibrated
    yet, not a claim that they hold everywhere.
    """

    max_stockout_episode_weeks: int = 13
    """Longest stockout episode still read as a stockout (notebook S26).

    Roughly four replenishment cycles for a tenant that replenishes every 19 to
    24 days. A tenant that replenishes weekly needs a much smaller value.
    """

    made_to_order_threshold: float = 0.5
    """Share of selling weeks that may open without stock (notebook S24).

    At or above this share the product is read as sold to order. Moving it to
    0.25 or 0.75 changed the classification of 3.7 % of EquipMaster's units.
    """

    min_weeks_with_stock_data: int = 4
    """Selling weeks with a known opening balance needed to classify (S24)."""

    inactivity_weeks: int = 26
    """Weeks without a sale after which a series is read as inactive (S29).

    About six months. The notebook keeps it despite knowing it strands products
    that only sell in one quarter: only 70 of 3,092 inactive products qualified
    for the seasonal rescue (7.5 C), 1.0 % of the last year's demand.
    """

    new_history_max_weeks: int = 13
    """Longest history still called new (E5). A quarter for this tenant."""

    limited_history_max_weeks: int = 52
    """Longest history still called limited (E5). Above it, mature."""

    min_observed_weeks: int = 8
    """Observable weeks needed to forecast a series on its own (E1: 2 x H).

    At eight weeks 99.8 % of units are evaluable. Tied to the horizon, so a
    tenant forecasting further ahead needs a larger value.
    """

    weekday_weights: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.0)
    """How much of a normal business day each weekday sells, Monday first.

    Saturday is 0.6 because EquipMaster sells about 60 % of a weekday on it
    (11.3), and Sunday is 0 because it does not trade. Both are calibration,
    not rule: a tenant closed on Saturdays needs 0 and one that trades on
    Sundays needs a different table altogether, which is why the whole table
    is a parameter instead of a Saturday weight with the rest hardcoded.

    A full week is the sum, 5.6 with these defaults. It is derived rather than
    stored, so the two can never disagree.
    """

    classification_window_months: int = 24
    """Months of monthly history used to classify the demand profile.

    Route A confirmed the 24-month calibration from S30 and A5. The window is
    recomputed at every cutoff and is part of the policy's parameter
    fingerprint.
    """

    def __post_init__(self) -> None:
        if self.max_stockout_episode_weeks < 1:
            raise ValueError("max_stockout_episode_weeks must be at least 1")

        if not 0 < self.made_to_order_threshold <= 1:
            raise ValueError(
                "made_to_order_threshold must be greater than 0 and at most 1"
            )

        if self.min_weeks_with_stock_data < 1:
            raise ValueError("min_weeks_with_stock_data must be at least 1")

        if len(self.weekday_weights) != DAYS_PER_WEEK:
            raise ValueError(
                f"weekday_weights needs one weight per day, got "
                f"{len(self.weekday_weights)}"
            )

        if any(not isfinite(weight) or weight < 0 for weight in self.weekday_weights):
            raise ValueError("every weekday weight must be finite and non-negative")

        if sum(self.weekday_weights) <= 0:
            raise ValueError(
                "at least one weekday must carry weight, or every week would "
                "have no business days at all"
            )

        if self.inactivity_weeks < 1:
            raise ValueError("inactivity_weeks must be at least 1")

        if self.new_history_max_weeks < 1:
            raise ValueError("new_history_max_weeks must be at least 1")

        if self.limited_history_max_weeks <= self.new_history_max_weeks:
            raise ValueError(
                "limited_history_max_weeks must be greater than "
                "new_history_max_weeks, or no history could ever be limited"
            )

        if self.min_observed_weeks < 1:
            raise ValueError("min_observed_weeks must be at least 1")

        if (
            self.classification_window_months is not None
            and self.classification_window_months < 1
        ):
            raise ValueError(
                "classification_window_months must be at least 1"
            )

    def fingerprint(self) -> str:
        """Stable short hash of this calibration.

        The recipe is deliberately simple so that both routes can reproduce it:
        the fields as JSON with sorted keys and no whitespace, SHA-256, and the
        first 16 hex characters. Changing a value changes the fingerprint;
        reordering the fields in this file does not.
        """
        payload = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
        )

        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return digest[:FINGERPRINT_LENGTH]


DEFAULT_PARAMETERS = DomainParameters()
"""EquipMaster's calibration, used until a tenant supplies its own."""
