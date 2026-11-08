"""Central orchestrator tests (ARCHITECTURE.md §9, §16, §17 "Orchestrator tests", Phase 6).

Checks the decision policy end to end on the organizers' data:
* the Thursday 12:00 demo: the shared-truck joint plan, inspections by environmental priority, honest 'partial';
* the cheapest option isn't picked blindly (environmental veto), invalid options are never recommended, plans that
  still trip are flagged;
* one agent failing still gives a recommendation, with the gap stated;
* the explanation matches the decision (fallback text and grounded Nemotron text; a made-up number is rejected);
* no incidents → no invented actions;
* the automatic hand-off from monitoring triggers only on new / resolved incidents.
No live LLM: no key is set, and ``llm.set_llm_override`` stands in for Nemotron where needed.
"""

from __future__ import annotations

import asyncio

import pytest

from nightwatch import llm
from nightwatch.agents.base_agent import BaseAgent, IncidentContext, build_context
from nightwatch.agents.cost_agent import CostAgent
from nightwatch.agents.environmental_agent import EnvironmentalAgent
from nightwatch.agents.feasibility_agent import FeasibilityAgent
from nightwatch.agents.production_agent import ProductionAgent
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentAssessment, AgentName, OptionAssessment, RiskLevel
from nightwatch.monitoring.anomaly_detector import detect
from nightwatch.monitoring.incident_manager import IncidentManager
from nightwatch.monitoring.telemetry_processor import Dataset, load_dataset
from nightwatch.orchestration import orchestrator
from nightwatch.orchestration.decision_engine import MISSING_NOTES, arrives_before_repair, check_fleet, decide
from nightwatch.orchestration.orchestrator import OrchestratorEvent, orchestrate

HISTORY = REPO_ROOT / "history"
THURSDAY = HISTORY / "snapshot_2026-10-08T1200"
QUIET_MONDAY_01 = 1791162000  # full_week Mon 01:00: nothing ongoing
HOUR = 3600


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
def thursday() -> Dataset:
    """The Thursday 12:00 snapshot (the §16 demo: 3 S2 failures share 2 trucks)."""
    return load_dataset(THURSDAY)


def run(ds: Dataset, at: int | None = None, agents: tuple[BaseAgent, ...] | None = None) -> orchestrator.OrchestratorRun:
    """Run the orchestrator synchronously (default agents unless given)."""
    return asyncio.run(orchestrate(ds, at, agents=agents) if agents else orchestrate(ds, at))


class FixedAgent(BaseAgent):
    """An agent whose assessment is built by ``make(ctx)``: stands in for one specialist in a test."""

    responsibility = "test"

    def __init__(self, name: AgentName, make) -> None:
        self.name = name
        self.make = make

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Return the prepared assessment."""
        return self.make(ctx)


class Crashing(BaseAgent):
    """A specialist that always crashes."""

    responsibility = "test"

    def __init__(self, name: AgentName) -> None:
        self.name = name

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Fail on purpose."""
        raise RuntimeError("boom")


def env_rating(ratings: dict[tuple[str, str], RiskLevel]):
    """A fake environmental assessment that rates the given (event ID, option label) pairs."""

    def make(ctx: IncidentContext) -> AgentAssessment:
        return AgentAssessment(
            agent="environment",
            incident_ids=ctx.incident_ids(),
            status="completed",
            risk_level="moderate",
            findings=["Observed: test ratings."],
            evidence=[],
            metrics={},
            options=[
                OptionAssessment(plan_label=lbl, event_id=eid, risk_level=r, note="test")
                for (eid, lbl), r in ratings.items()
            ],
            uncertainties=[],
            suggested_interventions=[],
            explanation="test",
            llm_used=False,
        )

    return make


def s2_options(ds: Dataset):
    """The S2 joint plan's options at 'now', and the event ID of the failure at S2_FACILITY_B."""
    sp = build_context(ds).plans["S2"]
    b = next(eid for eid, o in sp.options.items() if o.event.facility_id == "S2_FACILITY_B")
    return sp, b


# ---------------------------------------------------------------------------
# The demo: Thursday 12:00 (§16)
# ---------------------------------------------------------------------------


