"""Central orchestrator: the final decision-maker of the multi-agent workflow (ARCHITECTURE.md §9, Phase 6).

Where this sits in the app
--------------------------
Stage 1 (monitoring) finds incidents → this module runs Stage 2 (the specialist agents, in parallel) and Stage 3
(the decision) → the operator approves (Phase 8) → the field crew is dispatched. There is no other model after the
orchestrator (§9).

What one run does (§9.1)
------------------------
1. ``build_context``: the incidents at that moment and the planner's options (deterministic, shared by every agent).
2. ``run_agents``: cost, environment, feasibility and production at the same time, each with a timeout.
3. ``decide``: the written decision policy (hard constraints → cheapest valid → fewer trucks), no LLM.
4. Nemotron explains the decision for the operator, using only the decision's numbers (grounding check, §9.5).
   If Nemotron is unavailable the deterministic text is used and ``llm_used`` is False.
5. The ``Recommendation``: always ``requires_operator_approval=True``; nothing is dispatched from here.

Progress events (``on_event``) are emitted as the run goes: started, each agent finishing, decided, finished.
Phase 7 streams them to the Sentinel panel without changing this module.

Automatic hand-off (Stage 1 §3.3 step 10)
-----------------------------------------
``on_incident_transitions`` is called by the monitoring loop after each tick. A ``new`` or ``resolved`` incident
starts a run on its own (§3: "rather than waiting for a user"). ``updated`` alone doesn't: a failing tank's level
changes every tick, and re-running every agent (and Nemotron) every 5 minutes would only add cost; the live numbers
still refresh through ``/state`` and ``/plan``.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..agents.base_agent import BaseAgent, IncidentContext, build_context
from ..llm import explain
from ..models import AgentAssessment, AgentName, Event, Plan, Recommendation
from ..monitoring.incident_manager import Transition
from ..monitoring.telemetry_processor import Dataset
from .agent_runner import AGENTS, run_agents
from .decision_engine import Decision, decide

# Lifecycle changes that hand an incident to the problem-solving workflow (§3.3 step 10).
TRIGGERS = ("new", "resolved")


@dataclass(frozen=True)
class OrchestratorEvent:
    """One progress event of a run, for streaming (Phase 7).

    ``kind``: ``started`` (context built, agents launched), ``agent_done`` (one agent finished, with its assessment),
    ``decided`` (the deterministic decision is made), ``finished`` (the recommendation with its explanation).
    """

    kind: Literal["started", "agent_done", "decided", "finished"]
    elapsed_s: float  # seconds since the run started
    context: IncidentContext | None = None
    assessment: AgentAssessment | None = None
    decision: Decision | None = None
    recommendation: Recommendation | None = None


OnEvent = Callable[[OrchestratorEvent], Awaitable[None]]


@dataclass
class OrchestratorRun:
    """Everything one orchestrator run produced: inputs, every agent's assessment, the decision, the result."""

    context: IncidentContext
    assessments: dict[AgentName, AgentAssessment]
    decision: Decision
    recommendation: Recommendation
    elapsed_s: float  # wall time of the whole run, seconds
    explanation_note: str | None  # why the deterministic explanation was used, if it was


async def orchestrate(
    ds: Dataset,
    at: int | None = None,
    agents: tuple[BaseAgent, ...] = AGENTS,
    on_event: OnEvent | None = None,
    timeout_s: float | None = None,
) -> OrchestratorRun:
    """Run the full workflow for every ongoing incident at ``at`` and return the recommendation.

    Args:
        ds: the loaded dataset.
        at: the moment to decide for, unix seconds (None = the dataset's 'now').
        agents: the specialists to run (default: all four; tests pass fakes).
        on_event: optional async callback for progress events (see ``OrchestratorEvent``).
        timeout_s: per-agent time limit, seconds (default ``agent_timeout_s`` in ``config.py``).
    """
    t0 = time.perf_counter()

    def elapsed() -> float:
        return round(time.perf_counter() - t0, 3)

    async def emit(**kw: Any) -> None:
        if on_event is not None:
            await on_event(OrchestratorEvent(elapsed_s=elapsed(), **kw))

    ctx = build_context(ds, at)
    await emit(kind="started", context=ctx)

    async def agent_done(a: AgentAssessment) -> None:
        await emit(kind="agent_done", assessment=a)

    # No incidents: nothing to investigate, so no agent (and no Nemotron call) is needed.
    assessments = await run_agents(ctx, agents, timeout_s, agent_done) if ctx.incidents else {}
    decision = decide(ctx, assessments)
    await emit(kind="decided", decision=decision)

    selected = selected_plans(ctx, decision)
    fallback = fallback_explanation(ctx, decision, selected)
    if ctx.incidents:
        text = await explain(
            "central orchestrator",
            "Explain to the operator what is recommended and why: the selected plans, their total cost compared with "
            "doing nothing, the inspections, any trade-off, and which analyses are missing. The operator must approve "
            "before anything is dispatched.",
            explanation_facts(decision, selected, assessments),
            fallback,
        )
        explanation, llm_used, note = text.text, text.llm_used, text.note
    else:
        explanation, llm_used, note = fallback, False, "No incidents: nothing for Nemotron to explain"

    rec = Recommendation(
        incident_ids=ctx.incident_ids(),
        status=decision.status,
        selected_plans=selected,
        inspections=decision.inspections,
        estimated_cost_usd=decision.estimated_cost,
        cheapest_cost_usd=decision.cheapest_cost,
        environmental_risk=decision.environmental_risk,
        decision_policy=decision.policy,
        reasons=decision.reasons,
        tradeoffs=decision.tradeoffs,
        alternatives=decision.alternatives,
        uncertainties=decision.uncertainties,
        agents_completed=decision.agents_completed,
        missing_assessments=decision.missing,
        explanation=explanation,
        llm_used=llm_used,
        requires_operator_approval=True,  # rule 6: nothing is dispatched on an AI recommendation alone
    )
    await emit(kind="finished", decision=decision, recommendation=rec)
    return OrchestratorRun(ctx, assessments, decision, rec, elapsed(), note)


