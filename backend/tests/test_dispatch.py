"""Approval and field dispatch tests (ARCHITECTURE.md §10, §15 Phase 8, §16; Phase 9's approve/reject/field checks).

Checks, with mock mode OFF (the demo setting) and drafts from a real Sentinel run:
* nothing is dispatched on an AI recommendation alone; only an operator approval dispatches;
* approve → dispatched (stamped, listed, visible in the stored run); reject → never reaches the field;
* clear 404 / 409 / 422 errors; a draft is decided once;
* safety rules: no duplicate order across runs, never more trucks than the system has (draft stays pending);
* the /field/stream feed: current orders on connect, and new ones pushed live.
No live LLM: no key is set.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from nightwatch import api, llm
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentRunResult, ApproveRequest, DraftAction, PlanAction
from nightwatch.monitoring.telemetry_processor import load_dataset
from nightwatch.services import dispatch_service, sentinel_service
from tests.test_api import read_sse

DS = "snapshot_2026-10-08T1200"
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Mock mode off, no NVIDIA key, a short field-stream window, and no orders left from other tests."""
    monkeypatch.setenv("NIGHTWATCH_MOCK", "0")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("FIELD_STREAM_MAX_S", "0.2")
    get_settings.cache_clear()
    llm.set_llm_override(None)
    dispatch_service.reset()
    yield
    dispatch_service.reset()
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """A test client for the real routes."""
    return TestClient(api.app)


def new_run(client: TestClient) -> tuple[str, list[DraftAction]]:
    """Run Sentinel on the Thursday demo and return (run ID, its drafts)."""
    with client.stream("POST", f"/api/{DS}/agent/run") as resp:
        events = read_sse(resp)
    run_id = json.loads(next(d for n, d in events if n == "done"))["run_id"]
    drafts = [DraftAction.model_validate(d) for d in json.loads(next(d for n, d in events if n == "drafts"))]
    return run_id, drafts


def decide(client: TestClient, run_id: str, action_id: str, approve: bool, ds: str = DS):
    """POST /agent/approve."""
    return client.post(f"/api/{ds}/agent/approve", json={"run_id": run_id, "action_id": action_id, "approve": approve})


def stored(client: TestClient, run_id: str) -> AgentRunResult:
    """GET /agent/runs/{id}."""
    return AgentRunResult.model_validate(client.get(f"/api/{DS}/agent/runs/{run_id}").json())


def dispatches(client: TestClient) -> list[DraftAction]:
    """GET /dispatches."""
    return [DraftAction.model_validate(d) for d in client.get(f"/api/{DS}/dispatches").json()]


def truck_draft(drafts: list[DraftAction], facility: str) -> DraftAction:
    """The run's truck draft for one facility."""
    return next(d for d in drafts if d.kind == "dispatch_truck" and d.action.facility_id == facility)


# ---------------------------------------------------------------------------
# The lifecycle
# ---------------------------------------------------------------------------


def test_ai_recommendation_alone_dispatches_nothing(client: TestClient) -> None:
    """§10: a recommendation is not a dispatch. After a run, every draft is pending and the field list is empty."""
    run_id, drafts = new_run(client)
    assert drafts and all(d.status == "pending" for d in drafts)
    assert dispatches(client) == []
    assert all(d.status == "pending" for d in stored(client, run_id).drafts)


def test_approve_dispatches_and_is_traceable(client: TestClient) -> None:
    """Approve → 'dispatched' with both stamps, listed for the field, and the stored run shows the same status."""
    run_id, drafts = new_run(client)
    b = truck_draft(drafts, "S2_FACILITY_B")
    resp = decide(client, run_id, b.id, True)
    assert resp.status_code == 200, resp.text
    out = DraftAction.model_validate(resp.json())
    assert out.id == b.id and out.status == "dispatched"
    assert ISO_UTC.match(out.decided_at or "") and ISO_UTC.match(out.dispatched_at or "")
    assert out.action == b.action and out.label == b.label  # exactly what the operator saw is what is sent
    assert [d.id for d in dispatches(client)] == [b.id]
    run = stored(client, run_id)
    assert next(d for d in run.drafts if d.id == b.id).status == "dispatched"
    assert all(d.status == "pending" for d in run.drafts if d.id != b.id)  # nothing else was touched


def test_reject_never_reaches_the_field(client: TestClient) -> None:
    """Reject → 'rejected', stamped, never dispatched; deciding again is a 409."""
    run_id, drafts = new_run(client)
    insp = next(d for d in drafts if d.kind == "field_inspection")
    out = DraftAction.model_validate(decide(client, run_id, insp.id, False).json())
    assert out.status == "rejected" and out.decided_at and out.dispatched_at is None
    assert dispatches(client) == []
    again = decide(client, run_id, insp.id, True)
    assert again.status_code == 409 and "already rejected" in again.json()["detail"]


def test_newest_dispatch_first(client: TestClient) -> None:
    """/dispatches lists orders newest first and only approved ones."""
    run_id, drafts = new_run(client)
    b, a = truck_draft(drafts, "S2_FACILITY_B"), truck_draft(drafts, "S2_FACILITY_A")
    insp = next(d for d in drafts if d.kind == "field_inspection")
    decide(client, run_id, b.id, True)
    decide(client, run_id, insp.id, False)
    decide(client, run_id, a.id, True)
    assert [d.id for d in dispatches(client)] == [a.id, b.id]


