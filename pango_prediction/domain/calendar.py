"""Calendar effects on a week.

A single known-future covariate summarises holidays and closures: **equivalent
business days per week**, the notebook's ``dias_habiles`` (11.4, S34). It
correlates with weekly sales at Spearman 0.41, and a week containing a public
holiday sells 23.8 % less without recovering the loss afterwards.

Two things are deliberately kept apart here, and the split is what lets the
calendar survive a second tenant:

- **the weight rule** is domain code, shared by both routes: each weekday
  carries a weight, a non-operational date carries none, and a week is the sum;
- **which dates are non-operational** is tenant data and arrives with the
  request, per warehouse.

So this module imports no holiday package and knows no country. That is not
squeamishness about a dependency. A country code answers only one of the four
reasons the notebook gave a day zero weight: it gets the public holidays, and
misses the company's year-start closure, Holy Saturday (not an official holiday
in Colombia, yet the company does not invoice), and any difference between
warehouses. Those are also the ones with the larger effect: a public holiday
takes a week from 5.6 to 4.6, while the year-start closure takes it to 0. A
country code is useful for *drafting* a tenant's closed-date list at onboarding,
which is a different job from reading it at forecast time.

It also removes an asymmetry in the notebook's own covariate. There, the
year-start closure was learned from history (business days with no sales between
26 December and 10 January), which cannot be evaluated for a future year, so
future year-ends scored a full week while historical ones scored nearly nothing
- a skew in a variable DeepAR consumes as known. Here the value depends only on
the week and the supplied dates, so history and horizon are computed by one
rule.
"""

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import date, timedelta

from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    DomainParameters,
)
from pango_prediction.domain.series import DAYS_PER_WEEK

MONDAY = 0


@dataclass(frozen=True, slots=True)
class ClosedDates:
    """The non-operational dates of a request, at the two levels they occur at.

    Closures are not all of one kind. A public holiday or a company-wide
    shutdown closes every warehouse; a physical inventory count or a local
    holiday closes one. The forecast grain is product x warehouse, so the
    question the `CalendarSpec` left open - per request or per warehouse - is
    answered here as both.

    Resolution is a **union**: a warehouse is closed on its own dates and on
    every tenant-wide date. There is deliberately no way for a warehouse to
    re-open a day the tenant declared closed, because that would let one
    warehouse contradict the company calendar rather than extend it.
    """

    everywhere: frozenset[date] = frozenset()
    by_warehouse: Mapping[str, frozenset[date]] = field(default_factory=dict)

    def for_warehouse(self, warehouse_id: str) -> frozenset[date]:
        """Every date on which ``warehouse_id`` does not operate."""
        return self.everywhere | self.by_warehouse.get(warehouse_id, frozenset())

    @classmethod
    def of(
        cls,
        everywhere: Iterable[date] = (),
        by_warehouse: Mapping[str, Iterable[date]] | None = None,
    ) -> "ClosedDates":
        """Build from the loose collections an adapter has, normalising them.

        The adapter receives JSON lists; the domain wants sets, so that a date
        repeated in the request costs nothing and membership is a hash lookup
        rather than a scan of every closure for every day of every week.
        """
        return cls(
            everywhere=frozenset(everywhere),
            by_warehouse={
                warehouse: frozenset(dates)
                for warehouse, dates in (by_warehouse or {}).items()
            },
        )


def full_week_business_days(parameters: DomainParameters = DEFAULT_PARAMETERS) -> float:
    """What an unclosed week is worth, 5.6 with the default weights.

    Derived from the weight table rather than stored beside it, so the two
    cannot drift apart.
    """
    return sum(parameters.weekday_weights)


def day_weight(
    day: date,
    closed_dates: Set[date],
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> float:
    """What one day is worth: nothing if it is closed, else its weekday weight.

    Closing a day the tenant never trades anyway changes nothing, because the
    weekday weight is already zero. The two mechanisms layer rather than fight.
    """
    if day in closed_dates:
        return 0.0

    return parameters.weekday_weights[day.weekday()]


def week_business_days(
    week_start: date,
    closed_dates: Set[date],
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> float:
    """Equivalent business days of the week beginning on ``week_start``.

    Works for a future week as readily as a past one: it reads only the dates
    and the tenant's calibration, never the demand history. That is what lets
    the horizon carry the same covariate as the history.

    ``week_start`` must be a Monday. A Tuesday would quietly measure a
    Tuesday-to-Monday window and put the wrong number against the week.
    """
    if week_start.weekday() != MONDAY:
        raise ValueError(
            f"a week starts on a Monday, and {week_start.isoformat()} is not one"
        )

    return sum(
        day_weight(week_start + timedelta(days=offset), closed_dates, parameters)
        for offset in range(DAYS_PER_WEEK)
    )


def weekly_business_days(
    weeks: Sequence[date],
    closed_dates: Set[date],
    parameters: DomainParameters = DEFAULT_PARAMETERS,
) -> tuple[float, ...]:
    """The covariate for a run of weeks, aligned to ``weeks``.

    Used for both halves of a forecast: the weeks of the series and the weeks
    of the horizon.
    """
    return tuple(
        week_business_days(week, closed_dates, parameters) for week in weeks
    )
