"""Cost-Effectiveness Agent + Nemotron explanation tests (ARCHITECTURE.md §5, §9.5, §17 "Agent tests").

No live LLM: ``llm.set_llm_override`` stands in for Nemotron so the tests are deterministic and need no key.
"""

from __future__ import annotations

import asyncio

import pytest

from nightwatch import llm
from nightwatch.agents.base_agent import BaseAgent, IncidentContext, build_context
from nightwatch.agents.cost_agent import CostAgent
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentAssessment
from nightwatch.monitoring.telemetry_processor import load_dataset

THURSDAY = REPO_ROOT / "history" / "snapshot_2026-10-08T1200"


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Every test starts with no NVIDIA key and no override, so nothing calls the real API."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    get_settings.cache_clear()
    llm.set_llm_override(None)
    yield
    llm.set_llm_override(None)
    get_settings.cache_clear()


def run_cost_agent(folder=THURSDAY, at=None) -> AgentAssessment:
    """Run the cost agent on a dataset at a time (None = 'now')."""
    return asyncio.run(CostAgent().run(build_context(load_dataset(folder), at)))


def test_returns_valid_structured_costs() -> None:
    """§17: a completed, typed assessment whose costs are exactly the planner's joint plan."""
    a = run_cost_agent()
    ctx = build_context(load_dataset(THURSDAY))
    sp = ctx.plans["S2"]
    assert a.status == "completed" and a.agent == "cost"
    assert a.metrics["joint_plan_cost_usd"] == sp.total_cost
    assert a.metrics["do_nothing_cost_usd"] == sp.do_nothing_cost
    assert a.metrics["trucks_used"] <= a.metrics["trucks_available"]
    assert set(a.incident_ids) == set(sp.selection)
    assert a.suggested_interventions == [c.label for c in sp.selection.values()]


def test_every_option_cost_comes_from_the_planner() -> None:
    """§5.5: no invented figures. Every option's cost equals a cost the planner computed."""
    a = run_cost_agent()
    sp = build_context(load_dataset(THURSDAY)).plans["S2"]
    planner_costs = {c.cost_total for o in sp.options.values() for c in o.candidates}
    assert a.options and all(o.cost_usd in planner_costs for o in a.options)


def test_explains_the_shared_truck_trade_off() -> None:
    """The agent explains why a cheaper-alone option wasn't chosen (§5.5 'why one option costs less')."""
    a = run_cost_agent()
    assert any("needs a truck the joint plan gives to another failure" in o.note for o in a.options)
    assert "2 trucks" in a.explanation and "$3,108" in a.explanation


def test_no_failures_is_handled_explicitly() -> None:
    """At a moment with no pump failures, the agent says so instead of inventing options."""
    a = run_cost_agent(at=1791158400 + 3600)  # Mon 01:00, nothing has failed yet
    assert a.status == "completed" and a.options == [] and a.metrics["pump_failures"] == 0


def test_inspections_are_not_costed() -> None:
    """Leaks and worn pumps get inspections; the dataset has no inspection cost, so it's an uncertainty."""
    a = run_cost_agent()
    assert any("have no cost in the dataset" in u for u in a.uncertainties)


def test_without_nemotron_the_fallback_is_marked() -> None:
    """§9.5: with no key, the deterministic explanation is used and llm_used is False."""
    assert run_cost_agent().llm_used is False


def test_grounded_nemotron_text_is_used() -> None:
    """When Nemotron's text only uses numbers from the facts, it becomes the explanation (llm_used=True)."""
    llm.set_llm_override(lambda system, user: "Two trucks cover the two most urgent tanks; total $3,108.")
    a = run_cost_agent()
    assert a.llm_used is True and a.explanation.startswith("Two trucks")


def test_ungrounded_nemotron_text_is_rejected() -> None:
    """If Nemotron writes a number that isn't in the facts, its text is rejected and the fallback is used."""
    llm.set_llm_override(lambda system, user: "This plan saves $99,999.")
    a = run_cost_agent()
    assert a.llm_used is False and "$99,999" not in a.explanation


def test_nemotron_errors_fall_back_honestly() -> None:
    """A failing Nemotron call never breaks the agent or pretends AI ran."""

    def boom(system: str, user: str) -> str:
        raise TimeoutError("slow network")

    llm.set_llm_override(boom)
    a = run_cost_agent()
    assert a.status == "completed" and a.llm_used is False


def test_agent_errors_are_surfaced() -> None:
    """§17: an agent that crashes reports status 'error' with the reason, instead of disappearing."""

    class Broken(BaseAgent):
        name = "cost"
        responsibility = "test"

        async def assess(self, ctx: IncidentContext) -> AgentAssessment:
            raise ValueError("bad input")

    a = asyncio.run(Broken().run(build_context(load_dataset(THURSDAY))))
    assert a.status == "error" and "bad input" in (a.error or "")


def test_grounding_check() -> None:
    """Numbers in text must match the facts (allowing rounding and small counts)."""
    facts = {"cost": 3108.33, "items": ["shutdown in 1.42 h"]}
    assert llm.is_grounded("Total $3,108 with 2 trucks; shutdown in 1.42 h.", facts)
    assert not llm.is_grounded("Total $3,500.", facts)
