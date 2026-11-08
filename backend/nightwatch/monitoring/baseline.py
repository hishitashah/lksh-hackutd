"""Normal operating behavior per asset (ARCHITECTURE.md §3.2).

What "normal" means here
------------------------
For each facility's pump + pipeline we learn, from the readings taken **while the pump is running**:

* pipeline pressure at the disposal end (psi)
* pump discharge pressure (psi)
* flow gap = pump flow − pipeline receive flow (bpd): ~0 when all the water arrives
* pump flow (bpd)

For each, the baseline is the **median** and the noise σ is the **MAD-based standard deviation** (1.4826 × median
absolute deviation). Both are robust: the few hours of a leak or a worn pump in a week barely move them, so the
baseline reflects normal operation without needing labels. Readings with the pump stopped or faulted are excluded,
because pressures then just equal the disposal pressure (SPEC "Pressure formulas").

The baseline is learned once from the loaded dataset's history (like fitting a model) and then applied to each
reading as it arrives; detection itself only looks at readings up to the time asked about. Thresholds are multiples
of σ (``config.py``), so they adapt to every asset and every network without hardcoded psi or bpd values.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass

import numpy as np

from ..config import get_settings
from ..models import Facility
from .telemetry_processor import Dataset

# 1.4826 × MAD estimates the standard deviation for normally distributed noise.
MAD_TO_SIGMA = 1.4826

# Lower bound on σ so a perfectly flat sensor can't cause a division by zero.
MIN_SIGMA = 1e-6

PUMP_RUNNING = 1  # SPEC telemetry: pump status 0 stopped, 1 running, 2 fault


@dataclass(frozen=True)
class Stat:
    """Robust summary of one signal during normal running: median, noise σ, and how many readings it came from."""

    median: float
    sigma: float
    n: int


@dataclass(frozen=True)
class PumpBaseline:
    """Normal behavior of one facility's pump + pipeline while the pump runs."""

    facility_id: str
    pipeline_pressure_psi: Stat
    discharge_pressure_psi: Stat
    flow_gap_bpd: Stat
    pump_flow_bpd: Stat


_cache: weakref.WeakKeyDictionary[Dataset, dict[str, PumpBaseline | None]] = weakref.WeakKeyDictionary()


def robust_stat(values: np.ndarray) -> Stat:
    """Median and MAD-based σ of the finite values in ``values``."""
    x = values[np.isfinite(values)]
    if x.size == 0:
        return Stat(median=float("nan"), sigma=MIN_SIGMA, n=0)
    med = float(np.median(x))
    sigma = max(MAD_TO_SIGMA * float(np.median(np.abs(x - med))), MIN_SIGMA)
    return Stat(median=med, sigma=sigma, n=int(x.size))


def running_mask(ds: Dataset, facility: Facility) -> np.ndarray:
    """Boolean array over ``ds.times``: True where this facility's pump reports 'running'."""
    return ds.series(facility.pump.id, "status") == PUMP_RUNNING


def pump_baseline(ds: Dataset, facility: Facility) -> PumpBaseline | None:
    """Learn (once per dataset) the normal running behavior of a facility's pump and pipeline.

    Returns None when there are too few running readings to trust a baseline (``baseline_min_samples``); the
    detector then skips leak/wear checks for that facility rather than guessing.
    """
    per_ds = _cache.setdefault(ds, {})
    if facility.id in per_ds:
        return per_ds[facility.id]

    run = running_mask(ds, facility)
    if int(run.sum()) < get_settings().baseline_min_samples:
        per_ds[facility.id] = None
        return None

    pump, pipe = facility.pump.id, facility.pipeline.id
    flow = ds.series(pump, "flow_bpd")[run]
    received = ds.series(pipe, "receive_flow_bpd")[run]
    baseline = PumpBaseline(
        facility_id=facility.id,
        pipeline_pressure_psi=robust_stat(ds.series(pipe, "pressure_psi")[run]),
        discharge_pressure_psi=robust_stat(ds.series(pump, "discharge_psi")[run]),
        flow_gap_bpd=robust_stat(flow - received),
        pump_flow_bpd=robust_stat(flow),
    )
    per_ds[facility.id] = baseline
    return baseline
