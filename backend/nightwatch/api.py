"""The HTTP API the frontend talks to (ARCHITECTURE.md §12).

Where this sits in the app
--------------------------
The React app calls these routes through the Vite dev proxy (``/api`` -> port 8000).
Every response is a model from ``models.py``, so the generated TypeScript types match.

Real vs mock
------------
* **Real:** ``/health``, ``/datasets``, ``/{ds}/network``, ``/{ds}/state``, ``/{ds}/series`` (Stage 1 monitoring,
  ``monitoring/`` + ``services/incident_service.py``), ``/{ds}/plan/{event_id}`` (the deterministic planner,
  ``planning/``), the Sentinel run, ``/{ds}/agent/run`` + ``/{ds}/agent/runs/{run_id}`` (the multi-agent
  orchestrator, streamed by ``services/sentinel_service.py``), and approval + field dispatch, ``/{ds}/agent/approve``,
  ``/{ds}/dispatches``, ``/{ds}/field/stream`` (``services/dispatch_service.py``). Real routes ignore
  ``NIGHTWATCH_MOCK``.
* **Mock only (cut from the MVP):** simulate, replay and the events.json export answer from ``fixtures/`` while
  ``NIGHTWATCH_MOCK=1``; with mock mode off they return 501, so fixtures can never reach the demo.

Never read ``answer_key.json`` here: the app finds every problem from the sensor data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from . import __version__, mocks
from .config import REPO_ROOT, get_settings
from .models import (
    AgentRunResult,
    ApproveRequest,
    DatasetInfo,
    DraftAction,
    ExportEvent,
    Health,
    NetworkResponse,
    PlanResponse,
    ReplayResult,
    SeriesResponse,
    SimulateRequest,
    StateResponse,
)
from .monitoring.anomaly_detector import detect
from .monitoring.telemetry_processor import Dataset, TelemetryValidationError, get_dataset, scan_datasets
from .planning.intervention_planner import plan_response
from .services import dispatch_service, sentinel_service
from .services.incident_service import build_state

app = FastAPI(
    title="Avon Nightwatch API",
    version=__version__,
    description="Backend for the Avon Nightwatch operator console. See ARCHITECTURE.md for the design.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dataset_path(ds: str) -> Path:
    """Resolve a dataset ID to its folder, or answer 404 if it doesn't exist."""
    path = scan_datasets().get(ds)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset '{ds}'. See GET /api/datasets.")
    return path


def _dataset(ds: str) -> Dataset:
    """Load a dataset for a real route: 404 if unknown, 422 if its files fail validation (§3.3 step 2)."""
    try:
        return get_dataset(ds)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown dataset '{ds}'. See GET /api/datasets.") from None
    except TelemetryValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from None


def _require_mock(task: str) -> None:
    """Allow a not-yet-built route to answer only in mock mode.

    Raises 501 when mock mode is off, naming the task that will implement the route, so a
    half-built backend can never silently serve fixtures in the demo.
    """
    if not get_settings().nightwatch_mock:
        raise HTTPException(
            status_code=501,
            detail=f"Not implemented yet (task {task}). Set NIGHTWATCH_MOCK=1 to use fixtures.",
        )


# ---------------------------------------------------------------------------
# Routes: real
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=Health, tags=["meta"])
def health() -> Health:
    """Liveness check. ``mock`` tells the UI whether to show the MOCK DATA badge."""
    return Health(ok=True, mock=get_settings().nightwatch_mock, version=__version__)


@app.get("/api/datasets", response_model=list[DatasetInfo], tags=["datasets"])
def list_datasets() -> list[DatasetInfo]:
    """Every dataset folder the app can load (feeds the dataset switcher, T6.2)."""
    return [
        DatasetInfo(id=ds, path=str(path.relative_to(REPO_ROOT)), source="folder") for ds, path in scan_datasets().items()
    ]


@app.post("/api/datasets/upload", response_model=DatasetInfo, tags=["datasets"])
def upload_dataset() -> DatasetInfo:
    """Upload the 3 files as a new dataset, for the deployed copy at judging (task T4.2)."""
    raise HTTPException(status_code=501, detail="Not implemented yet (task T4.2).")


