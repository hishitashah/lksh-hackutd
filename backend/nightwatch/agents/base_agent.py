"""Shared incident context and the specialized-agent interface (ARCHITECTURE.md §4.1, Phase 5 groundwork).

Why one shared context
----------------------
All specialists investigate the same incidents from different angles (§4). They get one ``IncidentContext`` built
once per run, so they see the same moment, the same incidents and the same intervention options, and none can
overwrite another's findings: each returns its own ``AgentAssessment``.

What the context carries (§4.1)
-------------------------------
* the incidents (ID, category, asset, facility, detection evidence, tank level, time to shutdown): ``incidents``
* current and historical telemetry and the network: through ``dataset`` (``telemetry_window`` for readings)
* the available intervention options and the operational constraints (each system's free trucks): ``plans``,
  produced by the deterministic planner, never by an agent
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from ..models import AgentAssessment, AgentName, Event
from ..monitoring.anomaly_detector import detect
from ..monitoring.telemetry_processor import Dataset
from ..planning.intervention_planner import SystemPlan, plan_now


@dataclass
class IncidentContext:
    """Everything the specialists need about one moment (ARCHITECTURE.md §4.1)."""

    dataset: Dataset
    at_unix: int
    incidents: list[Event]
    plans: dict[str, SystemPlan] = field(default_factory=dict)

    def failures(self) -> list[Event]:
        """Ongoing pump failures (the incidents that have intervention options with costs)."""
        return [e for e in self.incidents if e.category == "pump_failure"]

    def inspections(self) -> list[Event]:
        """Ongoing leaks and worn pumps (SPEC: they get a field inspection, not a costed plan)."""
        return [e for e in self.incidents if e.category != "pump_failure"]

    def incident_ids(self) -> list[str]:
        """IDs of every incident in this context."""
        return [e.id for e in self.incidents]

    def telemetry_window(self, asset_id: str, metric: str, hours: float) -> tuple[np.ndarray, np.ndarray]:
        """Readings of one sensor over the last ``hours`` up to this context's time: (times, values)."""
        return self.dataset.window(asset_id, metric, int(self.at_unix - hours * 3600), self.at_unix)


def build_context(ds: Dataset, at: int | None = None) -> IncidentContext:
    """The context for every ongoing incident at ``at`` (None = 'now'), with the planner's options attached."""
    at = ds.clamp(at)
    ongoing = [e for e in detect(ds, at) if e.status == "ongoing"]
    return IncidentContext(dataset=ds, at_unix=at, incidents=ongoing, plans=plan_now(ds, at))


class BaseAgent(ABC):
    """A specialized agent: one responsibility, one structured result (ARCHITECTURE.md §4.1).

    Subclasses set ``name`` and ``responsibility`` and implement ``assess``. ``run`` wraps ``assess`` so an error
    becomes an explicit ``status='error'`` assessment instead of disappearing (§9.2, §17 "errors are surfaced").
    Timeouts are applied by the orchestrator's agent runner (Phase 6).
    """

    name: AgentName
    responsibility: str

    @abstractmethod
    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Investigate the context and return this agent's assessment."""

    async def run(self, ctx: IncidentContext) -> AgentAssessment:
        """Run ``assess``; turn any failure into a visible error assessment."""
        try:
            return await self.assess(ctx)
        except Exception as err:
            return error_assessment(self.name, ctx.incident_ids(), f"{type(err).__name__}: {err}")


def error_assessment(agent: AgentName, incident_ids: list[str], error: str, status: str = "error") -> AgentAssessment:
    """An assessment that says plainly this agent's analysis is missing, and why."""
    return AgentAssessment(
        agent=agent,
        incident_ids=incident_ids,
        status=status,
        findings=[],
        evidence=[],
        metrics={},
        options=[],
        uncertainties=[f"The {agent} analysis is missing: {error}"],
        suggested_interventions=[],
        explanation=f"The {agent} agent could not complete its analysis ({error}).",
        llm_used=False,
        error=error,
    )


def not_implemented(agent: AgentName, incident_ids: list[str]) -> AgentAssessment:
    """For planned agents that don't exist yet (§7, §8): clearly marked, never presented as done."""
    return AgentAssessment(
        agent=agent,
        incident_ids=incident_ids,
        status="not_implemented",
        findings=[],
        evidence=[],
        metrics={},
        options=[],
        uncertainties=[f"No {agent} analysis was performed: this agent is not implemented yet."],
        suggested_interventions=[],
        explanation=f"The {agent} agent is planned but not implemented yet, so this perspective is missing.",
        llm_used=False,
        error=None,
    )
