"""The HTTP API the frontend talks to (T0.1 skeleton; filled in through M1-M5, finished in M4).

Where this sits in the app
--------------------------
The React app calls these routes through the Vite dev proxy (``/api`` -> port 8000).
Every response is a model from ``models.py``, so the generated TypeScript types match.

How routes are built up over the hackathon
------------------------------------------
* Real now: ``/health``, ``/datasets`` (folder scan) and ``/{ds}/network`` (reads
  network.json + ops.json and the first/last telemetry timestamps).
* Mock now: everything else answers from ``fixtures/`` while ``NIGHTWATCH_MOCK=1``.
  With mock mode off, those routes return 501 naming the task that will implement them.
  When your task lands, replace the ``_require_mock(...)`` branch with the real call.

Never read ``answer_key.json`` here: the app finds every problem from the sensor data.
"""

from __future__ import annotations

import asyncio
import json
import os
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
    Network,
    NetworkResponse,
    Ops,
    PlanResponse,
    ReplayResult,
    SeriesResponse,
    SimulateRequest,
    StateResponse,
    TimeRange,
)

app = FastAPI(
    title="Avon Nightwatch API",
    version=__version__,
    description="Backend for the Avon Nightwatch operator console. See plan.md for the design.",
)

# The three files every dataset folder must contain (README "What you get").
DATASET_FILES = ("network.json", "ops.json", "telemetry.csv")

# Pause between mock Sentinel steps so the UI can show them arriving one by one.
# Tests set this to 0.
MOCK_STEP_DELAY_S = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def scan_datasets() -> dict[str, Path]:
    """Find every dataset folder under the configured ``DATA_DIRS``.

    A folder is a dataset when it holds all three files. A top-level folder that is a
    dataset itself (``test/``) gets its own name as ID; otherwise each qualifying
    subfolder does (``history/full_week`` -> ``full_week``). Runs on every request, so a
    newly pulled ``test/`` folder shows up without restarting the server.

    Returns:
        Mapping of dataset ID -> absolute folder path, sorted by ID. If two folders share
        a name, the first one found wins.
    """
    found: dict[str, Path] = {}
    for rel in get_settings().data_dir_list:
        root = REPO_ROOT / rel
        if not root.is_dir():
            continue  # e.g. test/ before the organizers release it
        candidates = [root] if _is_dataset(root) else sorted(p for p in root.iterdir() if p.is_dir())
        for folder in candidates:
            if _is_dataset(folder):
                found.setdefault(folder.name, folder)
    return dict(sorted(found.items()))


def _is_dataset(folder: Path) -> bool:
    """True if ``folder`` contains network.json, ops.json and telemetry.csv."""
    return all((folder / name).is_file() for name in DATASET_FILES)


def _dataset_path(ds: str) -> Path:
    """Resolve a dataset ID to its folder, or answer 404 if it doesn't exist."""
    path = scan_datasets().get(ds)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset '{ds}'. See GET /api/datasets.")
    return path


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


def read_time_range(csv_path: Path) -> TimeRange:
    """Read the dataset's time window from telemetry.csv without loading the whole file.

    SPEC: no file states the time range; it is the first and last ``ts_unix`` in the
    telemetry, and the last one is "now". We read the header, the first rows (to find
    the step between readings) and the last line (via a seek from the end).

    T1.1 (data.py) replaces this with the full loader; keep the same results.
    """
    with open(csv_path, "rb") as f:
        header = f.readline().decode().strip().split(",")
        ts_col = header.index("ts_unix")

        # First timestamp, then keep reading until the timestamp changes to get the step.
        first_line = f.readline()
        first_ts = int(first_line.split(b",")[ts_col])
        step_s = 0
        for line in f:
            ts = int(line.split(b",")[ts_col])
            if ts != first_ts:
                step_s = ts - first_ts
                break

        # Last timestamp: read the final few KB and take the last non-empty line.
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 4096))
        tail = [ln for ln in f.read().splitlines() if ln.strip()]
        last_ts = int(tail[-1].split(b",")[ts_col])

    return TimeRange(start_unix=first_ts, now_unix=last_ts, step_s=step_s)