@app.get("/api/{ds}/network", response_model=NetworkResponse, tags=["datasets"])
def get_network(ds: str) -> NetworkResponse:
    """Static description of a dataset: network.json, ops.json and the telemetry time window.

    The time range comes from the telemetry (SPEC: no file states it). Never reads the answer key.
    """
    d = _dataset(ds)
    return NetworkResponse(dataset_id=ds, network=d.network, ops=d.ops, time_range=d.time_range)


@app.get("/api/{ds}/state", response_model=StateResponse, tags=["console"])
def get_state(
    ds: str,
    at: int | None = Query(default=None, description="Unix seconds to view. Omit for the dataset's 'now'."),
) -> StateResponse:
    """The whole network as of one moment: facilities, incidents, and alerts in SPEC order (Stage 1, §3)."""
    return build_state(_dataset(ds), at)


@app.get("/api/{ds}/series", response_model=SeriesResponse, tags=["console"])
def get_series(
    ds: str,
    asset: str = Query(description="Asset ID, e.g. 'S2_TANK_B'."),
    metric: str = Query(description="Metric from telemetry.csv, e.g. 'level_pct'."),
    from_unix: int | None = Query(default=None, alias="from", description="Start, unix seconds."),
    to_unix: int | None = Query(default=None, alias="to", description="End, unix seconds."),
) -> SeriesResponse:
    """One sensor's readings over time, for the trend charts. 404 if the dataset has no such sensor."""
    d = _dataset(ds)
    if not d.has(asset, metric):
        raise HTTPException(status_code=404, detail=f"No telemetry for asset '{asset}' metric '{metric}'.")
    times, values = d.window(asset, metric, from_unix, to_unix)
    points = [[int(t), float(v)] for t, v in zip(times, values, strict=True) if v == v]  # skip NaN gaps
    return SeriesResponse(asset_id=asset, metric=metric, points=points)


# ---------------------------------------------------------------------------
# Routes: planning and Sentinel (real), and routes still mock until their phase lands (``_require_mock``)
# ---------------------------------------------------------------------------