# ---------------------------------------------------------------------------
# Errors and safety rules
# ---------------------------------------------------------------------------


def test_clear_errors(client: TestClient) -> None:
    """404 unknown run / draft / other dataset's run; 409 deciding twice; 422 malformed body."""
    run_id, drafts = new_run(client)
    assert decide(client, "run-nope", drafts[0].id, True).status_code == 404
    assert decide(client, run_id, "nope", True).status_code == 404
    assert decide(client, run_id, drafts[0].id, True, ds="full_week").status_code == 404  # runs are per dataset
    assert decide(client, run_id, drafts[0].id, True).status_code == 200
    second = decide(client, run_id, drafts[0].id, True)
    assert second.status_code == 409 and "already dispatched" in second.json()["detail"]
    assert client.post(f"/api/{DS}/agent/approve", json={"run_id": run_id}).status_code == 422


def test_duplicate_order_from_another_run_is_refused(client: TestClient) -> None:
    """Two runs propose the same truck: only the first approval dispatches; the second stays pending (409)."""
    run1, drafts1 = new_run(client)
    run2, drafts2 = new_run(client)
    b1, b2 = truck_draft(drafts1, "S2_FACILITY_B"), truck_draft(drafts2, "S2_FACILITY_B")
    assert decide(client, run1, b1.id, True).status_code == 200
    dup = decide(client, run2, b2.id, True)
    assert dup.status_code == 409 and b1.id in dup.json()["detail"]
    assert next(d for d in stored(client, run2).drafts if d.id == b2.id).status == "pending"
    assert [d.id for d in dispatches(client)] == [b1.id]


def test_never_more_trucks_than_the_system_has(client: TestClient) -> None:
    """§16: S2 has 2 trucks (ops.json). With both dispatched, a third truck order is refused and stays pending."""
    run_id, drafts = new_run(client)
    for facility in ("S2_FACILITY_B", "S2_FACILITY_A"):
        assert decide(client, run_id, truck_draft(drafts, facility).id, True).status_code == 200
    extra = DraftAction(
        id="run-test-d1",
        kind="dispatch_truck",
        label="Send 1 truck to S2_FACILITY_C",
        action=PlanAction(type="dispatch_truck", facility_id="S2_FACILITY_C", trucks=1, trips_per_truck=1),
        event_id="S2_PUMP_C@1791451800",
        status="pending",
    )
    sentinel_service._store(DS, AgentRunResult(run_id="run-test", steps=[], recommendation=None, drafts=[extra]))
    fleet = load_dataset(REPO_ROOT / "history" / DS).ops.systems["S2"].emergency_fleet_size
    resp = decide(client, "run-test", extra.id, True)
    assert resp.status_code == 409
    assert f"S2 has {fleet} trucks and {fleet} are already dispatched" in resp.json()["detail"]
    assert extra.status == "pending" and extra.decided_at is None  # nothing half-happened


# ---------------------------------------------------------------------------
# The field feed
# ---------------------------------------------------------------------------


def test_field_stream_sends_current_orders_on_connect(client: TestClient) -> None:
    """On connect: one 'dispatch' event per dispatched order (with the 3 s retry hint); rejected ones never."""
    run_id, drafts = new_run(client)
    b, a = truck_draft(drafts, "S2_FACILITY_B"), truck_draft(drafts, "S2_FACILITY_A")
    insp = next(d for d in drafts if d.kind == "field_inspection")
    decide(client, run_id, b.id, True)
    decide(client, run_id, a.id, True)
    decide(client, run_id, insp.id, False)
    with client.stream("GET", f"/api/{DS}/field/stream") as resp:
        assert resp.status_code == 200
        lines = list(resp.iter_lines())
    events = read_sse(SavedLines(lines))
    streamed = [DraftAction.model_validate_json(d) for n, d in events if n == "dispatch"]
    assert [d.id for d in streamed] == [b.id, a.id]
    assert all(d.status == "dispatched" for d in streamed)
    assert {d.id for d in streamed} == {d.id for d in dispatches(client)}
    assert any(line.startswith("retry:") and "3000" in line for line in lines)


class SavedLines:
    """Wraps already-read SSE lines so ``read_sse`` can parse them."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def iter_lines(self):
        """Yield the stored lines."""
        return iter(self.lines)


def test_approved_order_is_pushed_live(client: TestClient) -> None:
    """A phone already listening receives the order the moment the operator approves it."""
    run_id, drafts = new_run(client)
    ds = load_dataset(REPO_ROOT / "history" / DS)
    b = truck_draft(drafts, "S2_FACILITY_B")

    async def main() -> DraftAction:
        feed = dispatch_service.subscribe(ds.id, max_s=2.0)
        first = asyncio.ensure_future(feed.__anext__())  # connects, sends the (empty) snapshot, then waits
        await asyncio.sleep(0.05)
        assert not first.done()  # nothing dispatched yet: nothing pushed
        dispatch_service.approve(ds, ApproveRequest(run_id=run_id, action_id=b.id, approve=True))
        try:
            return await asyncio.wait_for(first, timeout=1.0)
        finally:
            await feed.aclose()

    pushed = asyncio.run(main())
    assert pushed.id == b.id and pushed.status == "dispatched"
