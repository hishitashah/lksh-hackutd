"""Feasibility Agent: interface only (ARCHITECTURE.md §7, Phase 5; full implementation is future work).

Responsibility (when implemented)
---------------------------------
Can a proposed intervention realistically be carried out? Are enough trucks available, can they arrive before the
tank reaches its shutdown limit, is the equipment available, can several incidents be handled with the resources at
hand, are there scheduling or transport limits? It would return a feasibility assessment, the required resources and
their availability, timing, limitations, reasons an intervention may not be possible, and alternatives (§7.2).

Today
-----
It returns a clearly marked ``not_implemented`` assessment. The orchestrator lists it as missing and never assumes
an intervention is feasible because this analysis is absent (§7). (The deterministic planner does check truck counts
and arrival before the repair ends, but that isn't this agent's independent review.)
"""

from __future__ import annotations

from ..models import AgentAssessment
from .base_agent import BaseAgent, IncidentContext, not_implemented


class FeasibilityAgent(BaseAgent):
    """Placeholder for the future Feasibility Agent: reports ``not_implemented`` honestly."""

    name = "feasibility"
    responsibility = "Whether each intervention can realistically be carried out (ARCHITECTURE.md §7)."

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Return the not-implemented assessment for every incident in the context."""
        return not_implemented("feasibility", ctx.incident_ids())
