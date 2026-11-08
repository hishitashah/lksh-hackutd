"""Deterministic decision engine: the orchestrator's six steps (ARCHITECTURE.md §9.3-9.4).

Input: the shared incident context (incidents + the planner's options) and every agent's assessment.
Output: a ``Decision``: what to do for each pump failure, which inspections to send, and why. No LLM here; the
orchestrator asks Nemotron only to explain this decision afterwards.

The decision policy (written out, no weighted scoring, configurable where it's a policy choice)
----------------------------------------------------------------------------------------------
1. **Validate** each option and check which agents completed. Fallbacks (§9.2):
   * cost agent missing → still rank on the deterministic planner's costs; listed as missing;
   * environment agent missing → decide on cost only; environmental risk unknown; listed as missing;
   * feasibility / production missing (``not_implemented`` in the MVP) → listed as missing, and never assumed
     feasible (§7). The decision status is then ``partial``.
2. **Hard constraints** (an option failing one is never recommended):
   * never more trucks than the system has (shared fleet, §16);
   * every truck trip arrives before the repair ends (SPEC);
   * an option the environmental agent rates at or above ``env_veto_risk`` is excluded when a lower-risk option
     exists for the same incident (``config.py``, default ``high``).
   Plans where the tank still reaches shutdown stay allowed (sometimes nothing prevents it) but are flagged and
   never presented as preventing the shutdown.
3. **Cost:** the planner's shared-fleet selection (``select_joint``), restricted to options passing step 2.
4. **Environment:** each selected option's risk from the environmental agent; disagreements between the agents
   are recorded as trade-offs.
5. **Rank:** valid → cheapest total → fewer trucks (lexicographic).
6. **Select:** the chosen plans, inspections for every leak and worn pump (SPEC), reasons, trade-offs,
   alternatives and uncertainties.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from ..agents.base_agent import IncidentContext
from ..agents.environmental_agent import RISK_ORDER, highest
from ..config import get_settings
from ..models import AgentAssessment, AgentName, Event, PlanAction, RiskLevel
from ..planning.intervention_planner import Allowed, Candidate, SystemPlan, select_joint

AGENT_ORDER: tuple[AgentName, ...] = ("cost", "environment", "feasibility", "production")

MISSING_NOTES: dict[str, str] = {
    "cost": "The cost agent's analysis is missing; costs come straight from the deterministic planner.",
    "environment": "Environmental risk wasn't assessed, so this decision is based on cost only.",
    "feasibility": (
        "Feasibility wasn't independently assessed (agent not implemented): the planner only checks the truck count "
        "and that every trip arrives before the repair ends."
    ),
    "production": (
        "Production impact wasn't separately assessed (agent not implemented); lost oil and gas revenue is already "
        "part of each plan's cost."
    ),
}


@dataclass
class Decision:
    """What the orchestrator decided, and everything needed to explain it."""

    status: Literal["completed", "partial", "failed"]
    selection: dict[str, Candidate]  # event ID -> chosen option (pump failures)
    planner_cheapest: dict[str, Candidate]  # event ID -> the planner's unfiltered joint choice
    inspections: list[PlanAction]
    environmental_risk: RiskLevel | None  # highest rating of the selected response; None if not assessed
    agents_completed: list[AgentName]
    missing: list[AgentName]
    policy: str
    reasons: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    do_nothing_cost: float = 0.0

    @property
    def estimated_cost(self) -> float:
        """Total cost of the chosen plans, $."""
        return round(sum(c.cost_total for c in self.selection.values()), 2)

    @property
    def cheapest_cost(self) -> float:
        """Total cost of the planner's cheapest joint plans (before any veto), $."""
        return round(sum(c.cost_total for c in self.planner_cheapest.values()), 2)


