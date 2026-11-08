"""Deterministic intervention planning: every option for a pump failure, its exact cost, and joint plans when
several failures share one truck fleet (ARCHITECTURE.md §5.3-5.5, §16; SPEC "Plans").

Where this sits in the app
--------------------------
This is the "planning and cost-analysis logic" the spec says all costs must come from (§5.5, §9.4 step 3). The
cost-effectiveness agent interprets its results; the orchestrator ranks them; the ``/plan`` route returns them; the
alert list shows each failure's chosen plan. Nothing here uses an LLM.

What it tries for each pump failure (§5.3)
------------------------------------------
* **A. Send trucks:** 1..(free trucks) trucks × 1..(as many trips as arrive before the repair ends).
* **B. Shut in wells early:** every non-empty set of the facility's producing wells, shut in together at the
  **latest start that still keeps the tank under its shutdown limit** (binary search to the minute: shutting in
  later costs less). Also a 15-minute grid of starts, which covers plans that only delay the trip.
* **A + B combined:** each truck option with each well set.
* **C. Do nothing:** let the tank trip if it will; costed like everything else (never assumed cheap or expensive).

Each option is simulated exactly (``simulator.py``) and the cheapest is chosen. Simplification: wells in one shut-in
set share one start time.

Shared trucks (§16 step 3)
--------------------------
At "now" nothing has been dispatched, so a system's ongoing failures share its whole fleet, and a truck sent to one
tank can't help another. ``plan_system`` computes each failure's best plan for every possible number of trucks, then
tries every split of the fleet across the failures and keeps the cheapest total. It never assigns more trucks than
the system has.

``plan_sequential_at_onset`` reproduces how the answer key's ``reference_action``s were made: each failure planned at
its onset, with trucks still busy on earlier plans unavailable. Tests use it to check against the key.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

import numpy as np

from ..models import Event, Plan, PlanAction, PlanResponse, SimTrace
from ..monitoring.anomaly_detector import detect
from ..monitoring.countdown import WELL_PRODUCING
from ..monitoring.telemetry_processor import Dataset
from .cost_calculator import well_revenue_per_hour
from .simulator import SimOutcome, TankScenario, TruckPlan, WellRate, simulate

GRID_STEP_S = 15 * 60  # spacing of the shut-in start grid for plans that only delay a trip
MINUTE = 60

# A filter on options: (event ID, candidate) -> keep it? Used by the orchestrator's hard constraints.
Allowed = Callable[[str, "Candidate"], bool]


@dataclass
class Candidate:
    """One evaluated option: what it does (trucks, shut-ins), what happens, and how it's shown."""

    trucks: TruckPlan | None
    shut_ins: dict[str, int]
    outcome: SimOutcome
    label: str
    actions: list[PlanAction]

    @property
    def trucks_used(self) -> int:
        """Trucks this option takes from the fleet."""
        return self.trucks.trucks if self.trucks else 0

    @property
    def cost_total(self) -> float:
        """Total cost, $."""
        return round(self.outcome.cost_total, 2)

    def sort_key(self) -> tuple:
        """Cheapest first; ties go to fewer trucks, then to plans that keep the tank under the limit."""
        return (self.cost_total, self.trucks_used, self.outcome.tank_trips, self.label)

    def to_plan(self, kind: str, event_ids: list[str], label: str | None = None) -> Plan:
        """The contract ``Plan`` for this option (fields the frontend already expects, §5.4)."""
        return Plan(
            kind=kind,
            event_ids=event_ids,
            actions=self.actions,
            label=label or self.label,
            cost_total=self.cost_total,
            cost_breakdown=self.outcome.cost,
            tank_trips=self.outcome.tank_trips,
            trips_at_unix=self.outcome.trips_at_unix,
            peak_level_pct=self.outcome.peak_level_pct,
        )


