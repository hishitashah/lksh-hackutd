"""The network state at one moment, for ``GET /api/{ds}/state`` (ARCHITECTURE.md §12; frontend ``useNetworkState``).

Builds a ``StateResponse`` from monitoring output:

* **facilities:** each facility's tank level, pump status, inflow, producing wells, and whether it's shut in;
* **events:** every incident started by that time (``monitoring.anomaly_detector.detect``);
* **alerts:** the ongoing incidents in the order SPEC's alerts view requires: pump failures first, soonest shutdown
  first, ties going to the higher lost revenue per hour; then leaks and worn pumps (any order, here by onset);
* **lost_revenue_per_hour_total:** revenue lost right now by facilities whose wells are shut in.

Alert labels: leaks and worn pumps get "Send inspector to …" (SPEC: they get a field inspection). Pump failures
say "Plan pending" until the intervention planner (Phase 3) exists, so no cost is shown before it's computed.
"""

from __future__ import annotations

import math
from functools import lru_cache

from ..models import Alert, Event, FacilityState, StateResponse
from ..monitoring.anomaly_detector import detect
from ..monitoring.countdown import WELL_PRODUCING
from ..monitoring.telemetry_processor import Dataset
from ..planning.cost_calculator import facility_revenue_per_hour

PLAN_PENDING_LABEL = "Plan pending (cost analysis not built yet)"


def build_state(ds: Dataset, at: int | None = None) -> StateResponse:
    """The whole network as of ``at`` (None = the dataset's 'now')."""
    return _build_state_cached(ds, ds.clamp(at)).model_copy(deep=True)


@lru_cache(maxsize=256)
def _build_state_cached(ds: Dataset, at: int) -> StateResponse:
    """Cached per (dataset, time): the console polls the same moment repeatedly."""
    prices = ds.ops.prices
    facilities: list[FacilityState] = []
    lost_total = 0.0
    for system in ds.network.systems:
        for f in system.facilities:
            producing = sum(1 for w in f.wells if ds.value_at(w.id, "status", at) == WELL_PRODUCING)
            shut_in = producing == 0
            if shut_in:
                lost_total += facility_revenue_per_hour(f, prices)
            pump_status = ds.value_at(f.pump.id, "status", at)
            facilities.append(
                FacilityState(
                    facility_id=f.id,
                    system_id=system.id,
                    tank_level_pct=round(_finite(ds.value_at(f.tank.id, "level_pct", at)), 2),
                    pump_status=int(pump_status) if math.isfinite(pump_status) else 0,
                    inflow_bpd=round(_finite(ds.value_at(f.id, "inflow_bpd", at)), 1),
                    wells_producing=producing,
                    wells_total=len(f.wells),
                    shut_in=shut_in,
                )
            )
    events = detect(ds, at)
    return StateResponse(
        dataset_id=ds.id,
        at_unix=at,
        now_unix=ds.now_unix,
        facilities=facilities,
        alerts=build_alerts(events),
        events=events,
        lost_revenue_per_hour_total=round(lost_total, 2),
    )


def build_alerts(events: list[Event], labels: dict[str, str] | None = None) -> list[Alert]:
    """Ongoing incidents in the SPEC alerts-view order, each with a one-line action.

    Args:
        events: incidents from the detector.
        labels: optional action label per pump-failure event ID (from the planner, Phase 3).
    """
    labels = labels or {}
    ongoing = [e for e in events if e.status == "ongoing"]
    failures = sorted(
        (e for e in ongoing if e.category == "pump_failure"),
        key=lambda e: (
            e.hours_to_limit.p50 if e.hours_to_limit is not None else math.inf,
            -(e.lost_revenue_per_hour or 0.0),
        ),
    )
    others = sorted((e for e in ongoing if e.category != "pump_failure"), key=lambda e: e.onset_unix)
    alerts = [
        Alert(event=e, order=i, action_label=labels.get(e.id, PLAN_PENDING_LABEL)) for i, e in enumerate(failures, start=1)
    ]
    alerts += [Alert(event=e, order=None, action_label=f"Send inspector to {e.asset_id}") for e in others]
    return alerts


def _finite(x: float) -> float:
    """NaN → 0.0 so a missing reading never breaks the response (the frontend shows 0)."""
    return x if math.isfinite(x) else 0.0
