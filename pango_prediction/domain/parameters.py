"""Calibrated domain parameters.

PANGO is multi-tenant, and not every number in the forecasting domain is a
universal rule. Some are calibrations of one company's behaviour:

- the stockout threshold is about four replenishment cycles, so it follows the
  tenant's replenishment interval;
- the made-to-order thresholds were fitted to one catalogue's distribution.

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

    def __post_init__(self) -> None:
        if self.max_stockout_episode_weeks < 1:
            raise ValueError("max_stockout_episode_weeks must be at least 1")

        if not 0 < self.made_to_order_threshold <= 1:
            raise ValueError(
                "made_to_order_threshold must be greater than 0 and at most 1"
            )

        if self.min_weeks_with_stock_data < 1:
            raise ValueError("min_weeks_with_stock_data must be at least 1")

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