@dataclass
class EventOptions:
    """Every evaluated option for one pump failure, cheapest first."""

    event: Event
    scenario: TankScenario
    candidates: list[Candidate]
    do_nothing: Candidate

    def best_with(self, max_trucks: int, allowed: Allowed | None = None) -> Candidate | None:
        """The cheapest option that uses at most ``max_trucks`` trucks and passes ``allowed`` (None if none does).

        ``allowed(event_id, candidate)`` lets the orchestrator exclude options that break a hard constraint
        (e.g. an environmental veto) while reusing this search.
        """
        return next(
            (c for c in self.candidates if c.trucks_used <= max_trucks and (allowed is None or allowed(self.event.id, c))),
            None,
        )


@dataclass
class SystemPlan:
    """The joint plan for one system's ongoing pump failures, sharing its truck fleet (§16)."""

    system_id: str
    at_unix: int
    fleet_size: int
    free_trucks: int
    options: dict[str, EventOptions]
    selection: dict[str, Candidate] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        """Cost of the selected plans together, $."""
        return round(sum(c.cost_total for c in self.selection.values()), 2)

    @property
    def do_nothing_cost(self) -> float:
        """Cost if nothing is done for any of these failures, $."""
        return round(sum(o.do_nothing.cost_total for o in self.options.values()), 2)

    @property
    def trucks_used(self) -> int:
        """Trucks the selected plans use in total (never more than ``free_trucks``)."""
        return sum(c.trucks_used for c in self.selection.values())


# ---------------------------------------------------------------------------
# Building the tank scenario and the options
# ---------------------------------------------------------------------------


def hhmm(unix: int) -> str:
    """Unix seconds → 'HH:MM' UTC for plan labels."""
    return datetime.fromtimestamp(unix, tz=UTC).strftime("%H:%M")


def repair_end(ds: Dataset, event: Event) -> int:
    """SPEC: repairs last exactly ``expected_repair_hours`` (ops.json) from onset."""
    return int(event.onset_unix + ds.ops.expected_repair_hours["pump_failure"] * 3600)


def scenario_for(ds: Dataset, event: Event, start_unix: int, level_pct: float | None = None) -> TankScenario:
    """The tank of a failed pump at ``start_unix``, built only from dataset values.

    Args:
        level_pct: the tank level at the start. Defaults to the reading at ``start_unix``; for "now" the caller
            passes the detector's fitted level (``Event.level_pct``), which filters sensor noise.
    """
    facility = ds.facilities[event.facility_id]
    prices = ds.ops.prices
    wells = tuple(
        WellRate(
            id=w.id,
            water_bph=w.baseline_water_bpd / 24.0,
            revenue_per_h=well_revenue_per_hour(w, prices),
            producing=ds.value_at(w.id, "status", start_unix) == WELL_PRODUCING,
        )
        for w in facility.wells
    )
    limits = ds.network.defaults.tank_limits_pct
    level = level_pct if level_pct is not None else ds.value_at(facility.tank.id, "level_pct", start_unix)
    return TankScenario(
        facility_id=facility.id,
        capacity_bbl=facility.tank.capacity_bbl,
        level_pct=float(level),
        start_unix=start_unix,
        repair_end_unix=repair_end(ds, event),
        warning_pct=limits.warning,
        shutdown_pct=limits.shutdown,
        wells=wells,
        already_shut_in=not any(w.producing for w in wells),
    )


def truck_options(ds: Dataset, event: Event, sc: TankScenario, free_trucks: int) -> list[TruckPlan]:
    """Every (trucks, trips) combination whose trips all arrive before the repair ends (SPEC Plans)."""
    logistics = ds.ops.systems[event.system_id].facilities[event.facility_id]
    trucking = ds.ops.emergency_trucking
    plans: list[TruckPlan] = []
    for n in range(1, free_trucks + 1):
        m = 1
        while True:
            tp = TruckPlan(
                n, m, logistics.response_min, logistics.cycle_min, trucking.truck_capacity_bbl, trucking.cost_per_trip
            )
            if tp.arrivals(sc.start_unix)[-1] >= sc.repair_end_unix:
                break
            plans.append(tp)
            m += 1
    return plans


