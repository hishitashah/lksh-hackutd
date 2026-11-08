"""Sentinel streaming tests (ARCHITECTURE.md §11, §15 Phase 7, §17).

Checks the real ``POST /api/{ds}/agent/run`` stream and ``GET /agent/runs/{id}`` with mock mode OFF (as in the demo):
the event order and shapes the frontend brief documents, the four agents starting together, honest statuses for
agents that didn't run, drafts built only from the orchestrator's recommendation (all pending, never dispatched),
the handoff, the stored run, a crashing agent, no incidents, and the time machine. No live LLM: no key is set.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from nightwatch import api, llm
from nightwatch.agents.base_agent import BaseAgent, IncidentContext
from nightwatch.agents.cost_agent import CostAgent
from nightwatch.agents.feasibility_agent import FeasibilityAgent
from nightwatch.agents.production_agent import ProductionAgent
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentAssessment, AgentRunResult, AgentStep, DraftAction, Recommendation, StateResponse
from nightwatch.monitoring.telemetry_processor import Dataset, load_dataset
from nightwatch.services import sentinel_service
from tests.test_api import read_sse

DS = "snapshot_2026-10-08T1200"
THURSDAY_1030 = 1791455400  # 1.5 h before the snapshot's 'now' (Thu 12:00)
QUIET_MONDAY_01 = 1791162000  # full_week Mon 01:00: nothing ongoing
AGENT_STEPS = ("cost", "environment", "feasibility", "production")


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Mock mode off (the demo setting), no NVIDIA key and no override: nothing calls the real API."""
    monkeypatch.setenv("NIGHTWATCH_MOCK", "0")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    get_settings.cache_clear()
    llm.set_llm_override(None)
    yield
    llm.set_llm_override(None)
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """A test client for the real routes."""
    return TestClient(api.app)


def stream(client: TestClient, ds: str = DS, at: int | None = None) -> list[tuple[str, str]]:
    """POST /agent/run and collect every (event, data) pair."""
    url = f"/api/{ds}/agent/run" + (f"?at={at}" if at is not None else "")
    with client.stream("POST", url) as resp:
        assert resp.status_code == 200
        return read_sse(resp)


def steps_of(events: list[tuple[str, str]]) -> list[AgentStep]:
    """Every step event, in arrival order (an index may appear twice: running, then final)."""
    return [AgentStep.model_validate_json(d) for n, d in events if n == "step"]


def final_steps(events: list[tuple[str, str]]) -> dict[int, AgentStep]:
    """The last version of each step, by index (what the frontend shows)."""
    out: dict[int, AgentStep] = {}
    for st in steps_of(events):
        out[st.index] = st
    return out


def payload(events: list[tuple[str, str]], name: str):
    """The JSON data of the one event with this name."""
    return json.loads(next(d for n, d in events if n == name))


# ---------------------------------------------------------------------------
# The demo run: Thursday 12:00
# ---------------------------------------------------------------------------


def test_stream_order_and_shapes(client: TestClient) -> None:
    """triage → four agents (running together, then final) → orchestrator → act → recommendation, drafts, done."""
    events = stream(client)
    names = [n for n, _ in events]
    assert names[-3:] == ["recommendation", "drafts", "done"]
    assert all(n == "step" for n in names[:-3])
    steps = steps_of(events)
    assert steps[0].step == "triage" and steps[0].status == "done"
    # All four agents start before any of them finishes: they run in parallel, and the operator sees it.
    running = [i for i, st in enumerate(steps) if st.step in AGENT_STEPS and st.status == "running"]
    finished = [i for i, st in enumerate(steps) if st.step in AGENT_STEPS and st.status != "running"]
    assert len(running) == len(finished) == 4 and max(running) < min(finished)
    final = final_steps(events)
    assert [final[i].step for i in sorted(final)] == ["triage", *AGENT_STEPS, "orchestrator", "act"]
    assert final[1].status == final[2].status == "done"
    assert final[3].status == final[4].status == "not_implemented"  # rule 5: never shown as done
    assert final[5].status == "done" and final[6].status == "waiting_approval"
    for i in (1, 2, 3, 4):
        assert final[i].assessment is not None and final[i].assessment.agent == final[i].step
    run_id = payload(events, "done")["run_id"]
    assert {st.run_id for st in steps} == {run_id}
    elapsed = [st.elapsed_s for st in steps]
    assert elapsed == sorted(elapsed)
    rec = Recommendation.model_validate_json(next(d for n, d in events if n == "recommendation"))
    assert rec.requires_operator_approval is True and rec.status == "partial"
    assert rec.missing_assessments == ["feasibility", "production"]


def test_drafts_come_from_the_recommendation(client: TestClient) -> None:
    """The §16 joint plan as approval cards: trucks to B and A, shut in S2_WELL_C1, then inspections (S5 first)."""
    events = stream(client)
    drafts = [DraftAction.model_validate(d) for d in payload(events, "drafts")]
    rec = Recommendation.model_validate_json(next(d for n, d in events if n == "recommendation"))
    assert [d.kind for d in drafts] == [
        "dispatch_truck",
        "shut_in_wells",
        "dispatch_truck",
        "field_inspection",
        "field_inspection",
        "field_inspection",
    ]
    assert [d.action.facility_id for d in drafts if d.kind == "dispatch_truck"] == ["S2_FACILITY_B", "S2_FACILITY_A"]
    assert next(d for d in drafts if d.kind == "shut_in_wells").action.wells == ["S2_WELL_C1"]
    assert drafts[3].action.asset_id == "S5_PIPELINE_B"  # the leak is inspected first
    assert all(d.status == "pending" and d.decided_at is None and d.dispatched_at is None for d in drafts)
    assert len({d.id for d in drafts}) == len(drafts)
    assert {d.event_id for d in drafts} <= set(rec.incident_ids)
    # Every plan action in the recommendation has exactly one draft, with the same action.
    plan_actions = [a for p in rec.selected_plans for a in p.actions if a.type != "none"]
    assert [d.action for d in drafts if d.kind != "field_inspection"] == plan_actions
    assert [d.action for d in drafts if d.kind == "field_inspection"] == rec.inspections


