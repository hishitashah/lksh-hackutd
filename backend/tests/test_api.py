"""API tests (T0.1 / T0.3): every route answers with the right shape, in mock mode and out of it.

What's covered:
* Real routes (/health, /datasets, /{ds}/network) work and read the files correctly.
* Mock routes return fixture data that validates against the response model.
* With mock mode off, unbuilt routes return 501 naming their task (so mocks can't leak into the demo).
* Unknown datasets return 404.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from nightwatch import api
from nightwatch.config import get_settings
from nightwatch.models import (
    AgentRunResult,
    AgentStep,
    DraftAction,
    ExportEvent,
    PlanResponse,
    Recommendation,
    ReplayResult,
    SeriesResponse,
    StateResponse,
)

DS = "snapshot_2026-10-08T1200"

# Routes that serve fixtures until their phase lands: (method, path, json body or None).
# /state and /series are real now (Stage 1 monitoring) and are tested separately.
MOCK_ROUTES = [
    ("get", f"/api/{DS}/plan/EV-0009", None),
    ("post", f"/api/{DS}/simulate", {"event_id": "EV-0009", "actions": [{"type": "none"}]}),
    ("get", f"/api/{DS}/replay", None),
    ("get", f"/api/{DS}/events.json", None),
    ("get", f"/api/{DS}/agent/runs/run-1", None),
    ("post", f"/api/{DS}/agent/approve", {"run_id": "run-1", "action_id": "d-1", "approve": True}),
    ("get", f"/api/{DS}/dispatches", None),
    ("get", f"/api/{DS}/field/stream", None),
]


def read_sse(resp) -> list[tuple[str, str]]:
    """Collect (event name, data) pairs from a server-sent-events response."""
    events: list[tuple[str, str]] = []
    name = None
    for line in resp.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            events.append((name, line.split(":", 1)[1].strip()))
    return events


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """A test client in mock mode with no delay between streamed agent steps."""
    monkeypatch.setenv("NIGHTWATCH_MOCK", "1")
    monkeypatch.setenv("DATA_DIRS", "history,test")
    monkeypatch.setattr(api, "MOCK_STEP_DELAY_S", 0)
    get_settings.cache_clear()
    yield TestClient(api.app)
    get_settings.cache_clear()


@pytest.fixture
def real_client(monkeypatch) -> TestClient:
    """A test client with mock mode OFF, as it must be for the demo."""
    monkeypatch.setenv("NIGHTWATCH_MOCK", "0")
    get_settings.cache_clear()
    yield TestClient(api.app)
    get_settings.cache_clear()


def test_health_reports_mock_flag(client: TestClient) -> None:
    """The UI's MOCK DATA badge depends on this flag."""
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["mock"] is True


def test_datasets_lists_history_folders(client: TestClient) -> None:
    """All five history folders are found; answer keys are never listed as datasets."""
    ids = {d["id"] for d in client.get("/api/datasets").json()}
    assert {
        "full_week",
        "snapshot_2026-10-08T1200",
        "snapshot_2026-10-09T0330",
        "snapshot_2026-10-10T0900",
        "mock_test_2026-10-10T1530",
    } <= ids


def test_network_reads_files_and_time_range(client: TestClient) -> None:
    """Network comes from the files; the time range comes from the telemetry (SPEC: no file states it)."""
    body = client.get("/api/full_week/network").json()
    assert body["network"]["defaults"]["tank_limits_pct"]["shutdown"] == 92
    assert len(body["network"]["systems"]) == 5
    # SPEC / CLAUDE.md: the week runs 1791158400 to 1791762900 on a 5-minute grid.
    assert body["time_range"] == {"start_unix": 1791158400, "now_unix": 1791762900, "step_s": 300}


def test_snapshot_now_is_its_last_row(client: TestClient) -> None:
    """A snapshot's 'now' is its own last telemetry timestamp (Thu 10-08 12:00 = 1791460800)."""
    body = client.get(f"/api/{DS}/network").json()
    assert body["time_range"]["now_unix"] == 1791460800


def test_unknown_dataset_is_404(client: TestClient) -> None:
    """A typo in the dataset ID gives a clear 404, not a crash."""
    assert client.get("/api/nope/network").status_code == 404
    assert client.get("/api/nope/state").status_code == 404


@pytest.mark.parametrize("method,path,body", MOCK_ROUTES)
def test_mock_routes_answer(client: TestClient, method: str, path: str, body) -> None:
    """Every mock route answers 200 in mock mode."""
    resp = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert resp.status_code == 200, resp.text


def test_mock_payloads_match_models(client: TestClient) -> None:
    """Mock responses validate against the response models the frontend types are generated from."""
    StateResponse.model_validate(client.get(f"/api/{DS}/state").json())
    SeriesResponse.model_validate(client.get(f"/api/{DS}/series?asset=S2_TANK_B&metric=level_pct").json())
    PlanResponse.model_validate(client.get(f"/api/{DS}/plan/EV-0009").json())
    ReplayResult.model_validate(client.get(f"/api/{DS}/replay").json())
    for item in client.get(f"/api/{DS}/events.json").json():
        ExportEvent.model_validate(item)


def test_state_alerts_follow_spec_order(client: TestClient) -> None:
    """SPEC alerts view: pump failures first by hours_to_limit, then leaks and worn pumps."""
    alerts = client.get(f"/api/{DS}/state").json()["alerts"]
    failures = [a for a in alerts if a["event"]["category"] == "pump_failure"]
    hours = [a["event"]["hours_to_limit"]["p50"] for a in failures]
    assert hours == sorted(hours)
    first_other = next(i for i, a in enumerate(alerts) if a["event"]["category"] != "pump_failure")
    assert all(a["event"]["category"] != "pump_failure" for a in alerts[first_other:])