def _label(sc: TankScenario, trucks: TruckPlan | None, shut_ins: dict[str, int], outcome: SimOutcome) -> str:
    """One-line description, e.g. '1 truck, 1 trip + shut in S4_WELL_A2 at 12:51 · $975'."""
    parts: list[str] = []
    if trucks:
        if trucks.trucks == 1:
            parts.append(f"1 truck, {trucks.trips_per_truck} trip{'s' if trucks.trips_per_truck > 1 else ''}")
        else:
            trips = trucks.trips_per_truck
            parts.append(f"{trucks.trucks} trucks, {trips} trip{'s' if trips > 1 else ''} each")
    if shut_ins:
        start = next(iter(shut_ins.values()))
        parts.append(f"shut in {' + '.join(sorted(shut_ins))} at {hhmm(start)}")
    if not parts:
        tail = f"tank trips at {hhmm(outcome.trips_at_unix)}" if outcome.trips_at_unix else "tank stays under the limit"
        return f"Do nothing · {tail} · ${outcome.cost_total:,.0f}"
    text = " + ".join(parts)
    text = text[0].upper() + text[1:]
    if outcome.tank_trips:
        text += f" (tank still trips at {hhmm(outcome.trips_at_unix)})"
    return f"{text} · ${outcome.cost_total:,.0f}"


def _candidate(sc: TankScenario, trucks: TruckPlan | None, shut_ins: dict[str, int]) -> Candidate:
    """Simulate one option (no chart trace) and wrap it with its label and contract actions."""
    outcome = simulate(sc, trucks, shut_ins, trace_step_s=None)
    actions: list[PlanAction] = []
    if trucks:
        actions.append(
            PlanAction(
                type="dispatch_truck",
                facility_id=sc.facility_id,
                trucks=trucks.trucks,
                trips_per_truck=trucks.trips_per_truck,
            )
        )
    if shut_ins:
        start = next(iter(shut_ins.values()))
        actions.append(
            PlanAction(type="shut_in_wells", wells=sorted(shut_ins), start_after_min=round((start - sc.start_unix) / 60))
        )
    if not actions:
        actions.append(PlanAction(type="none"))
    return Candidate(trucks, dict(shut_ins), outcome, _label(sc, trucks, shut_ins, outcome), actions)


def _latest_safe_start(sc: TankScenario, trucks: TruckPlan | None, wells: tuple[str, ...]) -> int | None:
    """Latest minute to shut ``wells`` in so the tank never trips (binary search), or None if no start works.

    Shutting in later always lets in more water, so "keeps the tank under the limit" can only switch from true to
    false as the start moves later: that makes a binary search exact.
    """
    last = (sc.repair_end_unix - 1 - sc.start_unix) // MINUTE

    def safe(k: int) -> bool:
        start = sc.start_unix + k * MINUTE
        return not simulate(sc, trucks, dict.fromkeys(wells, start), trace_step_s=None).tank_trips

    if last < 0 or not safe(0):
        return None
    if safe(last):
        return None  # the tank is safe without these wells: shutting them in only adds cost
    lo, hi = 0, last  # safe(lo) is True, safe(hi) is False
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if safe(mid) else (lo, mid)
    return sc.start_unix + lo * MINUTE