def test_demo_joint_plan_and_inspections(thursday: Dataset) -> None:
    """3 S2 failures share 2 trucks: trucks to B and A, shut in S2_WELL_C1 at C; ≈ $3,108; S5 leak inspected first."""
    r = run(thursday)
    rec = r.recommendation
    by_facility = {p.event_ids[0].split("@")[0].replace("PUMP", "FACILITY"): p for p in rec.selected_plans}
    assert by_facility["S2_FACILITY_B"].actions[0].type == "dispatch_truck"
    assert by_facility["S2_FACILITY_A"].actions[0].type == "dispatch_truck"
    assert by_facility["S2_FACILITY_C"].actions[0].type == "shut_in_wells"
    assert by_facility["S2_FACILITY_C"].actions[0].wells == ["S2_WELL_C1"]
    assert sum(p.actions[0].trucks or 0 for p in rec.selected_plans) <= 2  # never more trucks than S2 has
    assert rec.estimated_cost_usd == pytest.approx(3108.33, abs=0.01)
    assert rec.estimated_cost_usd == rec.cheapest_cost_usd  # no constraint forced a dearer plan
    assert all(p.kind == "cheapest" and not p.tank_trips for p in rec.selected_plans)
    assert rec.inspections[0].asset_id == "S5_PIPELINE_B"  # the only observed loss of containment
    assert {i.type for i in rec.inspections} == {"field_inspection"}
    assert rec.environmental_risk == "moderate"
    assert rec.requires_operator_approval is True


def test_demo_is_partial_and_names_what_is_missing(thursday: Dataset) -> None:
    """Rule 5: feasibility and production didn't run, so the status is 'partial' and both are listed missing."""
    r = run(thursday)
    rec = r.recommendation
    assert rec.status == "partial"
    assert rec.agents_completed == ["cost", "environment"]
    assert rec.missing_assessments == ["feasibility", "production"]
    assert MISSING_NOTES["feasibility"] in rec.uncertainties and MISSING_NOTES["production"] in rec.uncertainties
    assert r.assessments["feasibility"].status == r.assessments["production"].status == "not_implemented"


def test_demo_records_the_shared_truck_tradeoff(thursday: Dataset) -> None:
    """§16: S2_FACILITY_C gets a shut-in instead of a cheaper truck because the 2 trucks go to B and A."""
    rec = run(thursday).recommendation
    assert any(t.startswith("S2_FACILITY_C:") and "needed at the other failures" in t for t in rec.tradeoffs)
    assert any("needs a truck assigned to another failure" in a for a in rec.alternatives)


def test_progress_events_in_order(thursday: Dataset) -> None:
    """The Phase 7 streaming hook: started → one agent_done per agent → decided → finished."""
    events: list[OrchestratorEvent] = []

    async def on_event(e: OrchestratorEvent) -> None:
        events.append(e)

    asyncio.run(orchestrate(thursday, on_event=on_event))
    kinds = [e.kind for e in events]
    assert kinds[0] == "started" and kinds[-2:] == ["decided", "finished"]
    assert sorted(e.assessment.agent for e in events if e.kind == "agent_done") == [
        "cost",
        "environment",
        "feasibility",
        "production",
    ]
    assert events[-1].recommendation is not None


# ---------------------------------------------------------------------------
# Hard constraints: the cheapest isn't picked blindly; invalid is never recommended
# ---------------------------------------------------------------------------


def test_environmental_veto_picks_a_lower_risk_option(thursday: Dataset) -> None:
    """§9.4: an option rated 'high' is excluded when a lower-risk one exists, and the extra cost is a trade-off."""
    sp, b = s2_options(thursday)
    cheapest_b = sp.selection[b]
    other = next(c for c in sp.options[b].candidates if c.label != cheapest_b.label and c.trucks_used == 0)
    ratings = {(b, cheapest_b.label): "high", (b, other.label): "low"}
    agents = (CostAgent(), FixedAgent("environment", env_rating(ratings)), FeasibilityAgent(), ProductionAgent())
    r = run(thursday, agents=agents)
    rec = r.recommendation
    chosen_b = r.decision.selection[b]
    assert chosen_b.label != cheapest_b.label  # the vetoed option is not recommended
    plan_b = next(p for p in rec.selected_plans if p.event_ids == [b])
    assert plan_b.kind == "alternative"
    assert rec.estimated_cost_usd > rec.cheapest_cost_usd  # it costs more, and says so
    assert any(t.startswith("S2_FACILITY_B: environmental veto") and "'high'" in t for t in rec.tradeoffs)
    # The other failures' choices may move with the shared trucks; that is stated, never called 'cheaper'.
    assert not any("was cheaper" in t for t in rec.tradeoffs)
    assert sum(c.trucks_used for c in r.decision.selection.values() if c is not None) <= sp.free_trucks


