"""Anomaly detection: pump failures, pipeline leaks and worn pumps → incidents (ARCHITECTURE.md §3.3, §3.4).

The three rules (thresholds live in ``config.py``; σ = each asset's own noise from ``baseline.py``)
-----------------------------------------------------------------------------------------------
* **Pump failure:** the pump reports a fault (status 2). The incident lasts while the fault does. Ongoing failures
  get the shutdown countdown from ``countdown.py``.
* **Pipeline leak:** while the pump runs, averaged over the last few readings, the pipeline pressure at the disposal
  end is well below normal **and** water leaves the pump but doesn't arrive (pump flow − receive flow above normal).
  Both together, so a single noisy sensor can't raise a leak.
* **Worn pump:** while the pump runs, discharge pressure is well below normal **without** the leak signature. This
  is how a pump that moves less water each hour shows up in this data.

Multiple readings, not single spikes (§3.3 step 7): leaks and wear are judged on rolling averages of running
readings, need several abnormal readings in a row, and stretches separated by short normal gaps are merged into one
incident. The onset is moved back to where the signal first left normal (back-dating).

Checked on history/full_week (``scripts/score.py``): 13/13 pump failures with exact times, 10/10 leaks and worn
pumps, 0 false alarms, 0 duplicates. Leaks are flagged ~0.2-1.2 h after they start, worn pumps ~4.6-6.3 h.

Only readings up to the time asked about are used, so the same code serves "now" and any earlier moment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import get_settings
from ..models import Event, Facility
from ..planning.cost_calculator import facility_revenue_per_hour
from .baseline import PumpBaseline, pump_baseline, running_mask
from .countdown import estimate
from .telemetry_processor import Dataset

PUMP_FAULT = 2  # SPEC telemetry: pump status 2 = fault


def fmt_time(unix: int) -> str:
    """Unix seconds → 'Thu 09:00' in UTC, for evidence text (all data times are UTC)."""
    return datetime.fromtimestamp(unix, tz=UTC).strftime("%a %H:%M")


def detect(ds: Dataset, at: int | None = None) -> list[Event]:
    """Every incident that has started by ``at`` (None = 'now'), ongoing or resolved, in onset order."""
    return list(_detect_cached(ds, ds.clamp(at)))


@lru_cache(maxsize=512)
def _detect_cached(ds: Dataset, at: int) -> tuple[Event, ...]:
    """Cached detection per (dataset, time): the console and the agents ask for the same moment repeatedly."""
    events: list[Event] = []
    for system in ds.network.systems:
        for facility in system.facilities:
            failures = _pump_failures(ds, system.id, facility, at)
            events.extend(failures)
            events.extend(_leaks_and_wear(ds, system.id, facility, at, failures))
    events.sort(key=lambda e: (e.onset_unix, e.asset_id))
    return tuple(events)


# ---------------------------------------------------------------------------
# Pump failures
# ---------------------------------------------------------------------------


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [start, end] (inclusive) where ``flags`` is True."""
    padded = np.concatenate([[False], flags, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e) - 1) for s, e in zip(edges[::2], edges[1::2], strict=True)]


