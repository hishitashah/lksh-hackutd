"""Exact tank simulator for intervention plans (ARCHITECTURE.md §5.3, §5.5; SPEC "Plans").

What it answers
---------------
Given a failed pump's tank at a moment (level, which wells feed it, when the repair ends) and a plan (trucks and/or
wells shut in early), what happens to the tank and what does it cost? Every number is deterministic and comes from
the dataset: tank size and limits from ``network.json``; truck capacity, price and travel times, oil and gas prices,
and repair hours from ``ops.json``; well water rates from ``network.json``.

How it works (exact, not a step-by-step approximation)
------------------------------------------------------
Between events the tank level rises in a straight line (pump flow is 0 while it's down; SPEC "Normal behavior"). The
events are: wells being shut in, truck arrivals and the repair ending. We walk through them in time order and solve
exactly when the level would cross the shutdown limit. That matches the answer key's trip times to the minute
(EVENT_9: 04:48:48 → "04:49").

SPEC rules implemented here
---------------------------
* All trucks in a plan are called at the plan start and arrive together ``response_min`` later; each truck comes
  back every ``cycle_min``. A trip removes ``truck_capacity_bbl`` per truck, but never more than is in the tank.
* Every trip must arrive before the repair ends (the planner only builds plans that satisfy this).
* Water is removed only when a trip arrives, so a trip after the tank has tripped doesn't help (it still costs).
* At the shutdown limit every well shuts in automatically and stays off until the repair ends.
* Shut-in cost = the well's lost oil and gas revenue × hours off. Automatic shut-in costs every well's revenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import CostBreakdown

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class WellRate:
    """One well feeding the tank: water it adds and money it earns, per hour."""

    id: str
    water_bph: float  # barrels of water per hour (baseline_water_bpd ÷ 24)
    revenue_per_h: float  # $ per hour of oil + gas while producing
    producing: bool  # producing at the plan start (a well already off adds no water and loses nothing more)


@dataclass(frozen=True)
class TankScenario:
    """A failed pump's tank at the plan start: everything the simulator needs, all from the dataset."""

    facility_id: str
    capacity_bbl: float
    level_pct: float
    start_unix: int
    repair_end_unix: int
    warning_pct: float
    shutdown_pct: float
    wells: tuple[WellRate, ...]
    already_shut_in: bool = False  # the tank had already tripped before the plan start

    @property
    def shutdown_bbl(self) -> float:
        """Volume at the shutdown limit, bbl."""
        return self.shutdown_pct / 100.0 * self.capacity_bbl


@dataclass(frozen=True)
class TruckPlan:
    """Trucks sent to one facility: all called at the plan start, same number of trips each (SPEC Plans)."""

    trucks: int
    trips_per_truck: int
    response_min: float
    cycle_min: float
    capacity_bbl: float
    cost_per_trip: float

    def arrivals(self, start_unix: int) -> list[int]:
        """Arrival times: response_min after the start, then every cycle_min."""
        return [int(start_unix + (self.response_min + k * self.cycle_min) * 60) for k in range(self.trips_per_truck)]

    def busy_until(self, start_unix: int) -> int:
        """SPEC: a truck is busy until cycle_min after its last trip."""
        return int(start_unix + (self.response_min + self.trips_per_truck * self.cycle_min) * 60)

    @property
    def cost(self) -> float:
        """trucks × trips × cost per trip, $."""
        return self.trucks * self.trips_per_truck * self.cost_per_trip


@dataclass
class SimOutcome:
    """What a plan leads to: whether and when the tank trips, its peak, the cost split, and a level trace."""

    tank_trips: bool
    trips_at_unix: int | None
    peak_level_pct: float
    cost: CostBreakdown
    trace: list[list[float]] = field(default_factory=list)

    @property
    def cost_total(self) -> float:
        """Total cost, $."""
        return self.cost.trucks + self.cost.shut_in + self.cost.trip


