"""Cost-Effectiveness Agent (ARCHITECTURE.md §5, Phase 3).

Responsibility
--------------
For each ongoing pump failure: what does each way of handling it cost (send trucks, shut in wells early, do
nothing), which options are financially attractive, and why one costs less than another (§5.1, §5.5).

Where its numbers come from
---------------------------
Only from the deterministic planner (``planning/intervention_planner.py``), which uses the dataset's real truck
capacity, price, response and round-trip times, oil and gas prices, well rates and repair hours (§5.2). When several
failures in one system share a truck fleet, it reports the planner's **joint** plan, so no truck is counted twice
(§16). This agent never computes a cost itself; it reads, compares and explains. Nemotron may word the explanation
(``nightwatch/llm.py``), and only with numbers taken from these results.

Leaks and worn pumps get a field inspection (SPEC). The dataset has no inspection cost, so this agent lists them as
uncertainties instead of inventing a price.
"""

from __future__ import annotations

from ..llm import explain
from ..models import AgentAssessment, Event, OptionAssessment
from ..planning.cost_calculator import shut_in_cost_per_bbl, truck_cost_per_bbl
from ..planning.intervention_planner import Candidate, SystemPlan
from .base_agent import BaseAgent, IncidentContext

MAX_ALTERNATIVES = 2  # per failure, besides the selected plan and doing nothing


class CostAgent(BaseAgent):
    """Compares the cost of every intervention option, using the planner's deterministic results only."""

    name = "cost"
    responsibility = "Financial consequences of each intervention option (ARCHITECTURE.md §5)."

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Build the structured cost assessment for the context's pump failures."""
        failures = ctx.failures()
        ops = ctx.dataset.ops
        uncertainties = [
            "Truck response and round-trip times are planning values from ops.json, not live truck positions.",
            "The fill rate uses each producing well's baseline water rate.",
            "Wells shut in together share one start time; separate start times per well were not searched.",
            f"Repairs are assumed to end exactly {ops.expected_repair_hours['pump_failure']:g} h after onset (ops.json).",
        ]
        if ctx.inspections():
            assets = ", ".join(e.asset_id for e in ctx.inspections())
            uncertainties.append(f"Field inspections ({assets}) have no cost in the dataset, so they aren't costed.")
        if not failures:
            return AgentAssessment(
                agent="cost",
                incident_ids=ctx.incident_ids(),
                status="completed",
                assessment="No pump failures: no intervention costs to compare",
                findings=["No ongoing pump failure needs trucks or shut-ins at this time."],
                evidence=[],
                metrics={"pump_failures": 0.0},
                options=[],
                uncertainties=uncertainties,
                suggested_interventions=[],
                explanation="There are no ongoing pump failures, so there are no intervention costs to compare.",
                llm_used=False,
            )

        findings: list[str] = []
        evidence: list[str] = []
        options: list[OptionAssessment] = []
        suggested: list[str] = []
        trucking = ops.emergency_trucking
        truck_bbl_cost = truck_cost_per_bbl(trucking.cost_per_trip, trucking.truck_capacity_bbl)
        evidence.append(
            f"Truck trip: ${trucking.cost_per_trip:,.0f} for {trucking.truck_capacity_bbl:g} bbl "
            f"(${truck_bbl_cost:.2f}/bbl), ops.json"
        )

        plans: list[SystemPlan] = [ctx.plans[sid] for sid in sorted({e.system_id for e in failures}) if sid in ctx.plans]
        for sp in plans:
            n = len(sp.selection)
            findings.append(
                f"{sp.system_id}: {n} pump failure{'s' if n > 1 else ''} share {sp.free_trucks} truck"
                f"{'s' if sp.free_trucks != 1 else ''}; the joint plan costs ${sp.total_cost:,.0f} vs "
                f"${sp.do_nothing_cost:,.0f} if nothing is done (uses {sp.trucks_used} of {sp.free_trucks} trucks)."
            )
            for eid, chosen in sp.selection.items():
                event = next(e for e in failures if e.id == eid)
                opts = sp.options[eid]
                findings.append(f"{event.facility_id}: {chosen.label}" + _why(chosen, opts.do_nothing))
                evidence.extend(_evidence(ctx, event, chosen))
                options.append(_option(chosen, eid, "Selected in the joint plan for the shared fleet."))
                share = sp.free_trucks - (sp.trucks_used - chosen.trucks_used)
                shown = 0
                for c in opts.candidates:
                    if c.label == chosen.label or c is opts.do_nothing or shown >= MAX_ALTERNATIVES:
                        continue
                    note = (
                        "Cheaper on its own, but needs a truck the joint plan gives to another failure."
                        if c.trucks_used > share and c.cost_total < chosen.cost_total
                        else f"Costs ${c.cost_total - chosen.cost_total:,.0f} more than the selected plan."
                    )
                    options.append(_option(c, eid, note))
                    shown += 1
                options.append(_option(opts.do_nothing, eid, "Doing nothing: " + _do_nothing_note(opts.do_nothing)))
                suggested.append(chosen.label)

        total = round(sum(sp.total_cost for sp in plans), 2)
        nothing = round(sum(sp.do_nothing_cost for sp in plans), 2)
        still_trips = [c for sp in plans for c in sp.selection.values() if c.outcome.tank_trips]
        for c in still_trips:
            uncertainties.append(
                f"No option keeps every tank under the limit: '{c.label}' is the cheapest, but the tank still trips."
            )
        metrics = {
            "pump_failures": float(len(failures)),
            "joint_plan_cost_usd": total,
            "do_nothing_cost_usd": nothing,
            "savings_vs_do_nothing_usd": round(nothing - total, 2),
            "trucks_available": float(sum(sp.free_trucks for sp in plans)),
            "trucks_used": float(sum(sp.trucks_used for sp in plans)),
            "truck_cost_per_bbl_usd": round(truck_bbl_cost, 2),
        }
        cheapest_well_bbl = min(
            shut_in_cost_per_bbl(w, ops.prices) for e in failures for w in ctx.dataset.facilities[e.facility_id].wells
        )
        metrics["cheapest_shut_in_cost_per_bbl_usd"] = round(cheapest_well_bbl, 2)
        fallback = _fallback_explanation(plans, truck_bbl_cost, cheapest_well_bbl, total, nothing)
        facts = {
            "findings": findings,
            "metrics": metrics,
            "evidence": evidence,
            "options": [o.model_dump() for o in options],
        }
        text = await explain(
            "cost-effectiveness agent",
            "Explain which interventions are financially attractive and why one option costs less than another.",
            facts,
            fallback,
        )
        return AgentAssessment(
            agent="cost",
            incident_ids=[e.id for e in failures],
            status="completed",
            assessment=f"Plan ${total:,.0f} vs ${nothing:,.0f} if nothing is done",
            findings=findings,
            evidence=evidence,
            metrics=metrics,
            options=options,
            uncertainties=uncertainties,
            suggested_interventions=suggested,
            explanation=text.text,
            llm_used=text.llm_used,
        )