def options_for_event(
    ds: Dataset, event: Event, start_unix: int, free_trucks: int, level_pct: float | None = None
) -> EventOptions:
    """Every option for one pump failure from ``start_unix`` with ``free_trucks`` available, cheapest first."""
    sc = scenario_for(ds, event, start_unix, level_pct)
    do_nothing = _candidate(sc, None, {})
    if sc.already_shut_in:
        # The tank already tripped: no plan can help until the repair (SPEC). Only "do nothing" is honest.
        return EventOptions(event, sc, [do_nothing], do_nothing)

    producing = [w.id for w in sc.wells if w.producing]
    bases: list[TruckPlan | None] = [None, *truck_options(ds, event, sc, free_trucks)]
    seen: dict[str, Candidate] = {}

    def add(c: Candidate) -> None:
        if c.label not in seen or c.sort_key() < seen[c.label].sort_key():
            seen[c.label] = c

    add(do_nothing)
    for base in bases:
        base_c = _candidate(sc, base, {}) if base else do_nothing
        add(base_c)
        if not base_c.outcome.tank_trips:
            continue  # already safe: adding shut-ins would only add cost
        for r in range(1, len(producing) + 1):
            for wells in itertools.combinations(producing, r):
                start = _latest_safe_start(sc, base, wells)
                if start is not None:
                    add(_candidate(sc, base, dict.fromkeys(wells, start)))
                # Plans that can't prevent the trip but delay it (sometimes cheaper than doing nothing).
                for t in range(start_unix, sc.repair_end_unix, GRID_STEP_S):
                    c = _candidate(sc, base, dict.fromkeys(wells, t))
                    if c.outcome.tank_trips:
                        add(c)
    candidates = sorted(seen.values(), key=Candidate.sort_key)
    return EventOptions(event, sc, candidates, do_nothing)


# ---------------------------------------------------------------------------
# Joint planning for a system at "now" (shared trucks, §16)
# ---------------------------------------------------------------------------


def plan_system(ds: Dataset, system_id: str, at: int, failures: list[Event]) -> SystemPlan:
    """Cheapest joint plan for a system's ongoing pump failures, sharing its whole fleet.

    Tries every way to split the free trucks across the failures (a truck goes to at most one tank) and keeps the
    lowest total cost. Ties go to using fewer trucks.
    """
    fleet = ds.ops.systems[system_id].emergency_fleet_size
    options = {e.id: options_for_event(ds, e, at, fleet, e.level_pct) for e in failures}
    plan = SystemPlan(system_id, at, fleet, fleet, options)
    plan.selection = select_joint(options, fleet) or {}
    return plan


def select_joint(
    options: dict[str, EventOptions], fleet: int, allowed: Allowed | None = None
) -> dict[str, Candidate] | None:
    """Cheapest combination of one option per failure that shares ``fleet`` trucks (§16).

    Tries every split of the trucks across the failures (a truck goes to at most one tank), takes each failure's
    cheapest allowed option for its share, and keeps the lowest total; ties go to fewer trucks. Returns None if some
    failure has no allowed option at all. Used by the planner (no filter) and the orchestrator (hard-constraint filter).
    """
    ids = list(options)
    best: tuple[float, int, tuple[int, ...]] | None = None
    for split in itertools.product(range(fleet + 1), repeat=len(ids)):
        if sum(split) > fleet:
            continue
        chosen = [options[i].best_with(n, allowed) for i, n in zip(ids, split, strict=True)]
        if any(c is None for c in chosen):
            continue
        key = (round(sum(c.cost_total for c in chosen), 2), sum(c.trucks_used for c in chosen), split)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return {i: options[i].best_with(n, allowed) for i, n in zip(ids, best[2], strict=True)}


def plan_now(ds: Dataset, at: int | None = None) -> dict[str, SystemPlan]:
    """Joint plans for every system that has ongoing pump failures at ``at`` (None = 'now')."""
    return dict(_plan_now_cached(ds, ds.clamp(at)))


@lru_cache(maxsize=128)
def _plan_now_cached(ds: Dataset, at: int) -> tuple[tuple[str, SystemPlan], ...]:
    """Cached per (dataset, time): the alert list, /plan and the agents all ask for the same moment."""
    by_system: dict[str, list[Event]] = {}
    for e in detect(ds, at):
        if e.category == "pump_failure" and e.status == "ongoing":
            by_system.setdefault(e.system_id, []).append(e)
    return tuple((sid, plan_system(ds, sid, at, evs)) for sid, evs in sorted(by_system.items()))


