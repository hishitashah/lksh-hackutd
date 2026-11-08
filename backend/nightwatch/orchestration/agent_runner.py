"""Runs the specialist agents concurrently, with timeouts and visible failures (ARCHITECTURE.md §9.2, Phase 5).

Why concurrent: the four specialists investigate the same incidents independently (§4), so none waits for another.
Each agent's Nemotron call runs in a worker thread, so the run takes about as long as the slowest agent, not the sum.

Every agent always produces an assessment:
* finished normally → its own assessment (``completed``, or ``not_implemented`` for future agents);
* crashed → ``error`` with the reason (``BaseAgent.run``);
* too slow → ``timeout`` after ``agent_timeout_s`` (``config.py``).
So the orchestrator always knows exactly which analyses exist and which are missing; nothing is silently dropped.

Adding a real agent later (e.g. the full Feasibility Agent) means changing one line in ``AGENTS``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..agents.base_agent import BaseAgent, IncidentContext, error_assessment
from ..agents.cost_agent import CostAgent
from ..agents.environmental_agent import EnvironmentalAgent
from ..agents.feasibility_agent import FeasibilityAgent
from ..agents.production_agent import ProductionAgent
from ..config import get_settings
from ..models import AgentAssessment, AgentName

# The four specialists of the architecture (§2 Stage 2). MVP: cost + environment are real; the others are stubs.
AGENTS: tuple[BaseAgent, ...] = (CostAgent(), EnvironmentalAgent(), FeasibilityAgent(), ProductionAgent())

OnDone = Callable[[AgentAssessment], Awaitable[None]]


async def run_agents(
    ctx: IncidentContext,
    agents: tuple[BaseAgent, ...] = AGENTS,
    timeout_s: float | None = None,
    on_done: OnDone | None = None,
) -> dict[AgentName, AgentAssessment]:
    """Run every agent on the same context at the same time and collect one assessment per agent.

    Args:
        ctx: the shared incident context (§4.1).
        agents: which agents to run (default: all four).
        timeout_s: per-agent time limit in seconds (default: ``agent_timeout_s``).
        on_done: optional async callback, called as each agent finishes (Sentinel streams these in Phase 7).
    """
    limit = timeout_s if timeout_s is not None else get_settings().agent_timeout_s

    async def one(agent: BaseAgent) -> AgentAssessment:
        try:
            result = await asyncio.wait_for(agent.run(ctx), timeout=limit)
        except TimeoutError:
            result = error_assessment(agent.name, ctx.incident_ids(), f"no answer within {limit:g} s", status="timeout")
        if on_done is not None:
            await on_done(result)
        return result

    results = await asyncio.gather(*(one(a) for a in agents))
    return {a.agent: a for a in results}