def _option(c: Candidate, event_id: str, note: str) -> OptionAssessment:
    """One option row with its planner cost."""
    return OptionAssessment(plan_label=c.label, event_id=event_id, cost_usd=c.cost_total, note=note)


def _do_nothing_note(c: Candidate) -> str:
    """Plain words for the do-nothing outcome."""
    if c.outcome.tank_trips:
        return f"the tank trips and the facility's wells stay off until the repair (${c.cost_total:,.0f} lost)."
    return "the tank stays under the limit until the repair, so it costs nothing."


def _why(chosen: Candidate, do_nothing: Candidate) -> str:
    """Short reason a selected plan beats doing nothing."""
    if chosen is do_nothing or chosen.label == do_nothing.label:
        return " (doing nothing is cheapest here)."
    return f" instead of ${do_nothing.cost_total:,.0f} for doing nothing."


def _evidence(ctx: IncidentContext, event: Event, chosen: Candidate) -> list[str]:
    """The data behind one failure's plan: countdown, truck timing, cheapest well per barrel."""
    ds = ctx.dataset
    facility = ds.facilities[event.facility_id]
    logistics = ds.ops.systems[event.system_id].facilities[event.facility_id]
    out = []
    if event.hours_to_limit is not None:
        out.append(f"{event.facility_id}: shutdown in {event.hours_to_limit.p50:.2f} h without action (detector).")
    out.append(
        f"{event.facility_id}: trucks arrive {logistics.response_min:g} min after the call, "
        f"then every {logistics.cycle_min:g} min (ops.json)."
    )
    cheapest = min(facility.wells, key=lambda w: shut_in_cost_per_bbl(w, ds.ops.prices))
    out.append(
        f"{event.facility_id}: cheapest well to shut in is {cheapest.id} at "
        f"${shut_in_cost_per_bbl(cheapest, ds.ops.prices):.2f} per bbl kept out of the tank."
    )
    return out


def _fallback_explanation(
    plans: list[SystemPlan], truck_bbl_cost: float, well_bbl_cost: float, total: float, nothing: float
) -> str:
    """Deterministic explanation used when Nemotron is unavailable (and as its reference).

    The per-barrel comparison is computed from this dataset, never assumed: on another network shutting in a
    well could be cheaper per barrel than a truck.
    """
    shared = [sp for sp in plans if len(sp.selection) > sp.free_trucks]
    if truck_bbl_cost < well_bbl_cost:
        first = (
            f"Trucks remove water at ${truck_bbl_cost:.2f} per barrel, less than the cheapest well shut-in "
            f"(${well_bbl_cost:.2f} per barrel), so trucks are preferred and wells are shut in as late as possible "
            "only where trucks can't cover a tank."
        )
    else:
        first = (
            f"Shutting in the cheapest well costs ${well_bbl_cost:.2f} per barrel, no more than a truck "
            f"(${truck_bbl_cost:.2f} per barrel), so late shut-ins are preferred where they keep a tank safe."
        )
    parts = [first]
    for sp in shared:
        parts.append(
            f"{sp.system_id} has {len(sp.selection)} failures but only {sp.free_trucks} trucks, so the planner tried "
            "every split of the fleet and picked the cheapest combination."
        )
    parts.append(f"Total ${total:,.0f} instead of ${nothing:,.0f} if nothing is done.")
    return " ".join(parts)