def plan_response(ds: Dataset, event_id: str, at: int | None = None) -> PlanResponse:
    """The plan panel for one ongoing pump failure (``GET /plan``), taken from its system's joint plan.

    Raises:
        KeyError: the event isn't an ongoing pump failure at ``at``.
    """
    at = ds.clamp(at)
    for sp in plan_now(ds, at).values():
        if event_id not in sp.options:
            continue
        opts, chosen = sp.options[event_id], sp.selection[event_id]
        share = sp.free_trucks - (sp.trucks_used - chosen.trucks_used)  # trucks not needed by the other failures
        alternatives: list[Plan] = []
        for c in opts.candidates:
            if c.label == chosen.label or c is opts.do_nothing or len(alternatives) >= 5:
                continue
            label = c.label + (" · needs a truck assigned to another failure" if c.trucks_used > share else "")
            alternatives.append(c.to_plan("alternative", [event_id], label))
        sc = opts.scenario
        traces = []
        for name, cand in (("Do nothing", opts.do_nothing), ("Recommended plan", chosen)):
            out = simulate(sc, cand.trucks, cand.shut_ins)  # with the chart trace
            traces.append(SimTrace(label=name, points=out.trace, warning_pct=sc.warning_pct, shutdown_pct=sc.shutdown_pct))
        return PlanResponse(
            event_id=event_id,
            at_unix=at,
            cheapest=chosen.to_plan("cheapest", [event_id]),
            do_nothing=opts.do_nothing.to_plan("do_nothing", [event_id]),
            alternatives=alternatives,
            traces=traces,
        )
    raise KeyError(event_id)


# ---------------------------------------------------------------------------
# Sequential planning at each onset (how the answer key's reference plans were made)
# ---------------------------------------------------------------------------


def onset_level(ds: Dataset, event: Event, window_s: int = 1800) -> float:
    """Tank level at a failure's onset, with sensor noise removed (retrospective use only).

    A single reading is noisy (e.g. 42.01% when the tank is at 41.90%). After the pump fails the level rises in a
    straight line, so a line fitted to the first 30 minutes after onset and extended back to the onset recovers the
    true level (41.92%). This looks ahead of the onset, so it's only for looking back at past failures (reproducing
    reference plans, replays), never for live planning, which uses the detector's level at "now".
    """
    tank = ds.facilities[event.facility_id].tank.id
    end = min(event.onset_unix + window_s, event.end_unix or ds.now_unix, ds.now_unix)
    mask = (ds.times >= event.onset_unix) & (ds.times <= end)
    t = ds.times[mask] - event.onset_unix
    y = ds.series(tank, "level_pct")[mask]
    if t.size >= 3:
        slope, intercept = np.polyfit(t, y, 1)
        return float(intercept)
    return ds.value_at(tank, "level_pct", event.onset_unix)


def plan_sequential_at_onset(ds: Dataset) -> dict[str, Candidate]:
    """Plan every pump failure at its own onset, in onset order, with trucks busy on earlier plans unavailable.

    SPEC: "each event's reference_action is planned at its onset, assuming earlier reference plans were followed.
    A truck sent to an earlier event stays busy until it's back from its last trip."
    """
    failures = [e for e in detect(ds) if e.category == "pump_failure"]
    busy: dict[str, list[int]] = {}  # system → busy-until time of each truck in use
    chosen: dict[str, Candidate] = {}
    for e in sorted(failures, key=lambda x: (x.onset_unix, x.asset_id)):
        fleet = ds.ops.systems[e.system_id].emergency_fleet_size
        in_use = [t for t in busy.get(e.system_id, []) if t > e.onset_unix]
        free = fleet - len(in_use)
        best = options_for_event(ds, e, e.onset_unix, free, onset_level(ds, e)).best_with(free)
        chosen[e.id] = best
        if best.trucks:
            in_use += [best.trucks.busy_until(e.onset_unix)] * best.trucks.trucks
        busy[e.system_id] = in_use
    return chosen