def simulate(
    sc: TankScenario,
    trucks: TruckPlan | None = None,
    shut_ins: dict[str, int] | None = None,
    trace_step_s: int | None = 300,
) -> SimOutcome:
    """Run one plan on one tank, exactly (see the module docstring for the rules).

    Args:
        sc: the tank at the plan start.
        trucks: trucks sent to this tank, or None.
        shut_ins: well ID → unix time it's shut in (stays off until the repair ends).
        trace_step_s: spacing of the level trace for charts (event points are added too). None = no trace
            (the plan search runs thousands of simulations and only needs traces for the plans it shows).
    """
    shut_ins = shut_ins or {}
    t0, t_end = sc.start_unix, sc.repair_end_unix
    cap, vs = sc.capacity_bbl, sc.shutdown_bbl
    volume = sc.level_pct / 100.0 * cap
    peak = volume

    if sc.already_shut_in or t_end <= t0:
        # Nothing can change a tank that already tripped: its wells stay off until the repair (SPEC).
        lost = sum(w.revenue_per_h for w in sc.wells) * max(0.0, (t_end - t0) / SECONDS_PER_HOUR)
        cost = CostBreakdown(trucks=trucks.cost if trucks else 0.0, shut_in=0.0, trip=round(lost, 2))
        pct = min(volume / cap * 100, sc.shutdown_pct)
        return SimOutcome(
            sc.already_shut_in, t0 if sc.already_shut_in else None, round(pct, 2), cost, [[t0, pct], [t_end, pct]]
        )

    arrivals = trucks.arrivals(t0) if trucks else []
    # Event times inside the plan window, in order. The repair end closes the window.
    events = sorted({t for t in [*shut_ins.values(), *arrivals] if t0 <= t < t_end} | {t_end})
    off: set[str] = {w.id for w in sc.wells if not w.producing}
    trip_at: float | None = None
    points: list[tuple[float, float]] = [(t0, volume)]

    def inflow_bph() -> float:
        return sum(w.water_bph for w in sc.wells if w.id not in off)

    t = float(t0)
    for ev in events:
        if trip_at is None:
            rate = inflow_bph()
            dt_h = (ev - t) / SECONDS_PER_HOUR
            if rate > 0 and volume + rate * dt_h >= vs:
                # Solve the exact crossing time of the shutdown limit.
                trip_at = t + (vs - volume) / rate * SECONDS_PER_HOUR
                volume = vs
                points.append((trip_at, volume))
            else:
                volume += rate * dt_h
            peak = max(peak, volume)
        t = float(ev)
        points.append((t, volume))
        if trip_at is not None:
            continue  # tripped: wells are off until the repair; arrivals don't change anything
        for well_id, start in shut_ins.items():
            if start == ev:
                off.add(well_id)
        if trucks and ev in arrivals:
            volume = max(0.0, volume - trucks.trucks * trucks.capacity_bbl)  # never more than is in the tank
            points.append((t, volume))

    # Cost per well: off from its shut-in start or the trip, whichever came first, until the repair ends.
    shut_cost = trip_cost = 0.0
    for w in sc.wells:
        if not w.producing:
            continue
        starts = [s for s in (shut_ins.get(w.id), trip_at) if s is not None]
        if not starts:
            continue
        off_from = min(starts)
        hours = max(0.0, (t_end - off_from) / SECONDS_PER_HOUR)
        if w.id in shut_ins and shut_ins[w.id] <= (trip_at if trip_at is not None else float("inf")):
            shut_cost += w.revenue_per_h * hours
        else:
            trip_cost += w.revenue_per_h * hours

    cost = CostBreakdown(trucks=trucks.cost if trucks else 0.0, shut_in=round(shut_cost, 2), trip=round(trip_cost, 2))
    return SimOutcome(
        tank_trips=trip_at is not None,
        trips_at_unix=int(round(trip_at)) if trip_at is not None else None,
        peak_level_pct=round(peak / cap * 100, 2),
        cost=cost,
        trace=_trace(points, t0, t_end, cap, trace_step_s) if trace_step_s else [],
    )


def _trace(points: list[tuple[float, float]], t0: int, t_end: int, cap: float, step_s: int) -> list[list[float]]:
    """Level trace for charts: the event points plus regular samples, as [[unix, level %], ...] in time order."""
    pts = sorted(points, key=lambda p: p[0])

    def volume_at(t: float) -> float:
        # Last known point at or before t, then a straight line to the next point (exact between events).
        before = [p for p in pts if p[0] <= t]
        after = [p for p in pts if p[0] > t]
        if not before:
            return pts[0][1]
        (ta, va) = before[-1]
        if not after:
            return va
        (tb, vb) = after[0]
        if tb == ta:
            return vb
        return va + (vb - va) * (t - ta) / (tb - ta)

    samples = {float(t) for t in range(t0, t_end + 1, step_s)} | {p[0] for p in pts}
    out: list[list[float]] = []
    for t in sorted(samples):
        if t > t_end:
            continue
        # At an arrival there are two points (before/after); keep both so the chart shows the drop.
        same = [p for p in pts if p[0] == t]
        for _, v in same or [(t, volume_at(t))]:
            out.append([int(t), round(v / cap * 100, 2)])
    return out
