"""Deterministic planner tests (ARCHITECTURE.md Phase 3: "test it against known examples in the repository").

Known examples used (tests may read answer keys; the app never does):
* the README worked example (EVENT_9): do nothing trips at 04:49 for $5,016; shutting in S1_WELL_C2 at 02:25 costs
  $1,958; one truck costs $905 and keeps the tank safe;
* every pump failure's ``reference_action`` in history/full_week (planned at onset, trucks busy on earlier plans);
* the joint plans at the snapshots, where several failures share a system's trucks (§16).
The key's costs are written "~$…" and its shut-in times are minute-rounded, so costs are compared within 2 %.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nightwatch.config import REPO_ROOT
from nightwatch.monitoring.telemetry_processor import load_dataset
from nightwatch.planning.intervention_planner import plan_now, plan_sequential_at_onset
from nightwatch.planning.simulator import TankScenario, TruckPlan, WellRate, simulate

HISTORY = REPO_ROOT / "history"
FULL_WEEK = HISTORY / "full_week"
SNAPSHOTS = sorted(p for p in HISTORY.glob("snapshot_*") if p.is_dir())


def reference_cost(text: str) -> float:
    """'1 truck, 1 trip (~$905)' → 905.0; 'none (...)' → 0.0."""
    m = re.search(r"~\$([\d,]+)", text)
    return float(m.group(1).replace(",", "")) if m else 0.0


def close(ours: float, ref: float) -> bool:
    """Within 2 % (or $15 for small costs) of the key's rounded reference cost."""
    return abs(ours - ref) <= max(15.0, 0.02 * ref)


@pytest.fixture(scope="module")
def event9() -> TankScenario:
    """The README's worked example tank: S1_FACILITY_C, 800 bbl, 50% at 00:20, repair at 06:20."""
    ds = load_dataset(FULL_WEEK)
    f = ds.facilities["S1_FACILITY_C"]
    p = ds.ops.prices
    wells = tuple(
        WellRate(w.id, w.baseline_water_bpd / 24, (w.oil_bpd * p.oil_per_bbl + w.gas_mcfd * p.gas_per_mcf) / 24, True)
        for w in f.wells
    )
    t0 = 1791418800
    limits = ds.network.defaults.tank_limits_pct
    return TankScenario(f.id, f.tank.capacity_bbl, 50.0, t0, t0 + 6 * 3600, limits.warning, limits.shutdown, wells)


def test_readme_do_nothing(event9: TankScenario) -> None:
    """Do nothing: the tank trips at 04:48:48 ('04:49') and loses $5,016."""
    out = simulate(event9)
    assert out.tank_trips and out.trips_at_unix == 1791418800 + 4 * 3600 + 28 * 60 + 48
    assert out.cost_total == pytest.approx(5016, abs=1)


def test_readme_shut_in_one_well(event9: TankScenario) -> None:
    """Shut in S1_WELL_C2 at 02:25: the tank never trips; cost ≈ $1,958 ($500/h for 3 h 55 min)."""
    out = simulate(event9, shut_ins={"S1_WELL_C2": 1791418800 + 125 * 60})
    assert not out.tank_trips
    assert out.cost_total == pytest.approx(1958, abs=1)


def test_readme_one_truck(event9: TankScenario) -> None:
    """One truck arrives 75 min after the call (01:35), removes 150 bbl: no trip, $905."""
    truck = TruckPlan(1, 1, 75, 230, 150, 905)
    assert truck.arrivals(1791418800) == [1791418800 + 75 * 60]
    out = simulate(event9, trucks=truck)
    assert not out.tank_trips and out.cost_total == 905


def test_trip_after_tank_trips_does_not_help(event9: TankScenario) -> None:
    """SPEC: water is removed only when a trip arrives, so a trip after the tank tripped doesn't prevent it."""
    late = TruckPlan(1, 1, 5 * 60, 230, 150, 905)  # arrives 05:20, after the 04:49 trip
    out = simulate(event9, trucks=late)
    assert out.tank_trips and out.cost.trucks == 905 and out.cost.trip > 0


def test_full_week_reference_plans() -> None:
    """Each pump failure planned at its onset (trucks busy on earlier plans) matches the key's reference cost."""
    chosen = plan_sequential_at_onset(load_dataset(FULL_WEEK))
    labels = [lab for lab in json.loads((FULL_WEEK / "answer_key.json").read_text()) if lab["category"] == "pump_failure"]
    assert len(labels) == 13
    for lab in labels:
        c = chosen[f"{lab['asset']}@{lab['onset_unix']}"]
        assert close(c.cost_total, reference_cost(lab["reference_action"])), (lab["id"], c.label)


@pytest.mark.parametrize("folder", SNAPSHOTS, ids=lambda p: p.name)
def test_snapshot_joint_plans(folder: Path) -> None:
    """At each cut, every ongoing failure's part of the joint plan matches the key (same action type and cost)."""
    plans = plan_now(load_dataset(folder))
    for lab in json.loads((folder / "answer_key.json").read_text()):
        if lab.get("status") != "ongoing" or lab["category"] != "pump_failure":
            continue
        sp = plans[lab["system"]]
        c = next(c for eid, c in sp.selection.items() if eid.startswith(lab["asset"] + "@"))
        assert close(c.cost_total, reference_cost(lab["reference_action"])), (lab["id"], c.label)
        ref = lab["reference_action"].split(" (")[0]  # the action itself, without the "(~$…) (shares …)" notes
        assert ("truck" in ref) == (c.trucks_used > 0), (lab["id"], c.label)
        assert ("shut in" in ref) == bool(c.shut_ins), (lab["id"], c.label)


@pytest.mark.parametrize("folder", SNAPSHOTS, ids=lambda p: p.name)
def test_joint_plans_never_overuse_the_fleet(folder: Path) -> None:
    """§16: a truck can't be in two places; the joint plan never uses more trucks than the system has."""
    ds = load_dataset(folder)
    for sid, sp in plan_now(ds).items():
        assert sp.trucks_used <= ds.ops.systems[sid].emergency_fleet_size


def test_shared_trucks_change_the_plan() -> None:
    """Thu 12:00: on its own S2_FACILITY_C's cheapest option is a truck, but S2 has 2 trucks for 3 failures, so the
    joint plan gives it a late shut-in instead, and the total is cheaper than sending trucks naively."""
    sp = plan_now(load_dataset(HISTORY / "snapshot_2026-10-08T1200"))["S2"]
    c_id = next(eid for eid in sp.selection if eid.startswith("S2_PUMP_C@"))
    alone = sp.options[c_id].candidates[0]
    assert alone.trucks_used == 1 and alone.cost_total < sp.selection[c_id].cost_total
    assert sp.selection[c_id].shut_ins and sp.trucks_used == 2
    assert sp.total_cost < sp.do_nothing_cost