def policy_text() -> str:
    """The decision policy in one line, as returned to the operator (§9.4 'transparent decision policy')."""
    veto = get_settings().env_veto_risk
    return (
        f"Valid plans only (never more trucks than a system has; every trip arrives before the repair ends; options "
        f"rated '{veto}' or worse by the environmental agent are excluded when a lower-risk option exists), then the "
        "lowest total cost per system with shared trucks, then fewer trucks. No weighted scoring. Plans where a tank "
        "still reaches shutdown are flagged, not presented as preventing it. Leaks and worn pumps get an inspection."
    )


def hhmm(unix: int) -> str:
    """Unix seconds -> 'HH:MM' UTC."""
    return datetime.fromtimestamp(unix, tz=UTC).strftime("%H:%M")


def check_fleet(sp: SystemPlan, selection: dict[str, Candidate]) -> None:
    """Hard constraint: a selection may never use more trucks than the system has (§16). Raises if it does."""
    used = sum(c.trucks_used for c in selection.values())
    if used > sp.free_trucks:
        raise ValueError(f"{sp.system_id}: selection uses {used} trucks but only {sp.free_trucks} are available")


def arrives_before_repair(sp: SystemPlan, event_id: str, c: Candidate) -> bool:
    """Hard constraint (SPEC): every truck trip must arrive before the repair ends."""
    if not c.trucks:
        return True
    sc = sp.options[event_id].scenario
    return all(t < sc.repair_end_unix for t in c.trucks.arrivals(sc.start_unix))


@dataclass
class Constraints:
    """The hard constraints of step 2, plus the environmental ratings they use."""

    env_risk: dict[tuple[str, str], RiskLevel]  # (event ID, option label) -> environmental agent's rating
    veto_rank: int  # index in RISK_ORDER from which an option is vetoed (config ``env_veto_risk``)

    def lowest_rated(self, event_id: str) -> int | None:
        """Lowest risk rank the environmental agent gave any option of this incident (None if it rated none)."""
        ranks = [RISK_ORDER.index(r) for (eid, _), r in self.env_risk.items() if eid == event_id]
        return min(ranks) if ranks else None

    def env_vetoed(self, event_id: str, c: Candidate) -> bool:
        """Rated at or above the veto level while a lower-risk option exists for the same incident."""
        risk = self.env_risk.get((event_id, c.label))
        low = self.lowest_rated(event_id)
        return risk is not None and RISK_ORDER.index(risk) >= self.veto_rank and low is not None and low < self.veto_rank

    def allowed_for(self, sp: SystemPlan) -> Allowed:
        """The filter ``select_joint`` applies for one system: arrives before the repair and not vetoed."""
        return lambda event_id, c: arrives_before_repair(sp, event_id, c) and not self.env_vetoed(event_id, c)