def test_disagreement_below_the_veto_is_kept_but_recorded(thursday: Dataset) -> None:
    """A 'moderate' rating on the cheapest option is below the 'high' veto: kept, with the disagreement noted."""
    sp, b = s2_options(thursday)
    cheapest_b = sp.selection[b]
    other = next(c for c in sp.options[b].candidates if c.label != cheapest_b.label)
    ratings = {(b, cheapest_b.label): "moderate", (b, other.label): "low"}
    ctx = build_context(thursday)
    a = asyncio.run(FixedAgent("environment", env_rating(ratings)).run(ctx))
    d = decide(ctx, {"environment": a})
    assert d.selection[b].label == cheapest_b.label
    assert any("disagree" in t for t in d.tradeoffs)


def test_invalid_selections_are_rejected(thursday: Dataset) -> None:
    """Never more trucks than the system has (§16), and every option the planner offers arrives before the repair."""
    sp, _ = s2_options(thursday)
    over = {eid: next(c for c in o.candidates if c.trucks_used == 2) for eid, o in sp.options.items()}
    with pytest.raises(ValueError, match="trucks"):
        check_fleet(sp, over)
    assert all(arrives_before_repair(sp, eid, c) for eid, o in sp.options.items() for c in o.candidates)


def test_plans_that_still_trip_are_flagged(thursday: Dataset) -> None:
    """§9.4 step 2: if only tripping plans are allowed, the choice is flagged, never shown as preventing shutdown."""
    sp, b = s2_options(thursday)
    opts = sp.options[b]
    ratings: dict[tuple[str, str], RiskLevel] = {(b, c.label): "high" for c in opts.candidates if not c.outcome.tank_trips}
    ratings[(b, opts.do_nothing.label)] = "low"
    ctx = build_context(thursday)
    a = asyncio.run(FixedAgent("environment", env_rating(ratings)).run(ctx))
    d = decide(ctx, {"environment": a})
    chosen = d.selection[b]
    assert chosen.outcome.tank_trips
    assert any(t.startswith("S2_FACILITY_B:") and "still reaches" in t and "no valid option" in t for t in d.tradeoffs)
    assert not any(r.startswith("S2_FACILITY_B:") and "keeps the tank under" in r for r in d.reasons)


# ---------------------------------------------------------------------------
# One agent failing (§9.2 fallbacks)
# ---------------------------------------------------------------------------


def test_environment_crash_still_recommends_on_cost(thursday: Dataset) -> None:
    """Environment missing → the cost-based plan, risk unknown, the gap stated."""
    agents = (CostAgent(), Crashing("environment"), FeasibilityAgent(), ProductionAgent())
    r = run(thursday, agents=agents)
    rec = r.recommendation
    assert r.assessments["environment"].status == "error"
    assert rec.estimated_cost_usd == pytest.approx(3108.33, abs=0.01)
    assert rec.environmental_risk is None
    assert "environment" in rec.missing_assessments and rec.status == "partial"
    assert MISSING_NOTES["environment"] in rec.uncertainties
    assert [i.asset_id for i in rec.inspections]  # leaks and worn pumps are still inspected (SPEC)


def test_cost_crash_still_uses_the_planner_costs(thursday: Dataset) -> None:
    """Cost agent missing → still ranked on the deterministic planner's costs; cost listed missing."""
    agents = (Crashing("cost"), EnvironmentalAgent(), FeasibilityAgent(), ProductionAgent())
    rec = run(thursday, agents=agents).recommendation
    assert rec.estimated_cost_usd == pytest.approx(3108.33, abs=0.01)
    assert "cost" in rec.missing_assessments and "cost" not in rec.agents_completed
    assert MISSING_NOTES["cost"] in rec.uncertainties


