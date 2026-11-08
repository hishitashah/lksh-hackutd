"""Tank shutdown countdown with uncertainty (ARCHITECTURE.md §3.5).

How the estimate is made (all inputs from the dataset)
------------------------------------------------------
* **Current level:** a straight line fitted to the latest tank readings since the failure began, evaluated at the
  time asked about. The level sensor is noisy (e.g. it reads 78.49% when the tank is at 78.6%), and during a pump
  failure the level rises in a straight line, so the fit is more accurate than the last reading alone.
* **Fill rate (p50):** the baseline water of the wells still producing at that time ÷ 24 ÷ tank capacity × 100
  (SPEC "Normal behavior", pump flow = 0 while it's down). This reproduces the answer key's countdowns to about a
  minute: 1.419 / 2.317 / 2.599 h vs 1.42 / 2.33 / 2.58 for the three S2 failures at Thu 12:00.
* **Uncertainty (p10 / p90):** the spread between that rate and the rate actually observed in the tank readings,
  widened by the fit's level noise. Both rates agree when the data is clean, so the band is narrow.
* **Shutdown limit:** read only from ``network.json`` (``defaults.tank_limits_pct.shutdown``), never hardcoded.

If the facility's wells are already shut in (the tank tripped), the countdown is 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import get_settings
from ..models import Facility, HoursToLimit
from ..planning.cost_calculator import fill_rate_pct_per_hour
from .telemetry_processor import Dataset

WELL_PRODUCING = 1  # SPEC telemetry: well status 1 producing, 0 shut in
SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Countdown:
    """Result of a countdown estimate for one ongoing pump failure."""

    level_pct: float
    hours: HoursToLimit | None
    trips_at_unix: int | None
    fill_rate_pct_per_h: float
    observed_rate_pct_per_h: float | None
    shut_in: bool


def producing_wells(ds: Dataset, facility: Facility, at: int) -> list:
    """Wells of a facility whose status is 'producing' at ``at``."""
    return [w for w in facility.wells if ds.value_at(w.id, "status", at) == WELL_PRODUCING]


def first_shut_in_time(ds: Dataset, facility: Facility, since: int, at: int) -> int | None:
    """First time between ``since`` and ``at`` when every well of the facility was shut in (the tank tripped)."""
    mask = (ds.times >= since) & (ds.times <= at)
    if not mask.any():
        return None
    statuses = np.vstack([ds.series(w.id, "status")[mask] for w in facility.wells])
    all_off = np.all(statuses != WELL_PRODUCING, axis=0)
    return int(ds.times[mask][np.argmax(all_off)]) if all_off.any() else None


def estimate(ds: Dataset, facility: Facility, onset_unix: int, at: int) -> Countdown:
    """Estimate the level now and the hours until the tank reaches its shutdown limit.

    Args:
        ds: the loaded dataset.
        facility: the facility whose pump failed.
        onset_unix: when the failure started (only readings since then are fitted).
        at: the time asked about, unix seconds.
    """
    shutdown = ds.network.defaults.tank_limits_pct.shutdown
    tank_id, capacity = facility.tank.id, facility.tank.capacity_bbl

    # Current level: a line through the latest readings since onset (noise reduction).
    mask = (ds.times >= onset_unix) & (ds.times <= at)
    t = ds.times[mask].astype(float)
    y = ds.series(tank_id, "level_pct")[mask]
    finite = np.isfinite(y)
    t, y = t[finite][-get_settings().countdown_fit_samples :], y[finite][-get_settings().countdown_fit_samples :]
    observed_rate: float | None = None
    level_noise = 0.0
    if t.size >= 2 and t[-1] > t[0]:
        # Fit on times relative to the last reading: raw unix seconds (~1.8e9) make the fit numerically fragile.
        t0 = t[-1]
        slope, intercept = np.polyfit(t - t0, y, 1)
        level = float(slope * (at - t0) + intercept)
        level_noise = float(np.std(y - (slope * (t - t0) + intercept)))
        observed_rate = float(slope * SECONDS_PER_HOUR) if slope > 0 else None
    elif t.size:
        level = float(y[-1])
    else:
        level = ds.value_at(tank_id, "level_pct", at)
    level = min(level, shutdown)

    tripped_at = first_shut_in_time(ds, facility, onset_unix, at)
    if tripped_at is not None or level >= shutdown:
        zero = HoursToLimit(p50=0.0, p10=0.0, p90=0.0)
        return Countdown(level, zero, tripped_at or at, 0.0, observed_rate, shut_in=tripped_at is not None)

    water_bpd = sum(w.baseline_water_bpd for w in producing_wells(ds, facility, at))
    rate = fill_rate_pct_per_hour(water_bpd, capacity)
    if rate <= 0:
        # No producing wells feeding the tank: it isn't filling, so there is no countdown.
        return Countdown(level, None, None, 0.0, observed_rate, shut_in=False)

    room = shutdown - level
    p50 = room / rate
    alt = room / observed_rate if observed_rate else p50
    widen = 2.0 * level_noise / rate  # ± two standard deviations of level noise, in hours
    hours = HoursToLimit(
        p50=round(p50, 3),
        p10=round(max(0.0, min(p50, alt) - widen), 3),
        p90=round(max(p50, alt) + widen, 3),
    )
    return Countdown(
        level_pct=round(level, 2),
        hours=hours,
        trips_at_unix=int(at + p50 * SECONDS_PER_HOUR),
        fill_rate_pct_per_h=rate,
        observed_rate_pct_per_h=observed_rate,
        shut_in=False,
    )