def decide(ctx: IncidentContext, assessments: dict[AgentName, AgentAssessment]) -> Decision:
    """Run the six decision steps on one context (see the module docstring for the policy).

    Args:
        ctx: the shared incident context the agents investigated (incidents + the planner's options).
        assessments: one assessment per agent that was run, keyed by agent name (any status).
    Returns:
        The ``Decision``. Costs are the planner's (deterministic, $); nothing here comes from an LLM.
    """
    cfg = get_settings()
    if not ctx.incidents:
        # Nothing to decide: no agent was needed, so nothing is missing, and no action is invented.
        return Decision(
            status="completed",
            selection={},
            planner_cheapest={},
            inspections=[],
            environmental_risk=None,
            agents_completed=[n for n in AGENT_ORDER if n in assessments and assessments[n].status == "completed"],
            missing=[],
            policy=policy_text(),
            reasons=["No ongoing incidents: nothing to recommend."],
        )

    # Step 1: which analyses exist. Anything not 'completed' is missing and said so (never faked, §9.2).
    completed = [n for n in AGENT_ORDER if n in assessments and assessments[n].status == "completed"]
    missing = [n for n in AGENT_ORDER if n not in completed]
    env = assessments["environment"] if "environment" in completed else None
    uncertainties: list[str] = [MISSING_NOTES[n] for n in missing]
    for n in completed:
        uncertainties += assessments[n].uncertainties
    env_risk: dict[tuple[str, str], RiskLevel] = {}
    if env is not None:
        env_risk = {(o.event_id, o.plan_label): o.risk_level for o in env.options if o.risk_level is not None}
    cons = Constraints(env_risk, RISK_ORDER.index(cfg.env_veto_risk))

    decision = Decision(
        status="partial" if missing else "completed",
        selection={},
        planner_cheapest={},
        inspections=[],
        environmental_risk=None,
        agents_completed=completed,
        missing=missing,
        policy=policy_text(),
        uncertainties=uncertainties,
    )

    # Steps 2-5 per system: hard constraints (the filter), cost + rank (select_joint), environment (the ratings).
    for sid in sorted(ctx.plans):
        sp = ctx.plans[sid]
        allowed = cons.allowed_for(sp)
        selection = select_joint(sp.options, sp.free_trucks, allowed)
        if selection is None:
            decision.status = "failed"
            decision.uncertainties.append(f"{sid}: no option passes the hard constraints; no plan can be recommended.")
            continue
        check_fleet(sp, selection)
        decision.selection.update(selection)
        decision.planner_cheapest.update(sp.selection)
        decision.do_nothing_cost = round(decision.do_nothing_cost + sp.do_nothing_cost, 2)
        total = round(sum(c.cost_total for c in selection.values()), 2)
        n, fleet = len(selection), sp.free_trucks
        decision.reasons.append(
            f"{sid}: cheapest valid joint plan for {n} pump failure{'s' if n > 1 else ''} sharing {fleet} "
            f"truck{'s' if fleet != 1 else ''}: ${total:,.0f} vs ${sp.do_nothing_cost:,.0f} if nothing is done."
        )
        used = sum(c.trucks_used for c in selection.values())
        for eid, c in selection.items():
            share = fleet - (used - c.trucks_used)  # trucks this failure could have without taking another's
            _explain_choice(decision, ctx, sp, eid, c, share, cons, allowed)

    # Environmental risk of the selected response: the highest rating among the chosen plans and inspections.
    if env is not None:
        rated = [env_risk[(eid, c.label)] for eid, c in decision.selection.items() if (eid, c.label) in env_risk]
        rated += [env_risk[k] for e in ctx.inspections() if (k := (e.id, inspect_label(e.asset_id))) in env_risk]
        decision.environmental_risk = highest(rated) if rated else env.risk_level

    # Step 6: inspections for every leak and worn pump (SPEC), most urgent first per the environmental agent.
    priority = env.suggested_interventions if env is not None else []

    def urgency(e: Event) -> tuple[int, int]:
        label = inspect_label(e.asset_id)
        return (priority.index(label) if label in priority else len(priority), e.onset_unix)

    for e in sorted(ctx.inspections(), key=urgency):
        decision.inspections.append(PlanAction(type="field_inspection", asset_id=e.asset_id))
        what = "a pipeline leak" if e.category == "pipeline_leak" else "a pump wearing out"
        decision.reasons.append(f"Inspect {e.asset_id}: {what} (field inspection, as SPEC requires).")
    if decision.environmental_risk is not None:
        decision.reasons.append(f"Environmental risk of this response: {decision.environmental_risk} (environmental agent).")
    decision.uncertainties = list(dict.fromkeys(decision.uncertainties))  # de-duplicate, keep order
    return decision


def inspect_label(asset_id: str) -> str:
    """The option label the environmental agent uses for an inspection (so ratings can be looked up)."""
    return f"Send inspector to {asset_id}"


def money(delta: float) -> str:
    """A signed dollar difference, e.g. '+$393' or '-$120'."""
    return f"{'+' if delta >= 0 else '-'}${abs(delta):,.0f}"