def urgency_order(events: list[Event]) -> list[Event]:
    """Pump failures most urgent first: fewest hours to shutdown (p50), then highest lost revenue (SPEC alerts)."""
    return sorted(
        events,
        key=lambda e: (
            e.hours_to_limit.p50 if e.hours_to_limit is not None else float("inf"),
            -(e.lost_revenue_per_hour or 0.0),
        ),
    )


def selected_plans(ctx: IncidentContext, decision: Decision) -> list[Plan]:
    """The chosen plan per pump failure as contract ``Plan``s, most urgent first.

    ``kind`` is ``cheapest`` when the planner's cheapest joint choice was kept, ``alternative`` when a hard
    constraint (e.g. the environmental veto) changed it.
    """
    plans: list[Plan] = []
    for e in urgency_order([e for e in ctx.failures() if e.id in decision.selection]):
        c = decision.selection[e.id]
        cheapest = decision.planner_cheapest.get(e.id)
        kind = "cheapest" if cheapest is not None and cheapest.label == c.label else "alternative"
        plans.append(c.to_plan(kind, [e.id]))
    return plans


def explanation_facts(
    decision: Decision, selected: list[Plan], assessments: dict[AgentName, AgentAssessment]
) -> dict[str, Any]:
    """The only facts Nemotron may use for the operator explanation (every number comes from the decision)."""
    return {
        "status": decision.status,
        "selected_plans": [
            {"incident": p.event_ids[0], "plan": p.label, "cost_usd": p.cost_total, "tank_still_trips": p.tank_trips}
            for p in selected
        ],
        "estimated_cost_usd": decision.estimated_cost,
        "cheapest_cost_usd": decision.cheapest_cost,
        "do_nothing_cost_usd": decision.do_nothing_cost,
        "inspections": [i.asset_id for i in decision.inspections],
        "environmental_risk": decision.environmental_risk,
        "tradeoffs": decision.tradeoffs,
        "agents_completed": decision.agents_completed,
        "missing_analyses": decision.missing,
        "agent_headlines": {n: a.assessment for n, a in assessments.items() if a.status == "completed" and a.assessment},
        "decision_policy": decision.policy,
        "requires_operator_approval": True,
    }


def fallback_explanation(ctx: IncidentContext, decision: Decision, selected: list[Plan]) -> str:
    """Deterministic operator explanation, used when Nemotron is unavailable (and as its reference)."""
    if not ctx.incidents:
        return "No ongoing incidents, so there is nothing to recommend right now."
    parts: list[str] = []
    if selected:
        plans = "; ".join(f"{_facility(ctx, p.event_ids[0])}: {p.label}" for p in selected)
        parts.append(
            f"Recommended: {plans}. Together ${decision.estimated_cost:,.0f}, versus ${decision.do_nothing_cost:,.0f} "
            "if nothing is done."
        )
        if decision.estimated_cost > decision.cheapest_cost:
            parts.append(
                f"That is ${decision.estimated_cost - decision.cheapest_cost:,.0f} more than the cheapest plans, "
                "because of a hard constraint (see trade-offs)."
            )
    if decision.inspections:
        parts.append(f"Send inspectors to {', '.join(i.asset_id for i in decision.inspections)} (most urgent first).")
    if decision.environmental_risk is not None:
        parts.append(f"Environmental risk: {decision.environmental_risk}.")
    if decision.missing:
        parts.append(f"Not assessed: {', '.join(decision.missing)}.")
    parts.append("Nothing is dispatched until you approve.")
    return " ".join(parts)


def _facility(ctx: IncidentContext, event_id: str) -> str:
    """Facility ID of an incident in the context (the event ID if it isn't found)."""
    return next((e.facility_id for e in ctx.incidents if e.id == event_id), event_id)


# ---------------------------------------------------------------------------
# Automatic hand-off from monitoring (Stage 1 §3.3 step 10)
# ---------------------------------------------------------------------------

# Finished runs per dataset and time, so the same moment never runs the agents twice. Weak keys: a reloaded
# dataset drops its old runs.
_runs: weakref.WeakKeyDictionary[Dataset, dict[int, OrchestratorRun]] = weakref.WeakKeyDictionary()


def should_trigger(transitions: list[Transition]) -> bool:
    """True if this tick has an incident that is new or just resolved (the moments the decision can change)."""
    return any(t.lifecycle in TRIGGERS for t in transitions)


async def on_incident_transitions(
    ds: Dataset, transitions: list[Transition], at: int, on_event: OnEvent | None = None
) -> OrchestratorRun | None:
    """Hand a monitoring tick to the workflow: run the orchestrator if an incident is new or resolved.

    At most one run per tick, cached per (dataset, time). Returns the run, or None if this tick didn't trigger one.
    The monitoring replay loop (Phase 7) calls this after ``IncidentManager.process``.
    """
    if not should_trigger(transitions):
        return None
    per_ds = _runs.setdefault(ds, {})
    if at not in per_ds:
        per_ds[at] = await orchestrate(ds, at, on_event=on_event)
    return per_ds[at]


def cached_run(ds: Dataset, at: int) -> OrchestratorRun | None:
    """The run the automatic hand-off already made for this moment, if any (Phase 7 reuses it)."""
    return _runs.get(ds, {}).get(at)
