"""Stage 1 monitoring tests (ARCHITECTURE.md §17 "Monitoring tests", Phase 2).

Covers: loading + validation, detection of all three problem types against the answer keys (tests may read
answer keys; the app never does), no false alarms on normal readings, no duplicate incidents, the incident
lifecycle, the shutdown countdown with uncertainty, the alert order, and that the shutdown limit is read from
network.json rather than hardcoded.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from nightwatch.config import REPO_ROOT
from nightwatch.monitoring.anomaly_detector import detect
from nightwatch.monitoring.incident_manager import IncidentManager, replay
from nightwatch.monitoring.telemetry_processor import TelemetryValidationError, load_dataset
from nightwatch.services.incident_service import build_state

sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts"))
from score import score_dataset  # noqa: E402  (dev scorer; the only non-test code that reads answer keys)

HISTORY = REPO_ROOT / "history"
FULL_WEEK = HISTORY / "full_week"
SNAPSHOTS = sorted(p for p in HISTORY.glob("snapshot_*") if p.is_dir())
THURSDAY = HISTORY / "snapshot_2026-10-08T1200"


def answer_key(folder: Path) -> list[dict]:
    """The labelled events for a folder (tests only)."""
    return json.loads((folder / "answer_key.json").read_text())


# --- loading + validation (§3.1, §3.3 steps 1-3) ------------------------------------------------


@pytest.mark.parametrize("folder", sorted(p for p in HISTORY.iterdir() if p.is_dir()), ids=lambda p: p.name)
def test_every_history_folder_loads_quickly(folder: Path) -> None:
    """Each dataset loads in under 3 s, cleanly, and 'now' is its last telemetry row."""
    start = time.perf_counter()
    ds = load_dataset(folder)
    assert time.perf_counter() - start < 3
    assert ds.validation.issues == []
    assert ds.step_s == 300
    with open(folder / "telemetry.csv") as f:
        last_row = f.read().strip().splitlines()[-1]
    assert ds.now_unix == int(last_row.split(",")[1])


def test_bad_telemetry_is_rejected_clearly(tmp_path: Path) -> None:
    """A telemetry file without the required columns fails validation with a readable message."""
    for name in ("network.json", "ops.json"):
        shutil.copy(FULL_WEEK / name, tmp_path / name)
    (tmp_path / "telemetry.csv").write_text("timestamp,asset_id,value\n2026-10-05T00:00:00Z,S1_TANK_A,50\n")
    with pytest.raises(TelemetryValidationError, match="missing column"):
        load_dataset(tmp_path)


def test_non_numeric_rows_are_dropped_and_reported(tmp_path: Path) -> None:
    """Bad readings are dropped and counted, never silently used (§13 'make uncertainty visible')."""
    for name in ("network.json", "ops.json"):
        shutil.copy(FULL_WEEK / name, tmp_path / name)
    lines = (FULL_WEEK / "telemetry.csv").read_text().splitlines()[:400]
    lines[5] = lines[5].rsplit(",", 1)[0] + ",not-a-number"
    (tmp_path / "telemetry.csv").write_text("\n".join(lines) + "\n")
    ds = load_dataset(tmp_path)
    assert ds.validation.rows_dropped_non_numeric == 1
    assert any("non-numeric" in issue for issue in ds.validation.issues)


# --- detection (§3.3, §3.4) -------------------------------------------------------------------


def test_full_week_finds_every_problem_with_no_false_alarms() -> None:
    """All 23 labelled problems are found; normal readings create no incidents; no duplicates."""
    s = score_dataset(FULL_WEEK)
    assert s.found == s.labels == 23, s.missed
    assert s.false_alarms == []
    assert s.duplicates == []


def test_pump_failures_are_exact() -> None:
    """Pump faults (status 2) give exact onset and end times for all 13 failures."""
    events = {(e.asset_id, e.onset_unix): e for e in detect(load_dataset(FULL_WEEK)) if e.category == "pump_failure"}
    labels = [lab for lab in answer_key(FULL_WEEK) if lab["category"] == "pump_failure"]
    assert len(labels) == 13
    for lab in labels:
        ev = events[(lab["asset"], lab["onset_unix"])]
        assert ev.end_unix == lab["end_unix"]


@pytest.mark.parametrize("folder", SNAPSHOTS, ids=lambda p: p.name)
def test_snapshots_find_every_ongoing_problem(folder: Path) -> None:
    """At each cut, every ongoing labelled problem is detected as ongoing.

    An extra 'ongoing' leak/wear is only acceptable when the pump hasn't run since the label's repair, because a
    pipeline can't be re-checked without flow (the evidence then says so).
    """
    s = score_dataset(folder)
    assert s.ongoing_missing == []
    ds = load_dataset(folder)
    for extra in s.ongoing_extra:
        _, asset = extra.split()
        ev = next(e for e in detect(ds) if e.asset_id == asset and e.status == "ongoing")
        assert "can only be re-checked" in ev.evidence[-1]


def test_worn_pump_ends_where_the_failure_starts() -> None:
    """SPEC: a degrading pump that later fails is two incidents; the wear ends at the failure's onset."""
    events = [e for e in detect(load_dataset(FULL_WEEK)) if e.asset_id == "S1_PUMP_C"]
    wear = next(e for e in events if e.category == "pump_degradation")
    failure = next(e for e in events if e.category == "pump_failure" and e.onset_unix == 1791418800)
    assert wear.end_unix == failure.onset_unix


