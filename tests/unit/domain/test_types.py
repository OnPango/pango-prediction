from datetime import date

from pango_prediction.domain.types import (
    DemandObservation,
    ForecastSeriesInput,
    StockObservation,
)


def test_demand_observation_stores_values() -> None:
    observation = DemandObservation(
        date=date(2026, 11, 5),
        quantity=12.5,
    )

    assert observation.date == date(2026, 11, 5)
    assert observation.quantity == 12.5


def test_forecast_series_input_groups_product_warehouse_history() -> None:
    demand = (
        DemandObservation(
            date=date(2026, 11, 1),
            quantity=5.0,
        ),
    )

    stock = (
        StockObservation(
            date=date(2026, 11, 1),
            closing_stock=20.0,
        ),
    )

    series = ForecastSeriesInput(
        series_id="PW-001",
        product_id="P-001",
        warehouse_id="W-001",
        demand=demand,
        stock=stock,
    )

    assert series.series_id == "PW-001"
    assert series.product_id == "P-001"
    assert series.warehouse_id == "W-001"
    assert series.demand == demand
    assert series.stock == stock