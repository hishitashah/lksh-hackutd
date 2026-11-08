"""Agent framework tests: the future-agent stubs and the concurrent runner (ARCHITECTURE.md §4, §7, §8, §9.2, Phase 5).

Checks that planned agents are never presented as done, that the agents really run at the same time, and that a
slow or crashing agent becomes a visible ``timeout`` / ``error`` assessment instead of disappearing or blocking the
run. No live LLM: no NVIDIA key is set, and the fake agents never call Nemotron.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from nightwatch import llm
from nightwatch.agents.base_agent import BaseAgent, IncidentContext, build_context
from nightwatch.agents.feasibility_agent import FeasibilityAgent
from nightwatch.agents.production_agent import ProductionAgent
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentAssessment, AgentName
from nightwatch.monitoring.telemetry_processor import load_dataset
from nightwatch.orchestration.agent_runner import AGENTS, run_agents

THURSDAY = REPO_ROOT / "history" / "snapshot_2026-10-08T1200"


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """No NVIDIA key and no override: nothing calls the real API."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    get_settings.cache_clear()
    llm.set_llm_override(None)
    yield
    llm.set_llm_override(None)
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def ctx() -> IncidentContext:
    """The Thursday 12:00 demo context (3 S2 pump failures, the S5 leak, worn pumps)."""
    return build_context(load_dataset(THURSDAY))


def completed(agent: AgentName, ctx: IncidentContext) -> AgentAssessment:
    """A minimal completed assessment for fake agents."""
    return AgentAssessment(
        agent=agent,
        incident_ids=ctx.incident_ids(),
        status="completed",
        findings=["fake"],
        evidence=[],
        metrics={},
        options=[],
        uncertainties=[],
        suggested_interventions=[],
        explanation="fake",
        llm_used=False,
    )


class SleepyAgent(BaseAgent):
    """Takes ``delay_s`` seconds (without blocking the event loop), then completes."""

    responsibility = "test"

    def __init__(self, name: AgentName, delay_s: float) -> None:
        self.name = name
        self.delay_s = delay_s

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Sleep, then return a completed assessment."""
        await asyncio.sleep(self.delay_s)
        return completed(self.name, ctx)


class CrashingAgent(BaseAgent):
    """Always raises, to check that a crash becomes an ``error`` assessment."""

    responsibility = "test"

    def __init__(self, name: AgentName) -> None:
        self.name = name

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Fail on purpose."""
        raise RuntimeError("sensor feed unavailable")


def test_registry_has_the_four_specialists() -> None:
    """§2 Stage 2: cost, environment, feasibility, production (one entry each)."""
    assert [a.name for a in AGENTS] == ["cost", "environment", "feasibility", "production"]


@pytest.mark.parametrize("agent", [FeasibilityAgent(), ProductionAgent()])
def test_future_agents_are_not_implemented_never_completed(agent: BaseAgent, ctx: IncidentContext) -> None:
    """§7/§8 + rule 5: planned agents say so plainly; never 'completed', never an LLM claim, no findings."""
    a = asyncio.run(agent.run(ctx))
    assert a.agent == agent.name
    assert a.status == "not_implemented"
    assert a.llm_used is False
    assert a.findings == [] and a.options == [] and a.suggested_interventions == []
    assert a.incident_ids == ctx.incident_ids()
    assert any("not implemented" in u for u in a.uncertainties)


def test_agents_run_concurrently(ctx: IncidentContext) -> None:
    """Two 0.3 s agents finish together in well under the 0.6 s a sequential run would take."""
    agents = (SleepyAgent("feasibility", 0.3), SleepyAgent("production", 0.3))
    t0 = time.perf_counter()
    results = asyncio.run(run_agents(ctx, agents))
    assert time.perf_counter() - t0 < 0.5
    assert {n: a.status for n, a in results.items()} == {"feasibility": "completed", "production": "completed"}


def test_slow_agent_times_out_and_the_run_still_finishes(ctx: IncidentContext) -> None:
    """§9.2: an agent past its time limit becomes 'timeout' with the reason; the others are unaffected."""
    agents = (SleepyAgent("feasibility", 5.0), SleepyAgent("production", 0.0))
    t0 = time.perf_counter()
    results = asyncio.run(run_agents(ctx, agents, timeout_s=0.2))
    assert time.perf_counter() - t0 < 1.0
    slow = results["feasibility"]
    assert slow.status == "timeout" and slow.llm_used is False
    assert slow.error and "0.2" in slow.error
    assert results["production"].status == "completed"


def test_crashing_agent_becomes_a_visible_error(ctx: IncidentContext) -> None:
    """§17 'errors are surfaced': a crash is an 'error' assessment with the reason, not a missing key."""
    results = asyncio.run(run_agents(ctx, (CrashingAgent("environment"), SleepyAgent("cost", 0.0))))
    err = results["environment"]
    assert err.status == "error"
    assert "sensor feed unavailable" in (err.error or "")
    assert any("missing" in u for u in err.uncertainties)
    assert results["cost"].status == "completed"


def test_on_done_fires_once_per_agent(ctx: IncidentContext) -> None:
    """The streaming hook (Phase 7) sees every agent exactly once, including failures."""
    seen: list[str] = []

    async def on_done(a: AgentAssessment) -> None:
        seen.append(a.agent)

    agents = (SleepyAgent("cost", 0.05), CrashingAgent("environment"), FeasibilityAgent(), ProductionAgent())
    results = asyncio.run(run_agents(ctx, agents, on_done=on_done))
    assert sorted(seen) == sorted(results) == ["cost", "environment", "feasibility", "production"]
