"""Mock data for M0 (task T0.3): loads the JSON files in ``fixtures/`` and validates them.

Where this sits in the app
--------------------------
While ``NIGHTWATCH_MOCK=1``, the API answers from these fixtures instead of the real
engine. That lets P3 (agent) and P4 (frontend) build against realistic shapes from hour 1
while P1/P2 build the detector and optimizer. Each route switches to real code as its
task lands, and mock mode must be off for the demo.

The fixtures are static, hand-written examples shaped like the situation in
``history/snapshot_2026-10-08T1200`` (three S2 pump failures, a leak, two worn pumps).
Their numbers are illustrative, not real outputs. This module never reads
``answer_key.json``.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel

from .config import REPO_ROOT
from .models import (
    AgentRunResult,
    ExportEvent,
    PlanResponse,
    ReplayResult,
    SeriesResponse,
    StateResponse,
)

FIXTURES_DIR = REPO_ROOT / "fixtures"


# Which fixture file holds which model. Tests validate every entry (tests/test_models.py).
FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "state.json": StateResponse,
    "plan.json": PlanResponse,
    "series.json": SeriesResponse,
    "replay.json": ReplayResult,
    "agent_run.json": AgentRunResult,
}


@lru_cache
def _load(name: str) -> BaseModel:
    """Read ``fixtures/<name>`` and validate it against its model. Cached after the first read."""
    model = FIXTURE_MODELS[name]
    with open(FIXTURES_DIR / name) as f:
        return model.model_validate(json.load(f))


def _copy[M: BaseModel](name: str, model: type[M]) -> M:
    """Return a fresh copy of a fixture so callers can edit it without changing the cache."""
    cached = _load(name)
    assert isinstance(cached, model)
    return cached.model_copy(deep=True)


def mock_state(dataset_id: str) -> StateResponse:
    """Mock for ``GET /api/{ds}/state``: the network at the snapshot's 'now'."""
    state = _copy("state.json", StateResponse)
    state.dataset_id = dataset_id
    return state


def mock_plan(event_id: str) -> PlanResponse:
    """Mock for ``GET /api/{ds}/plan/{event_id}`` and ``POST /simulate``: one plan panel's data."""
    plan = _copy("plan.json", PlanResponse)
    plan.event_id = event_id
    return plan


def mock_series(asset_id: str, metric: str) -> SeriesResponse:
    """Mock for ``GET /api/{ds}/series``: a tank-level sawtooth relabelled to the requested sensor."""
    series = _copy("series.json", SeriesResponse)
    series.asset_id = asset_id
    series.metric = metric
    return series


def mock_replay(dataset_id: str, mode: str) -> ReplayResult:
    """Mock for ``GET /api/{ds}/replay``: savings replay totals, rows and cumulative curve."""
    replay = _copy("replay.json", ReplayResult)
    replay.dataset_id = dataset_id
    replay.mode = "long_run" if mode == "long_run" else "cheapest"
    return replay


def mock_agent_run() -> AgentRunResult:
    """Mock for ``POST /api/{ds}/agent/run``: a full six-step Sentinel run with drafts."""
    return _copy("agent_run.json", AgentRunResult)


def mock_events_export() -> list[ExportEvent]:
    """Mock for ``GET /api/{ds}/events.json``: the state's events in SPEC events.json shape.

    Built from the state fixture so the two always agree. Pump failures get the action of
    their alert's plan where the plan fixture covers them; everything else gets an inspection.
    """
    state = _copy("state.json", StateResponse)
    plan = _copy("plan.json", PlanResponse)
    out: list[ExportEvent] = []
    for ev in state.events:
        if ev.category == "pump_failure" and ev.id in plan.cheapest.event_ids:
            actions = plan.cheapest.actions
        elif ev.category == "pump_failure":
            actions = []  # the real export (T4.3) fills this from the optimizer
        else:
            actions = [{"type": "field_inspection", "asset_id": ev.asset_id}]
        out.append(
            ExportEvent.model_validate(
                {
                    "event_id": ev.id,
                    "system_id": ev.system_id,
                    "facility_id": ev.facility_id,
                    "asset_id": ev.asset_id,
                    "category": ev.category,
                    "status": ev.status,
                    "onset_ts": _iso(ev.onset_unix),
                    "onset_unix": ev.onset_unix,
                    "end_ts": _iso(ev.end_unix) if ev.end_unix else None,
                    "end_unix": ev.end_unix,
                    "confidence": ev.confidence,
                    "evidence": ev.evidence,
                    "level_pct": ev.level_pct,
                    "hours_to_limit": ev.hours_to_limit,
                    "recommended_actions": [a if isinstance(a, dict) else a.model_dump() for a in actions],
                }
            )
        )
    return out


def _iso(unix: int) -> str:
    """Unix seconds -> ISO 8601 UTC string like '2026-10-08T12:00:00Z' (the format SPEC uses)."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(unix, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