# ---------------------------------------------------------------------------
# The explanation matches the decision (§9.5)
# ---------------------------------------------------------------------------


def test_fallback_explanation_names_the_decision(thursday: Dataset) -> None:
    """Without Nemotron: llm_used False, and the text names every selected plan, the total and the approval."""
    rec = run(thursday).recommendation
    assert rec.llm_used is False
    for p in rec.selected_plans:
        assert p.label in rec.explanation
    assert "$3,108" in rec.explanation and "S5_PIPELINE_B" in rec.explanation
    assert "approve" in rec.explanation


def test_grounded_nemotron_text_is_used(thursday: Dataset) -> None:
    """A Nemotron reply whose numbers all come from the decision is used, with llm_used True."""
    reply = (
        "Send one truck each to S2_FACILITY_B and S2_FACILITY_A and shut in S2_WELL_C1, for $3,108 instead of "
        "$12,206 if nothing is done; inspect S5_PIPELINE_B first. Please approve before anything is dispatched."
    )
    llm.set_llm_override(lambda system, user: reply if "central orchestrator" in system else "")
    rec = run(thursday).recommendation
    assert rec.llm_used is True and rec.explanation == reply


def test_made_up_number_is_rejected(thursday: Dataset) -> None:
    """A Nemotron reply with a number not in the decision is rejected; the deterministic text is used."""
    llm.set_llm_override(lambda system, user: "This plan saves $9,999." if "central orchestrator" in system else "")
    r = run(thursday)
    assert r.recommendation.llm_used is False
    assert "$9,999" not in r.recommendation.explanation
    assert r.explanation_note and "rejected" in r.explanation_note


# ---------------------------------------------------------------------------
# No incidents, and the automatic hand-off from monitoring (§3.3 step 10)
# ---------------------------------------------------------------------------


def test_no_incidents_invents_nothing() -> None:
    """Nothing ongoing → an empty recommendation, no agents run, no Nemotron call, nothing missing."""
    ds = load_dataset(HISTORY / "full_week")
    agents = tuple(Crashing(n) for n in ("cost", "environment", "feasibility", "production"))  # must not be called
    r = run(ds, QUIET_MONDAY_01, agents=agents)
    rec = r.recommendation
    assert rec.incident_ids == [] and rec.selected_plans == [] and rec.inspections == []
    assert rec.status == "completed" and rec.missing_assessments == [] and r.assessments == {}
    assert rec.estimated_cost_usd == 0 and rec.llm_used is False
    assert rec.requires_operator_approval is True


def test_monitoring_hands_off_only_on_new_or_resolved(thursday: Dataset, monkeypatch) -> None:
    """Replay Thu 08:30 → 10:30: a run starts exactly when an incident is new (09:00, 09:30, 10:00) or resolved
    (S4_PUMP_C at 09:10), at most once per tick, and never on quiet ticks."""
    calls: list[int] = []

    async def fake_orchestrate(ds, at, on_event=None):
        calls.append(at)
        return f"run@{at}"

    monkeypatch.setattr(orchestrator, "orchestrate", fake_orchestrate)
    start, end = thursday.now_unix - int(3.5 * HOUR), thursday.now_unix - int(1.5 * HOUR)
    manager = IncidentManager()
    manager.process(detect(thursday, start), start)  # prime: incidents already open at 08:30 aren't 'new' later
    returned: dict[int, object] = {}
    for t in thursday.replay(start=start + thursday.step_s, until=end):
        transitions = manager.process(detect(thursday, t), t)
        out = asyncio.run(orchestrator.on_incident_transitions(thursday, transitions, t))
        if out is not None:
            returned[t] = out
            again = asyncio.run(orchestrator.on_incident_transitions(thursday, transitions, t))
            assert again == out  # cached: the same tick never runs the agents twice
    nine = thursday.now_unix - 3 * HOUR
    assert calls == [nine, nine + 600, nine + 1800, nine + 3600]
    assert list(returned) == calls