def _explain_choice(
    decision: Decision,
    ctx: IncidentContext,
    sp: SystemPlan,
    eid: str,
    c: Candidate,
    share: int,
    cons: Constraints,
    allowed: Allowed,
) -> None:
    """Add the reasons, trade-offs and alternatives for one selected option (steps 2, 4 and 6).

    Args:
        share: trucks this failure could use without taking one from another failure in its system.
    """
    opts = sp.options[eid]
    facility = opts.event.facility_id
    shutdown = ctx.dataset.network.defaults.tank_limits_pct.shutdown
    veto_level = RISK_ORDER[cons.veto_rank]

    # Step 2 flag: a plan that still trips is never presented as preventing the shutdown (§9.4).
    if c.outcome.tank_trips:
        safe = [a for a in opts.candidates if not a.outcome.tank_trips and a.trucks_used <= share and allowed(eid, a)]
        tail = (
            f"the cheapest option that prevents it, '{safe[0].label}', costs ${safe[0].cost_total - c.cost_total:,.0f} more"
            if safe
            else "no valid option prevents it"
        )
        decision.tradeoffs.append(
            f"{facility}: '{c.label}' limits the loss, but the tank still reaches the {shutdown:g}% shutdown "
            f"limit at {hhmm(c.outcome.trips_at_unix)}; {tail}."
        )
    else:
        decision.reasons.append(f"{facility}: {c.label} keeps the tank under its {shutdown:g}% limit until the repair.")

    # Step 4: a hard constraint changed the planner's cost choice -> say why and what it costs.
    planner_choice = sp.selection.get(eid)
    if planner_choice is not None and planner_choice.label != c.label:
        delta = money(c.cost_total - planner_choice.cost_total)
        if cons.env_vetoed(eid, planner_choice):
            rating = cons.env_risk[(eid, planner_choice.label)]
            decision.tradeoffs.append(
                f"{facility}: environmental veto: '{planner_choice.label}' was the cheapest, but the environmental "
                f"agent rated it '{rating}' (veto level '{veto_level}') and a lower-risk option exists; chose "
                f"'{c.label}' ({delta})."
            )
        else:
            # Not vetoed itself: the shared trucks moved because another failure's option was excluded (§16).
            decision.tradeoffs.append(
                f"{facility}: '{c.label}' instead of the planner's '{planner_choice.label}' ({delta}), because "
                f"{sp.system_id}'s trucks were re-split after another failure's option was excluded."
            )

    # Shared fleet (§16): a cheaper option for this failure alone needs trucks the other failures use.
    alone = opts.candidates[0]
    if alone.cost_total < c.cost_total and alone.trucks_used > share and allowed(eid, alone):
        fleet = sp.free_trucks
        decision.tradeoffs.append(
            f"{facility}: '{c.label}' instead of '{alone.label}' ({money(c.cost_total - alone.cost_total)}) "
            f"because {sp.system_id}'s {fleet} truck{'s' if fleet != 1 else ''} are needed at the other failures."
        )

    # A disagreement below the veto level is kept, but recorded (§9.4 step 4).
    chosen_risk = cons.env_risk.get((eid, c.label))
    low = cons.lowest_rated(eid)
    if chosen_risk is not None and low is not None and RISK_ORDER.index(chosen_risk) > low:
        decision.tradeoffs.append(
            f"{facility}: the cost and environment agents disagree: '{c.label}' is rated '{chosen_risk}', another "
            f"option is rated '{RISK_ORDER[low]}'; kept it because it is below the '{veto_level}' veto level."
        )

    # Alternatives considered (step 6), with the cost difference and why each wasn't chosen.
    shown = 0
    for alt in opts.candidates:
        if alt.label == c.label or alt is opts.do_nothing or shown >= 2:
            continue
        if cons.env_vetoed(eid, alt):
            note = "; excluded by the environmental veto"
        elif alt.trucks_used > share:
            note = "; needs a truck assigned to another failure"
        else:
            note = ""
        decision.alternatives.append(
            f"{facility}: {alt.label} ({money(alt.cost_total - c.cost_total)} vs the selected plan{note})"
        )
        shown += 1
    if opts.do_nothing.label != c.label:
        decision.alternatives.append(f"{facility}: {opts.do_nothing.label}")
