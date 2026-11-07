"""Generate the mock forecast requests that stand in for Pango Core.

Every field of the generated payload is traceable to a column of the tenant
schema; `tests/fixtures/README.md` holds the mapping. Regenerate the JSON after
the contract changes with (PowerShell):

    $env:PYTHONPATH="."; python tests/fixtures/build_fixtures.py

The series deliberately mirror the segments the demand analysis found, so that
loading these fixtures exercises the real availability paths instead of one
happy case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pango_prediction.domain.series import last_complete_week_start

CONTRACT_VERSION = "1.0"
"""Pinned to what Pango Core sends, which is not what the service serves.

The service is on 1.1 and accepts either, so these fixtures double as the proof
that a 1.0 client keeps working. Raise this only when Core actually moves.
"""
HISTORY_END = date(2026, 11, 1)  # Sunday: the history ends on a complete week
HORIZON = 4
FREQUENCY = "WEEKLY"  # settings.forecast_granularity / forecast_runs.granularity
WEEKS = 22

TUESDAY = 1
SATURDAY = 5

OUTPUT = Path(__file__).parent


@dataclass(frozen=True)
class SeriesSpec:
    """A series described by week, before being expanded into daily rows."""

    series_id: int
    product_id: int
    warehouse_id: int
    sku: str
    category: str
    stock_unit: str
    warehouse_code: str
    warehouse_type: str
    lead_time_days: int
    segment: str
    note: str
    weekly_sales: dict[int, int]
    weekly_closing_stock: dict[int, float]
    negative_row: tuple[int, float] | None = field(default=None)


def base_monday() -> date:
    """Monday that starts the oldest week of the generated history."""
    return last_complete_week_start(HISTORY_END) - timedelta(weeks=WEEKS - 1)


def day_of(week_index: int, weekday: int) -> date:
    return base_monday() + timedelta(weeks=week_index, days=weekday)


def demand_rows(spec: SeriesSpec) -> list[dict[str, Any]]:
    """Expand weekly units into the daily rows Core aggregates from movements.

    Sales are split between a weekday and a Saturday: Saturday carries about
    12 % of the units in the source data, which is why the week has to run
    Monday to Sunday.
    """
    rows: list[dict[str, Any]] = []

    for week_index in sorted(spec.weekly_sales):
        units = spec.weekly_sales[week_index]

        if units <= 0:
            continue

        on_saturday = units // 2
        on_tuesday = units - on_saturday

        rows.append(
            {
                "date": day_of(week_index, TUESDAY).isoformat(),
                "quantity": on_tuesday,
            }
        )

        if on_saturday:
            rows.append(
                {
                    "date": day_of(week_index, SATURDAY).isoformat(),
                    "quantity": on_saturday,
                }
            )

    if spec.negative_row is not None:
        week_index, quantity = spec.negative_row
        rows.append(
            {
                "date": day_of(week_index, TUESDAY).isoformat(),
                "quantity": quantity,
            }
        )

    return sorted(rows, key=lambda row: str(row["date"]))


def stock_rows(spec: SeriesSpec) -> list[dict[str, Any]]:
    """Emit a balance only on the weeks where it changed.

    Core reconstructs stock by replaying movements, so it has a balance for the
    days something moved, not for every calendar day. Availability carries the
    last known balance forward, which is why the gaps are safe.
    """
    return [
        {
            "date": day_of(week_index, SATURDAY).isoformat(),
            "closing_stock": spec.weekly_closing_stock[week_index],
        }
        for week_index in sorted(spec.weekly_closing_stock)
    ]


def series_payload(spec: SeriesSpec) -> dict[str, Any]:
    return {
        "series_id": spec.series_id,
        "product_id": spec.product_id,
        "warehouse_id": spec.warehouse_id,
        "metadata": {
            "sku": spec.sku,
            "category": spec.category,
            "stock_unit": spec.stock_unit,
            "warehouse_code": spec.warehouse_code,
            "warehouse_type": spec.warehouse_type,
            "lead_time_days": spec.lead_time_days,
        },
        "demand": demand_rows(spec),
        "stock": stock_rows(spec),
    }


def request_payload(
    request_id: str,
    forecast_run_id: int,
    specs: list[SeriesSpec],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "forecast_run_id": forecast_run_id,
        "frequency": FREQUENCY,
        "horizon": HORIZON,
        "history_end": HISTORY_END.isoformat(),
        # Resolved: closures are both, and they resolve as a union. Left empty
        # here because an empty calendar is what Core sends before a tenant has
        # configured one, which is the cold start the service has to handle.
        "calendar": {"closed_dates": [], "closed_dates_by_warehouse": {}},
        "series": [series_payload(spec) for spec in specs],
    }


# --- the segments ---

STOCK_MANAGED = SeriesSpec(
    series_id=1042,
    product_id=318,
    warehouse_id=1,
    sku="TAL-4512",
    category="Herramienta manual",
    stock_unit="UN",
    warehouse_code="PRINCIPAL",
    warehouse_type="WAREHOUSE",
    lead_time_days=5,
    segment="stock_managed_short_stockout",
    note="Held in stock, sells most weeks, two weeks of stockout in the middle.",
    weekly_sales={
        0: 4, 1: 5, 2: 3, 3: 4, 6: 4, 7: 3, 8: 4, 9: 4,
        11: 3, 12: 3, 13: 2, 16: 4, 17: 4, 18: 3, 19: 4, 20: 4, 21: 3,
    },
    weekly_closing_stock={
        0: 40.0, 1: 36.0, 2: 31.0, 3: 28.0, 4: 24.0, 6: 20.0, 7: 17.0,
        8: 13.0, 9: 9.0, 11: 6.0, 12: 3.0, 13: 1.0, 14: 0.0, 15: 0.0,
        16: 30.0, 17: 26.0, 18: 22.0, 19: 19.0, 20: 15.0, 21: 12.0,
    },
)

MADE_TO_ORDER = SeriesSpec(
    series_id=1198,
    product_id=742,
    warehouse_id=3,
    sku="BOM-9930",
    category="Equipo a pedido",
    stock_unit="UN",
    warehouse_code="DROPSHIPPING",
    warehouse_type="DROPSHIPPING",
    lead_time_days=12,
    segment="made_to_order",
    note="Sold without being held in stock: every selling week opens at zero.",
    weekly_sales={0: 2, 2: 3, 4: 2, 6: 4, 9: 2, 12: 3, 15: 2, 18: 4, 21: 3},
    weekly_closing_stock={0: 0.0, 5: 0.0, 10: 0.0, 15: 0.0, 20: 0.0},
)

OUT_OF_ASSORTMENT = SeriesSpec(
    series_id=1255,
    product_id=511,
    warehouse_id=1,
    sku="FIL-2201",
    category="Repuesto",
    stock_unit="UN",
    warehouse_code="PRINCIPAL",
    warehouse_type="WAREHOUSE",
    lead_time_days=7,
    segment="out_of_assortment",
    note="Fifteen consecutive weeks without stock: longer than the 13-week threshold.",
    weekly_sales={0: 5, 1: 4, 2: 6, 3: 3, 19: 2, 21: 3},
    weekly_closing_stock={
        0: 25.0, 1: 20.0, 2: 14.0, 3: 11.0, 4: 0.0,
        19: 14.0, 20: 11.0, 21: 8.0,
    },
)

NOT_CLASSIFIABLE = SeriesSpec(
    series_id=1403,
    product_id=903,
    warehouse_id=2,
    sku="NEW-0077",
    category="Herramienta manual",
    stock_unit="UN",
    warehouse_code="BAQ-PRINCIPAL",
    warehouse_type="WAREHOUSE",
    lead_time_days=7,
    segment="not_classifiable",
    note="Recently stocked product-warehouse: too few selling weeks to classify.",
    weekly_sales={19: 3, 20: 2, 21: 4},
    weekly_closing_stock={19: 12.0, 20: 9.0, 21: 5.0},
)

INVALID = SeriesSpec(
    series_id=1500,
    product_id=640,
    warehouse_id=1,
    sku="BAD-0001",
    category="Repuesto",
    stock_unit="UN",
    warehouse_code="PRINCIPAL",
    warehouse_type="WAREHOUSE",
    lead_time_days=7,
    segment="invalid_negative_quantity",
    note="A reversal Core failed to net out: demand is gross and cannot be negative.",
    weekly_sales={0: 3, 1: 2, 2: 4, 3: 2},
    weekly_closing_stock={0: 18.0, 1: 15.0, 2: 11.0, 3: 9.0},
    negative_row=(2, -4.0),
)

ALL_SPECS = [
    STOCK_MANAGED,
    MADE_TO_ORDER,
    OUT_OF_ASSORTMENT,
    NOT_CLASSIFIABLE,
    INVALID,
]


def segments_index() -> dict[str, Any]:
    """What each series is for, so tests assert intent instead of magic ids."""
    return {
        "history_end": HISTORY_END.isoformat(),
        "first_week": base_monday().isoformat(),
        "weeks": WEEKS,
        "series": [
            {
                "series_id": spec.series_id,
                "segment": spec.segment,
                "note": spec.note,
            }
            for spec in ALL_SPECS
        ],
    }


def write(name: str, payload: dict[str, Any]) -> Path:
    path = OUTPUT / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return path


def main() -> None:
    written = [
        write(
            "forecast_request.json",
            request_payload("req-2026-11-02-0001", 41, ALL_SPECS),
        ),
        write(
            "forecast_request_minimal.json",
            request_payload("req-2026-11-02-0002", 42, [STOCK_MANAGED]),
        ),
        write("segments.json", segments_index()),
    ]

    for path in written:
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