def _pump_failures(ds: Dataset, system_id: str, facility: Facility, at: int) -> list[Event]:
    """Pump faults (status 2) up to ``at``. Ongoing ones carry the level, countdown and trip time."""
    i_at = ds.index_at(at)
    if i_at < 0:
        return []
    pump = facility.pump.id
    fault = ds.series(pump, "status")[: i_at + 1] == PUMP_FAULT
    revenue = facility_revenue_per_hour(facility, ds.ops.prices)
    shutdown = ds.network.defaults.tank_limits_pct.shutdown
    events: list[Event] = []
    for start, end in _runs(fault):
        onset = int(ds.times[start])
        ongoing = end == i_at
        common = dict(
            id=f"{pump}@{onset}",
            category="pump_failure",
            system_id=system_id,
            facility_id=facility.id,
            asset_id=pump,
            onset_unix=onset,
            confidence=1.0,
            lost_revenue_per_hour=round(revenue, 2),
        )
        if not ongoing:
            end_unix = int(ds.times[end + 1])
            evidence = [f"Pump reported a fault (status 2) from {fmt_time(onset)} to {fmt_time(end_unix)}"]
            events.append(Event(status="resolved", end_unix=end_unix, evidence=evidence, **common))
            continue

        cd = estimate(ds, facility, onset, at)
        flow_now = ds.value_at(pump, "flow_bpd", at)
        evidence = [f"Pump reports a fault (status 2) since {fmt_time(onset)}; flow out of the tank {flow_now:.0f} bpd"]
        if cd.shut_in and cd.trips_at_unix is not None:
            evidence.append(
                f"Tank reached the {shutdown:.0f}% shutdown limit at {fmt_time(cd.trips_at_unix)}; "
                "its wells are shut in until the repair"
            )
        elif cd.hours is not None:
            evidence.append(
                f"Tank at {cd.level_pct:.1f}% and rising about {cd.fill_rate_pct_per_h:.1f}%/h; "
                f"shutdown limit {shutdown:.0f}%"
            )
        events.append(
            Event(
                status="ongoing",
                end_unix=None,
                evidence=evidence,
                level_pct=cd.level_pct,
                hours_to_limit=cd.hours,
                trips_at_unix=cd.trips_at_unix,
                **common,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Pipeline leaks and worn pumps
# ---------------------------------------------------------------------------


def _segments(flags: np.ndarray, run_times: np.ndarray, merge_gap_s: float, min_len: int) -> list[tuple[int, int]]:
    """Abnormal stretches over running readings, merged across short gaps, dropping ones that are too short.

    Merging (§3.3): a leak's readings are interrupted every time the pump stops (the sawtooth), and noise can
    briefly dip below a threshold. Stretches closer than ``merge_gap_s`` are one problem, not several incidents.
    """
    merged: list[list[int]] = []
    for s, e in _runs(flags):
        if merged and run_times[s] - run_times[merged[-1][1]] <= merge_gap_s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s + 1 >= min_len]


def _backdate(signal: np.ndarray, start: int, sigma: float) -> int:
    """Move an onset back while the signal was already more than ``sigma`` away from normal."""
    b = start
    while b > 0 and np.isfinite(signal[b - 1]) and signal[b - 1] > sigma:
        b -= 1
    return b


def _confidence(z: float, threshold: float) -> float:
    """0.6 at the threshold, rising 0.08 per σ beyond it, capped at 0.99 (documented, not tuned)."""
    return round(float(min(0.99, max(0.6, 0.6 + 0.08 * (z - threshold)))), 2)


def _leaks_and_wear(ds: Dataset, system_id: str, facility: Facility, at: int, failures: list[Event]) -> list[Event]:
    """Leak and worn-pump incidents for one facility, judged on running readings up to ``at``."""
    cfg = get_settings()
    base: PumpBaseline | None = pump_baseline(ds, facility)
    i_at = ds.index_at(at)
    if base is None or i_at < 0:
        return []  # too little history to know what normal looks like: don't guess
    idx = np.flatnonzero(running_mask(ds, facility)[: i_at + 1])
    if idx.size < cfg.detect_window_samples:
        return []

    pump, pipe = facility.pump.id, facility.pipeline.id
    run_times = ds.times[idx]
    flow = ds.series(pump, "flow_bpd")[idx]
    gap = flow - ds.series(pipe, "receive_flow_bpd")[idx]

    def avg(x: np.ndarray) -> np.ndarray:
        """Rolling mean over the last few running readings (multi-reading evidence, §3.3 step 7)."""
        win = cfg.detect_window_samples
        return pd.Series(x).rolling(win, min_periods=max(2, win - 2)).mean().to_numpy()

    flow_avg, gap_avg = avg(flow), avg(gap)
    pipe_avg = avg(ds.series(pipe, "pressure_psi")[idx])
    disch_avg = avg(ds.series(pump, "discharge_psi")[idx])

    # Distances from normal in σ units (positive = in the "problem" direction).
    pressure_drop_z = (base.pipeline_pressure_psi.median - pipe_avg) / base.pipeline_pressure_psi.sigma
    gap_z = (gap_avg - base.flow_gap_bpd.median) / base.flow_gap_bpd.sigma
    discharge_drop_z = (base.discharge_pressure_psi.median - disch_avg) / base.discharge_pressure_psi.sigma

    with np.errstate(invalid="ignore"):
        leak_flags = (pressure_drop_z > cfg.leak_pressure_sigma) & (gap_z > cfg.leak_gap_sigma)
        # A worn pump has low discharge pressure but no leak signature (pipeline pressure and flow balance normal).
        wear_flags = (
            (discharge_drop_z > cfg.wear_discharge_sigma)
            & ~(pressure_drop_z > 0.6 * cfg.leak_pressure_sigma)
            & ~(gap_z > cfg.leak_gap_sigma)
        )

    merge_s = cfg.merge_gap_hours * 3600
    events: list[Event] = []
    for category, flags, signal, threshold, asset in (
        ("pipeline_leak", leak_flags, pressure_drop_z, cfg.leak_pressure_sigma, pipe),
        ("pump_degradation", wear_flags, discharge_drop_z, cfg.wear_discharge_sigma, pump),
    ):
        for s, e in _segments(flags, run_times, merge_s, cfg.min_segment_samples):
            onset = int(run_times[_backdate(signal, s, cfg.backdate_sigma)])
            last_bad = int(run_times[e])
            end_unix: int | None = None
            if category == "pump_degradation":
                # ARCHITECTURE.md / SPEC: a worn pump that then fails is two incidents; the wear ends at the failure.
                follow = [f for f in failures if onset < f.onset_unix <= last_bad + merge_s]
                if follow:
                    end_unix = follow[0].onset_unix
            if end_unix is None and idx.size - 1 - e >= cfg.min_segment_samples:
                # Resolved only after several normal running readings in a row: one quiet reading at the edge of
                # the data is noise, not a repair (§3.3 step 7).
                end_unix = int(run_times[e + 1])  # first normal running reading after the stretch
            evidence = _evidence(category, base, e, pressure_drop_z, gap_avg, discharge_drop_z, flow_avg, onset, last_bad)
            if end_unix is None and at - int(run_times[-1]) > 3600:
                # The pump hasn't run for over an hour, so a repair (if any) can't be seen yet. Say so honestly.
                evidence.append(
                    f"Last checked while pumping at {fmt_time(int(run_times[-1]))}; "
                    "this can only be re-checked when the pump runs again"
                )
            events.append(
                Event(
                    id=f"{asset}@{onset}",
                    category=category,
                    system_id=system_id,
                    facility_id=facility.id,
                    asset_id=asset,
                    status="ongoing" if end_unix is None or end_unix > at else "resolved",
                    onset_unix=onset,
                    end_unix=end_unix if end_unix is not None and end_unix <= at else None,
                    confidence=_confidence(float(signal[e]), threshold),
                    evidence=evidence,
                )
            )
    return events


def _evidence(
    category: str,
    base: PumpBaseline,
    e: int,
    pressure_drop_z: np.ndarray,
    gap_avg: np.ndarray,
    discharge_drop_z: np.ndarray,
    flow_avg: np.ndarray,
    onset: int,
    last_bad: int,
) -> list[str]:
    """Plain-English reasons with the numbers behind a leak or worn-pump incident (latest abnormal readings)."""
    hours = (last_bad - onset) / 3600
    if category == "pipeline_leak":
        drop = pressure_drop_z[e] * base.pipeline_pressure_psi.sigma
        missing = gap_avg[e] - base.flow_gap_bpd.median
        return [
            f"Pipeline pressure about {drop:.0f} psi below its normal level while pumping",
            f"About {missing:.0f} bpd leaves the pump but doesn't arrive at the disposal site",
            f"Abnormal since {fmt_time(onset)} ({hours:.1f} h)",
        ]
    drop = discharge_drop_z[e] * base.discharge_pressure_psi.sigma
    flow_loss_pct = (base.pump_flow_bpd.median - flow_avg[e]) / base.pump_flow_bpd.median * 100
    return [
        f"Discharge pressure about {drop:.0f} psi below normal while pumping",
        f"Pump flow about {flow_loss_pct:.0f}% below its normal running flow",
        "Pipeline pressure and flow balance look normal, so this is not a leak",
        f"Abnormal since {fmt_time(onset)} ({hours:.1f} h)",
    ]