# --- countdown (§3.5) -------------------------------------------------------------------------


def test_countdown_matches_the_answer_key() -> None:
    """Thu 12:00: level within 0.5 points and p50 within 0.05 h of the key, with p10 <= p50 <= p90."""
    events = {e.asset_id: e for e in detect(load_dataset(THURSDAY)) if e.status == "ongoing"}
    for lab in answer_key(THURSDAY):
        if lab["status"] != "ongoing" or lab["category"] != "pump_failure":
            continue
        ev = events[lab["asset"]]
        assert abs(ev.level_pct - lab["level_final_pct"]) <= 0.5
        h = ev.hours_to_limit
        assert abs(h.p50 - lab["hours_to_limit_final"]) <= 0.05
        assert h.p10 <= h.p50 <= h.p90


def test_shutdown_limit_comes_from_network_json(tmp_path: Path) -> None:
    """Lowering the shutdown limit in network.json shortens the countdown: nothing is hardcoded (§13)."""
    for name in ("ops.json", "telemetry.csv"):
        shutil.copy(THURSDAY / name, tmp_path / name)
    network = json.loads((THURSDAY / "network.json").read_text())
    network["defaults"]["tank_limits_pct"]["shutdown"] = 85
    (tmp_path / "network.json").write_text(json.dumps(network))
    original = {e.asset_id: e.hours_to_limit.p50 for e in detect(load_dataset(THURSDAY)) if e.hours_to_limit}
    lowered = {e.asset_id: e.hours_to_limit.p50 for e in detect(load_dataset(tmp_path)) if e.hours_to_limit}
    assert all(lowered[a] < original[a] for a in original)


# --- alerts order (SPEC alerts view) --------------------------------------------------------------


@pytest.mark.parametrize("folder", SNAPSHOTS, ids=lambda p: p.name)
def test_alert_order_matches_the_key(folder: Path) -> None:
    """Pump failures are ordered exactly as the key's `order` (soonest shutdown first, ties by lost revenue)."""
    state = build_state(load_dataset(folder))
    got = [a.event.asset_id for a in state.alerts if a.order is not None]
    want = [lab["asset"] for lab in sorted(answer_key(folder), key=lambda x: x.get("order") or 99) if lab.get("order")]
    assert got == want


# --- incident lifecycle + no duplicates (§3.3) ---------------------------------------------------


def test_replay_creates_one_incident_per_problem_and_resolves_it() -> None:
    """Replaying Wednesday night to Thursday morning every 30 min: S1_PUMP_C's failure is one incident that goes
    new → (active/updated) → resolved, and no problem gets two incidents."""
    ds = load_dataset(FULL_WEEK)
    manager = replay(ds, start=1791410400, until=1791446400, every_s=1800)  # Wed 22:00 → Thu 08:00
    failure = manager.incidents["S1_PUMP_C@1791418800"]
    states = [state for _, state in failure.history]
    assert states[0] == "new" and states[-1] == "resolved"
    keys = [(i.event.category, i.event.asset_id, i.event.onset_unix) for i in manager.incidents.values()]
    assert len(keys) == len(set(keys))


def test_repeated_readings_do_not_duplicate_incidents() -> None:
    """Processing the same moment twice reports the incident once as new, then only as active."""
    ds = load_dataset(THURSDAY)
    manager = IncidentManager()
    first = manager.process(detect(ds), ds.now_unix)
    second = manager.process(detect(ds), ds.now_unix)
    assert {t.lifecycle for t in first} <= {"new", "resolved"}
    assert all(t.lifecycle != "new" for t in second)
    assert len(manager.incidents) == len(detect(ds))