def test_handoff_and_stored_run(client: TestClient) -> None:
    """The done event's handoff names the plan and the approval; GET /agent/runs/{id} returns the same run."""
    events = stream(client)
    done = payload(events, "done")
    handoff = done["handoff"]
    assert "$3,108" in handoff and "S5_PIPELINE_B" in handoff and "approval" in handoff
    assert "Not assessed: feasibility, production." in handoff
    run = AgentRunResult.model_validate(client.get(f"/api/{DS}/agent/runs/{done['run_id']}").json())
    assert run.handoff == handoff
    assert [d.model_dump() for d in run.drafts] == payload(events, "drafts")
    assert run.recommendation == Recommendation.model_validate_json(next(d for n, d in events if n == "recommendation"))
    assert [st.index for st in run.steps] == list(range(7))  # one final step per index
    assert run.steps == [final_steps(events)[i] for i in range(7)]


def test_unknown_run_or_dataset_is_404(client: TestClient) -> None:
    """Clear 404s for a run that doesn't exist and a dataset that doesn't exist."""
    assert client.get(f"/api/{DS}/agent/runs/run-nope").status_code == 404
    assert client.post("/api/no_such_dataset/agent/run").status_code == 404


def test_sentinel_never_answers_501_without_mock(client: TestClient) -> None:
    """Rule 8: with mock mode off the Sentinel flow is real (no fixture, no 501)."""
    assert client.get("/api/health").json()["mock"] is False
    events = stream(client)
    assert payload(events, "done")["run_id"].startswith("run-")


# ---------------------------------------------------------------------------
# Failures, no incidents, time machine
# ---------------------------------------------------------------------------


class Crashing(BaseAgent):
    """An environment agent that always crashes."""

    name = "environment"
    responsibility = "test"

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Fail on purpose."""
        raise RuntimeError("sensor feed unavailable")


async def collect(ds: Dataset, at: int | None = None, agents: tuple[BaseAgent, ...] | None = None):
    """Run the service directly (so tests can swap agents) and collect (event, data) pairs."""
    _, events = sentinel_service.start_run(ds, at, agents) if agents else sentinel_service.start_run(ds, at)
    return [(e["event"], e["data"]) async for e in events]


def test_crashing_agent_is_an_error_step_and_the_run_finishes() -> None:
    """§9.2: a crash is an 'error' step with the reason; the orchestrator still recommends (cost only)."""
    agents = (CostAgent(), Crashing(), FeasibilityAgent(), ProductionAgent())
    events = asyncio.run(collect(load_dataset(REPO_ROOT / "history" / DS), agents=agents))
    env = final_steps(events)[2]
    assert env.step == "environment" and env.status == "error" and env.grounded is None
    assert "sensor feed unavailable" in (env.assessment.error or "")
    rec = Recommendation.model_validate_json(next(d for n, d in events if n == "recommendation"))
    assert rec.environmental_risk is None and "environment" in rec.missing_assessments
    assert rec.estimated_cost_usd == pytest.approx(3108.33, abs=0.01)
    assert [n for n, _ in events][-1] == "done"


def test_run_crash_is_reported_never_faked(monkeypatch) -> None:
    """If the run itself crashes: an orchestrator error step, done with no handoff, no recommendation, no drafts."""

    async def broken(*args, **kwargs):
        raise ValueError("bad data")

    monkeypatch.setattr(sentinel_service, "orchestrate", broken)
    events = asyncio.run(collect(load_dataset(REPO_ROOT / "history" / DS)))
    names = [n for n, _ in events]
    assert "recommendation" not in names and "drafts" not in names and names[-1] == "done"
    err = steps_of(events)[-1]
    assert err.step == "orchestrator" and err.status == "error" and "bad data" in err.message
    done = payload(events, "done")
    assert done["handoff"] is None
    stored = sentinel_service.get_run(DS, done["run_id"])
    assert stored is not None and stored.recommendation is None and stored.drafts == []


def test_no_incidents_runs_no_agents(client: TestClient) -> None:
    """Nothing ongoing → triage says so, no agent steps, nothing to approve, an empty recommendation."""
    events = stream(client, "full_week", QUIET_MONDAY_01)
    final = final_steps(events)
    assert [st.step for st in final.values()] == ["triage", "orchestrator", "act"]
    assert final[0].title == "Triage: no ongoing incidents"
    assert final[6].status == "done"
    assert payload(events, "drafts") == []
    rec = payload(events, "recommendation")
    assert rec["incident_ids"] == [] and rec["selected_plans"] == [] and rec["inspections"] == []
    assert "No ongoing incidents" in payload(events, "done")["handoff"]


def test_time_machine_matches_the_alert_list(client: TestClient) -> None:
    """?at= analyses that moment: the run covers exactly the incidents /state shows as ongoing then."""
    events = stream(client, at=THURSDAY_1030)
    rec = payload(events, "recommendation")
    state = StateResponse.model_validate(client.get(f"/api/{DS}/state?at={THURSDAY_1030}").json())
    assert set(rec["incident_ids"]) == {a.event.id for a in state.alerts}
    assert "Thu 10:30" in final_steps(events)[0].message