@app.get("/api/{ds}/plan/{event_id}", response_model=PlanResponse, tags=["plans"])
def get_plan(
    ds: str,
    event_id: str,
    at: int | None = Query(default=None, description="Plan start, unix seconds. Omit for 'now'."),
) -> PlanResponse:
    """Plan panel for one ongoing pump failure (ARCHITECTURE.md §5.3-5.5, §16).

    Every number comes from the deterministic planner: the recommended plan is this failure's part of its system's
    joint plan (shared trucks), plus doing nothing, the next alternatives and the tank-level traces.

    Errors: 404 unknown event; 400 for a leak or worn pump (they get an inspection, not a plan); 409 if the pump
    failure has already been resolved at that time.
    """
    d = _dataset(ds)
    at = d.clamp(at)
    event = next((e for e in detect(d, at) if e.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail=f"No incident '{event_id}' at that time.")
    if event.category != "pump_failure":
        raise HTTPException(
            status_code=400, detail="Plans are for pump failures; leaks and worn pumps get a field inspection."
        )
    if event.status != "ongoing":
        raise HTTPException(status_code=409, detail=f"'{event_id}' was already resolved at that time.")
    return plan_response(d, event_id, at)


@app.post("/api/{ds}/simulate", response_model=PlanResponse, tags=["plans"])
def simulate(ds: str, body: SimulateRequest) -> PlanResponse:
    """Simulate a custom what-if plan with the same rules as the optimizer (T3.1 + T6.7)."""
    _dataset_path(ds)
    _require_mock("T3.1")
    return mocks.mock_plan(body.event_id)


@app.get("/api/{ds}/replay", response_model=ReplayResult, tags=["plans"])
def get_replay(
    ds: str,
    mode: Literal["cheapest", "long_run"] = Query(default="long_run", description="Planner for the per-event rows."),
) -> ReplayResult:
    """Savings replay: did nothing vs cheapest per event vs long run over the whole window (T3.7)."""
    _dataset_path(ds)
    _require_mock("T3.7")
    return mocks.mock_replay(ds, mode)


@app.get("/api/{ds}/events.json", response_model=list[ExportEvent], tags=["export"])
def export_events(
    ds: str,
    at: int | None = Query(default=None, description="Export as of this time, unix seconds. Omit for 'now'."),
) -> list[ExportEvent]:
    """Download events in the SPEC events.json shape (T4.3)."""
    _dataset_path(ds)
    _require_mock("T4.3")
    return mocks.mock_events_export()


@app.post(
    "/api/{ds}/agent/run",
    tags=["sentinel"],
    responses={
        200: {
            "description": (
                "Server-sent events, in order: 'step' events (one AgentStep each: index 0 triage; one per specialist "
                "agent, first 'running' and then again with its final status and AgentAssessment, since they run in "
                "parallel; the orchestrator, 'running' then 'done'; act). A step index can arrive twice: replace it "
                "by index. Then one 'recommendation' event (the orchestrator's Recommendation), one 'drafts' event "
                "(list of DraftAction, all pending) and 'done' ({run_id, handoff})."
            ),
            "content": {"text/event-stream": {}},
        }
    },
)
async def run_agent(
    ds: str,
    at: int | None = Query(default=None, description="Unix seconds to analyse (time machine). Omit for 'now'."),
) -> EventSourceResponse:
    """Start a Sentinel run and stream it live (ARCHITECTURE.md §11, Phase 7).

    The specialist agents run in parallel, then the orchestrator decides and Nemotron explains; drafts wait for the
    operator (nothing is dispatched here). Use a fetch-based SSE reader in the frontend: EventSource is GET-only.
    """
    d = _dataset(ds)
    _, events = sentinel_service.start_run(d, at)
    return EventSourceResponse(events)


@app.get("/api/{ds}/agent/runs/{run_id}", response_model=AgentRunResult, tags=["sentinel"])
def get_agent_run(ds: str, run_id: str) -> AgentRunResult:
    """A finished Sentinel run (e.g. after a page reload): final steps, recommendation, drafts, handoff. 404 if unknown."""
    d = _dataset(ds)
    run = sentinel_service.get_run(d.id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No finished run '{run_id}' for dataset '{ds}'.")
    return run


@app.post("/api/{ds}/agent/approve", response_model=DraftAction, tags=["sentinel"])
async def approve_action(ds: str, body: ApproveRequest) -> DraftAction:
    """Approve or reject one drafted action (ARCHITECTURE.md §10, Phase 8). Nothing is dispatched without this.

    Approve: the draft is dispatched to the field right away and returned with status 'dispatched' (plus
    decided_at, dispatched_at). Reject: returned 'rejected'; it never reaches the field.
    Errors: 404 unknown run or draft; 409 already decided, already dispatched by another run, or more trucks than the
    system has (the detail says which; the draft stays pending).
    """
    d = _dataset(ds)
    try:
        return dispatch_service.approve(d, body)
    except dispatch_service.DispatchError as err:
        raise HTTPException(status_code=err.status_code, detail=str(err)) from None


@app.get("/api/{ds}/dispatches", response_model=list[DraftAction], tags=["field"])
def list_dispatches(ds: str) -> list[DraftAction]:
    """Orders sent to the field app so far (status 'dispatched' or later), newest first (Phase 8).

    Only operator-approved actions are here: pending and rejected drafts never are. The /field page loads this
    once, then listens on /field/stream for new ones.
    """
    return dispatch_service.list_dispatches(_dataset(ds).id)


@app.get(
    "/api/{ds}/field/stream",
    tags=["field"],
    responses={
        200: {
            "description": (
                "Server-sent events over GET, so the browser's built-in EventSource works on any phone. On connect, "
                "one 'dispatch' event per order already dispatched, then one per new order as the operator approves "
                "it; each carries one DraftAction (status 'dispatched') as JSON. The stream closes after a few "
                "minutes and EventSource reconnects on its own (retry hint 3 s). De-duplicate cards by DraftAction.id."
            ),
            "content": {"text/event-stream": {}},
        }
    },
)
async def field_stream(ds: str) -> EventSourceResponse:
    """Live feed of dispatched orders for the /field phone view (ARCHITECTURE.md §10 step 8, Phase 8)."""
    d = _dataset(ds)

    async def stream() -> AsyncIterator[dict[str, str | int]]:
        """Every current order, then each new one live, until the connection's time limit."""
        async for order in dispatch_service.subscribe(d.id):
            yield {"event": "dispatch", "data": order.model_dump_json(), "retry": 3000}

    return EventSourceResponse(stream())