# ---------------------------------------------------------------------------
# Routes: real now
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

    Real in M0 so the map (T6.3) can be built on real geometry. Reads only the three
    dataset files, never the answer key.
    """
    folder = _dataset_path(ds)
    network = Network.model_validate(json.loads((folder / "network.json").read_text()))
    ops = Ops.model_validate(json.loads((folder / "ops.json").read_text()))
    return NetworkResponse(dataset_id=ds, network=network, ops=ops, time_range=read_time_range(folder / "telemetry.csv"))


# ---------------------------------------------------------------------------
# Routes: mock until their task lands
# ---------------------------------------------------------------------------


@app.get("/api/{ds}/state", response_model=StateResponse, tags=["console"])
def get_state(
    ds: str,
    at: int | None = Query(default=None, description="Unix seconds to view. Omit for the dataset's 'now'."),
) -> StateResponse:
    """The whole network as of one moment: facilities, ordered alerts and events (T2.5 + T2.7)."""
    _dataset_path(ds)
    _require_mock("T2.5")
    return mocks.mock_state(ds)


@app.get("/api/{ds}/series", response_model=SeriesResponse, tags=["console"])
def get_series(
    ds: str,
    asset: str = Query(description="Asset ID, e.g. 'S2_TANK_B'."),
    metric: str = Query(description="Metric from telemetry.csv, e.g. 'level_pct'."),
    from_unix: int | None = Query(default=None, alias="from", description="Start, unix seconds."),
    to_unix: int | None = Query(default=None, alias="to", description="End, unix seconds."),
) -> SeriesResponse:
    """One sensor's readings for the trend charts (T1.1 loader + T6.6 chart)."""
    _dataset_path(ds)
    _require_mock("T1.1")
    return mocks.mock_series(asset, metric)


@app.get("/api/{ds}/plan/{event_id}", response_model=PlanResponse, tags=["plans"])
def get_plan(
    ds: str,
    event_id: str,
    at: int | None = Query(default=None, description="Plan start, unix seconds. Omit for 'now'."),
) -> PlanResponse:
    """Plan panel data for one pump failure: cheapest, long run, do nothing, alternatives (T3.5, T3.6)."""
    _dataset_path(ds)
    _require_mock("T3.5")
    return mocks.mock_plan(event_id)


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
                "Server-sent events. 'step' events carry one AgentStep as JSON, then one 'drafts' event "
                "carries the list of DraftAction, then 'done' carries {run_id, handoff}."
            ),
            "content": {"text/event-stream": {}},
        }
    },
)
async def run_agent(ds: str) -> EventSourceResponse:
    """Start a Sentinel run and stream its six steps live (T5.3).

    Use a fetch-based SSE reader in the frontend: browsers' EventSource only supports GET.
    """
    _dataset_path(ds)
    _require_mock("T5.3")
    run = mocks.mock_agent_run()

    async def stream() -> AsyncIterator[dict[str, str]]:
        """Yield the mock run step by step, pausing so the UI shows progress."""
        for step in run.steps:
            await asyncio.sleep(MOCK_STEP_DELAY_S)
            yield {"event": "step", "data": step.model_dump_json()}
        yield {"event": "drafts", "data": json.dumps([d.model_dump() for d in run.drafts])}
        yield {"event": "done", "data": json.dumps({"run_id": run.run_id, "handoff": run.handoff})}

    return EventSourceResponse(stream())


@app.get("/api/{ds}/agent/runs/{run_id}", response_model=AgentRunResult, tags=["sentinel"])
def get_agent_run(ds: str, run_id: str) -> AgentRunResult:
    """Fetch a finished Sentinel run (e.g. after a page reload) (T5.3)."""
    _dataset_path(ds)
    _require_mock("T5.3")
    run = mocks.mock_agent_run()
    run.run_id = run_id
    return run


@app.post("/api/{ds}/agent/approve", response_model=DraftAction, tags=["sentinel"])
def approve_action(ds: str, body: ApproveRequest) -> DraftAction:
    """Approve or reject one drafted action. On approve, the real version notifies the field crew (T5.5)."""
    _dataset_path(ds)
    _require_mock("T5.5")
    for draft in mocks.mock_agent_run().drafts:
        if draft.id == body.action_id:
            draft.status = "approved" if body.approve else "rejected"
            return draft
    raise HTTPException(status_code=404, detail=f"Unknown draft action '{body.action_id}'.")
