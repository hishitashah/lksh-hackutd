"""Production Impact Agent: interface only (ARCHITECTURE.md §8, Phase 5; full implementation is future work).

Responsibility (when implemented)
---------------------------------
How do the incident and each intervention affect oil and gas production: wells shut in, production lost, the impact
of delaying, one well versus several, and the trade-off between keeping production and managing water capacity. It
would return the expected impact, affected wells, production consequences, an option comparison, potential losses,
and its explanation and uncertainty (§8.2), using the real well rates in ``network.json``.

Today
-----
It returns a clearly marked ``not_implemented`` assessment, which the orchestrator lists as missing. (Lost oil and
gas revenue is already part of every plan's cost through the planner, but that isn't this agent's analysis.)
"""

from __future__ import annotations

from ..models import AgentAssessment
from .base_agent import BaseAgent, IncidentContext, not_implemented


class ProductionAgent(BaseAgent):
    """Placeholder for the future Production Impact Agent: reports ``not_implemented`` honestly."""

    name = "production"
    responsibility = "Effect of incidents and interventions on oil and gas production (ARCHITECTURE.md §8)."

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Return the not-implemented assessment for every incident in the context."""
        return not_implemented("production", ctx.incident_ids())