def test_agent_run_streams_multi_agent_flow(client: TestClient) -> None:
    """Sentinel streams triage → specialist agents → orchestrator → act, then recommendation, drafts, done."""
    with client.stream("POST", f"/api/{DS}/agent/run") as resp:
        assert resp.status_code == 200
        events = read_sse(resp)
    steps = [AgentStep.model_validate_json(data) for name, data in events if name == "step"]
    assert [s.step for s in steps] == [
        "triage",
        "cost",
        "environment",
        "feasibility",
        "production",
        "orchestrator",
        "act",
    ]
    # Specialist steps carry their structured assessment, under their own agent name.
    for s in steps:
        if s.step in ("cost", "environment", "feasibility", "production"):
            assert s.assessment is not None and s.assessment.agent == s.step
    recommendation = Recommendation.model_validate_json(next(d for n, d in events if n == "recommendation"))
    assert recommendation.requires_operator_approval is True
    drafts = next(json.loads(data) for name, data in events if name == "drafts")
    assert all(DraftAction.model_validate(d).status == "pending" for d in drafts)
    assert [n for n, _ in events][-3:] == ["recommendation", "drafts", "done"]


def test_unbuilt_agents_are_never_shown_as_completed(client: TestClient) -> None:
    """Spec §9.2: agents that didn't run are marked not_implemented and listed as missing, never faked."""
    run = AgentRunResult.model_validate(client.get(f"/api/{DS}/agent/runs/run-1").json())
    by_agent = {s.assessment.agent: s for s in run.steps if s.assessment is not None}
    for agent in ("feasibility", "production"):
        assert by_agent[agent].status == "not_implemented"
        assert by_agent[agent].assessment.status == "not_implemented"
    assert run.recommendation is not None
    assert set(run.recommendation.missing_assessments) == {"feasibility", "production"}
    assert run.recommendation.status == "partial"
    # The recommendation only uses plans the optimizer produced: its total is the sum of their costs.
    total = sum(p.cost_total for p in run.recommendation.selected_plans)
    assert abs(total - run.recommendation.estimated_cost_usd) < 0.01


def test_approve_stamps_the_decision(client: TestClient) -> None:
    """Approving records the operator's decision and time; rejecting is recorded too."""
    approved = DraftAction.model_validate(
        client.post(f"/api/{DS}/agent/approve", json={"run_id": "r", "action_id": "d-1", "approve": True}).json()
    )
    assert approved.status == "approved" and approved.decided_at is not None
    rejected = client.post(f"/api/{DS}/agent/approve", json={"run_id": "r", "action_id": "d-2", "approve": False})
    assert rejected.json()["status"] == "rejected"


def test_field_feed_only_carries_dispatched_orders(client: TestClient) -> None:
    """/dispatches and /field/stream carry orders that were sent to the field, never pending drafts."""
    listed = [DraftAction.model_validate(d) for d in client.get(f"/api/{DS}/dispatches").json()]
    assert listed and all(d.status == "dispatched" and d.dispatched_at for d in listed)
    with client.stream("GET", f"/api/{DS}/field/stream") as resp:
        assert resp.status_code == 200
        events = read_sse(resp)
    streamed = [DraftAction.model_validate_json(data) for name, data in events if name == "dispatch"]
    assert {d.id for d in streamed} == {d.id for d in listed}


def test_approve_unknown_draft_is_404(client: TestClient) -> None:
    """Approving a draft that doesn't exist gives a clear 404."""
    resp = client.post(f"/api/{DS}/agent/approve", json={"run_id": "r", "action_id": "nope", "approve": True})
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path,body", MOCK_ROUTES)
def test_unbuilt_routes_501_without_mock(real_client: TestClient, method: str, path: str, body) -> None:
    """With mock mode off, unbuilt routes refuse with 501 and name their task, so fixtures can't reach the demo."""
    resp = getattr(real_client, method)(path, json=body) if body else getattr(real_client, method)(path)
    assert resp.status_code == 501
    assert "task T" in resp.json()["detail"]


def test_real_routes_work_without_mock(real_client: TestClient) -> None:
    """Health, datasets, network, state and series are real: they don't depend on mock mode."""
    assert real_client.get("/api/health").json()["mock"] is False
    assert real_client.get("/api/datasets").status_code == 200
    assert real_client.get("/api/full_week/network").status_code == 200
    state = StateResponse.model_validate(real_client.get(f"/api/{DS}/state").json())
    assert state.at_unix == state.now_unix == 1791460800  # 'now' = the snapshot's last telemetry row
    series = SeriesResponse.model_validate(real_client.get(f"/api/{DS}/series?asset=S2_TANK_B&metric=level_pct").json())
    assert series.points[-1][0] == 1791460800


def test_real_state_is_the_demo_scenario(real_client: TestClient) -> None:
    """ARCHITECTURE.md §16: at Thu 12:00 the three S2 pump failures come first, soonest shutdown first."""
    alerts = real_client.get(f"/api/{DS}/state").json()["alerts"]
    failures = [a["event"]["asset_id"] for a in alerts if a["event"]["category"] == "pump_failure"]
    assert failures == ["S2_PUMP_B", "S2_PUMP_C", "S2_PUMP_A"]
    others = {a["event"]["asset_id"] for a in alerts if a["event"]["category"] != "pump_failure"}
    assert others == {"S5_PIPELINE_B", "S3_PUMP_A", "S4_PUMP_C"}


def test_series_unknown_sensor_is_404(real_client: TestClient) -> None:
    """Asking for a sensor that doesn't exist gives a clear 404, not an empty chart."""
    assert real_client.get(f"/api/{DS}/series?asset=NOPE&metric=level_pct").status_code == 404
