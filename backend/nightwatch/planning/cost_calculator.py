"""Shared deterministic formulas for money and tank physics (ARCHITECTURE.md §5.5, §14 ``planning/cost_calculator.py``).

Where this sits in the app
--------------------------
Phase 2 (monitoring) uses ``facility_revenue_per_hour`` for each pump failure's lost-revenue figure (the alert-order
tie-break) and ``fill_rate_pct_per_hour`` for the shutdown countdown. Phase 3 (the intervention planner and the
cost agent) builds on the same functions, so a cost is computed one way everywhere.

All inputs come from the loaded dataset's ``network.json`` / ``ops.json``; nothing here has a hardcoded price, rate
or limit.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Facility, Prices, Well

HOURS_PER_DAY = 24.0


def well_revenue_per_hour(well: Well, prices: Prices) -> float:
    """Revenue a well earns per hour while producing, $/h.

    SPEC Plans: lost revenue per day = oil_bpd × oil price + gas_mcfd × gas price. A shut-in well earns nothing,
    so this is also what shutting it in costs per hour.
    """
    return (well.oil_bpd * prices.oil_per_bbl + well.gas_mcfd * prices.gas_per_mcf) / HOURS_PER_DAY


def facility_revenue_per_hour(facility: Facility, prices: Prices, wells: Iterable[Well] | None = None) -> float:
    """Revenue of a facility's wells per hour, $/h (all wells, or only ``wells`` if given).

    Used as an automatic shut-in's cost rate: when the tank trips, every well stops.
    """
    return sum(well_revenue_per_hour(w, prices) for w in (wells if wells is not None else facility.wells))


def shut_in_cost_per_bbl(well: Well, prices: Prices) -> float:
    """$ lost per barrel of water kept out of the tank by shutting this well in.

    SPEC Plans: cost per bbl avoided = lost revenue per day ÷ baseline_water_bpd. It's the same however long the
    well is off, which is why the cheapest well to shut in is the one with the lowest value here.
    """
    return well_revenue_per_hour(well, prices) * HOURS_PER_DAY / well.baseline_water_bpd


def truck_cost_per_bbl(cost_per_trip: float, truck_capacity_bbl: float) -> float:
    """$ per barrel hauled by an emergency truck trip (flat price ÷ capacity)."""
    return cost_per_trip / truck_capacity_bbl


def fill_rate_pct_per_hour(water_bpd: float, capacity_bbl: float) -> float:
    """How fast a tank fills with no pump running, in percentage points per hour.

    SPEC "Normal behavior": level change per hour = (inflow − pump flow) ÷ 24 ÷ capacity × 100. With the pump
    stopped (a failure), pump flow is 0.
    """
    return water_bpd / HOURS_PER_DAY / capacity_bbl * 100.0
