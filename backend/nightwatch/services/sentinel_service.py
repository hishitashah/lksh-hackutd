"""Sentinel: the live, streamed multi-agent run the operator watches (ARCHITECTURE.md §11, Phase 7).

Where this sits in the app
--------------------------
``POST /api/{ds}/agent/run`` calls ``start_run``. That runs the central orchestrator (``orchestration/``) in the
background and turns its progress into server-sent events for the Sentinel panel, as they happen:

====================  =====================================================================================
step 0  ``triage``    which incidents are ongoing, and the planner's options (both computed deterministically)
steps 1-4 (agents)    first all ``running`` at once (they really run in parallel), then each agent's final
                      status as it finishes: ``done``, ``not_implemented`` (never shown as done) or ``error``
step 5 ``orchestrator`` ``running`` once the decision is made, ``done`` with the operator explanation
step 6 ``act``        the drafted actions, waiting for the operator (nothing is dispatched from here)
then                  ``recommendation`` (Recommendation), ``drafts`` (DraftAction list), ``done`` (run ID + handoff)
====================  =====================================================================================

A step's ``index`` can arrive twice (``running``, then final): the frontend replaces it by index. Agent indexes
follow the ``AGENTS`` registry order, so adding an agent later just adds a step.

What it stores
--------------
Each finished run is kept in memory as an ``AgentRunResult`` (final step per index, the recommendation, the drafts
and the handoff) for ``GET /agent/runs/{id}`` and for approval and dispatch (Phase 8, which reads drafts through
``get_run``). The run is stored even if the browser tab closes mid-stream.

Rules kept here: drafts are built only from the orchestrator's Recommendation, all ``pending`` (rule 6); every number
in titles, labels and the handoff comes from the run (rule 4); agents that didn't run say so (rule 5).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from ..agents.base_agent import BaseAgent, IncidentContext
from ..config import get_settings
from ..models import AgentAssessment, AgentRunResult, AgentStep, DraftAction, Event, ToolCall
from ..monitoring.anomaly_detector import fmt_time
from ..monitoring.telemetry_processor import Dataset
from ..orchestration.agent_runner import AGENTS
from ..orchestration.decision_engine import hhmm
from ..orchestration.orchestrator import OrchestratorEvent, OrchestratorRun, orchestrate, urgency_order

# Finished runs kept per dataset (oldest dropped first). Enough for a shift of re-runs; memory only.
MAX_RUNS_PER_DATASET = 50

SSE = dict[str, str]  # one server-sent event: {"event": name, "data": JSON text}

AGENT_TITLES = {"cost": "Cost", "environment": "Environment", "feasibility": "Feasibility", "production": "Production"}

# AssessmentStatus -> AgentStep status. A timeout is an error step; the assessment keeps the exact status.
STEP_STATUS = {"completed": "done", "not_implemented": "not_implemented", "error": "error", "timeout": "error"}

_END = object()  # end-of-stream marker in a run's queue
_runs: dict[str, OrderedDict[str, AgentRunResult]] = {}  # dataset ID -> run ID -> finished run
_tasks: set[asyncio.Task] = set()  # running runs (kept referenced so they aren't garbage-collected mid-run)


def s(n: int) -> str:
    """Plural suffix for a count."""
    return "" if n == 1 else "s"


def nemotron_call() -> ToolCall:
    """The tool-call entry for a Nemotron explanation that passed the grounding check (only added when one did)."""
    return ToolCall(
        name="nemotron_explain",
        args={"model": get_settings().super_model},
        result_summary="Explanation written; every number in it matched the facts it was given.",
    )


class _RunBuilder:
    """Collects one run's steps and turns orchestrator progress into SSE events on a queue."""

    def __init__(self, run_id: str, agents: tuple[BaseAgent, ...], queue: asyncio.Queue) -> None:
        self.run_id = run_id
        self.queue = queue
        self.t0 = time.perf_counter()
        self.agent_index = {a.name: 1 + i for i, a in enumerate(agents)}
        self.orchestrator_index = 1 + len(agents)
        self.act_index = 2 + len(agents)
        self.steps: dict[int, AgentStep] = {}  # latest version of each step, by index
        self.ctx: IncidentContext | None = None
        self.result: AgentRunResult | None = None

    def elapsed(self) -> float:
        """Seconds since the run started."""
        return round(time.perf_counter() - self.t0, 3)

    def emit(self, name: str, data: str) -> None:
        """Queue one SSE event for the client."""
        self.queue.put_nowait({"event": name, "data": data})

    def step(
        self,
        index: int,
        step: str,
        status: str,
        title: str,
        message: str,
        tool_calls: list[ToolCall] | None = None,
        grounded: bool | None = None,
        assessment: AgentAssessment | None = None,
    ) -> None:
        """Record (or replace, by index) one step and stream it."""
        st = AgentStep(
            run_id=self.run_id,
            index=index,
            step=step,
            status=status,
            title=title,
            message=message,
            tool_calls=tool_calls or [],
            grounded=grounded,
            elapsed_s=self.elapsed(),
            assessment=assessment,
        )
        self.steps[index] = st
        self.emit("step", st.model_dump_json())

    # --- orchestrator progress -> steps -----------------------------------------------------------------

    async def on_event(self, e: OrchestratorEvent) -> None:
        """Handle one orchestrator progress event (``orchestrate``'s ``on_event`` callback)."""
        if e.kind == "started" and e.context is not None:
            self.ctx = e.context
            self.triage(e.context)
            if e.context.incidents:  # no incidents: the orchestrator runs no agent, so none is shown
                n = len(e.context.incidents)
                for name, i in self.agent_index.items():
                    self.step(
                        i,
                        name,
                        "running",
                        f"{AGENT_TITLES.get(name, name)}: investigating",
                        f"Investigating {n} ongoing incident{s(n)}.",
                    )
        elif e.kind == "agent_done" and e.assessment is not None:
            self.agent_step(e.assessment)
        elif e.kind == "decided" and e.decision is not None:
            d = e.decision
            done = ", ".join(d.agents_completed) or "none"
            missing = f"; missing: {', '.join(d.missing)}" if d.missing else ""
            self.step(
                self.orchestrator_index,
                "orchestrator",
                "running",
                "Orchestrator: deciding",
                f"Applying the decision policy with the analyses from: {done}{missing}. "
                "Writing the explanation for the operator.",
            )

    def triage(self, ctx: IncidentContext) -> None:
        """Step 0: the incidents at this moment and the planner's options (computed in ``build_context``)."""
        failures = urgency_order(ctx.failures())
        leaks = [e for e in ctx.incidents if e.category == "pipeline_leak"]
        worn = [e for e in ctx.incidents if e.category == "pump_degradation"]
        n = len(ctx.incidents)
        parts: list[str] = []
        for e in failures:
            countdown = (
                f"shutdown in {e.hours_to_limit.p50:.1f} h" if e.hours_to_limit is not None else "tank already shut in"
            )
            parts.append(f"{e.facility_id}: pump failure ({e.asset_id} since {fmt_time(e.onset_unix)}), {countdown}")
        parts += [f"leak on {e.asset_id} since {fmt_time(e.onset_unix)}" for e in leaks]
        parts += [f"{e.asset_id} wearing out since {fmt_time(e.onset_unix)}" for e in worn]
        message = f"At {fmt_time(ctx.at_unix)}: " + ("; ".join(parts) + "." if parts else "no ongoing incidents.")
        calls = [
            ToolCall(
                name="detect_incidents",
                args={"at_unix": ctx.at_unix},
                result_summary=(
                    f"{n} ongoing: {len(failures)} pump failure{s(len(failures))}, {len(leaks)} leak{s(len(leaks))}, "
                    f"{len(worn)} worn pump{s(len(worn))}"
                ),
            )
        ]
        if ctx.plans:
            summary = "; ".join(
                f"{sid}: {len(sp.options)} failure{s(len(sp.options))} share {sp.free_trucks} truck{s(sp.free_trucks)}, "
                f"{sum(len(o.candidates) for o in sp.options.values())} options evaluated, cheapest joint plan "
                f"${sp.total_cost:,.0f}"
                for sid, sp in sorted(ctx.plans.items())
            )
            calls.append(ToolCall(name="plan_options", args={"systems": sorted(ctx.plans)}, result_summary=summary))
        title = f"Triage: {n} ongoing incident{s(n)}" if n else "Triage: no ongoing incidents"
        self.step(0, "triage", "done", title, message, calls, grounded=True)

    def agent_step(self, a: AgentAssessment) -> None:
        """Steps 1-4: one specialist's final status, with its assessment attached."""
        name = AGENT_TITLES.get(a.agent, a.agent)
        status = STEP_STATUS[a.status]
        if a.status == "completed":
            title = f"{name}: {a.assessment}" if a.assessment else f"{name}: done"
        elif a.status == "not_implemented":
            title = f"{name}: not implemented"
        else:
            title = f"{name}: {a.status}"
        calls = [nemotron_call()] if a.llm_used else []
        index = self.agent_index.get(a.agent, len(self.agent_index) + 1)
        self.step(
            index, a.agent, status, title, a.explanation, calls, grounded=True if status == "done" else None, assessment=a
        )

    # --- the end of the run ------------------------------------------------------------------------------

    def finish(self, run: OrchestratorRun) -> None:
        """Orchestrator and act steps, then the recommendation, drafts and done events; keeps the result."""
        rec, ctx = run.recommendation, run.context
        n_plans, n_insp = len(rec.selected_plans), len(rec.inspections)
        if not ctx.incidents:
            title = "Orchestrator: nothing to recommend"
        elif rec.status == "failed":
            title = "Orchestrator: no valid plan for some incidents"
        else:
            insp = f" + {n_insp} inspection{s(n_insp)}" if n_insp else ""
            title = (
                f"Orchestrator: recommend ${rec.estimated_cost_usd:,.0f} for {n_plans} failure{s(n_plans)}{insp}"
                if n_plans
                else f"Orchestrator: {n_insp} inspection{s(n_insp)}"
            )
        decide_call = ToolCall(
            name="decide",
            args={
                "policy": "hard constraints, then cheapest valid joint plan, then fewer trucks",
                "env_veto_risk": get_settings().env_veto_risk,
                "agents_used": rec.agents_completed,
            },
            result_summary=(
                f"{rec.status}: {n_plans} plan{s(n_plans)}, ${rec.estimated_cost_usd:,.0f} (cheapest "
                f"${rec.cheapest_cost_usd:,.0f}), {n_insp} inspection{s(n_insp)}"
                + (f"; missing: {', '.join(rec.missing_assessments)}" if rec.missing_assessments else "")
            ),
        )
        calls = [decide_call] + ([nemotron_call()] if rec.llm_used else [])
        status = "error" if rec.status == "failed" else "done"
        self.step(self.orchestrator_index, "orchestrator", status, title, rec.explanation, calls, grounded=True)

        drafts = build_drafts(run, self.run_id)
        skipped = [_facility(ctx, p.event_ids[0]) for p in rec.selected_plans if all(a.type == "none" for a in p.actions)]
        skip_note = "".join(f" {f}: doing nothing is cheapest, so no field action is drafted." for f in skipped)
        if drafts:
            self.step(
                self.act_index,
                "act",
                "waiting_approval",
                f"Act: {len(drafts)} action{s(len(drafts))} need{'s' if len(drafts) == 1 else ''} your approval",
                "Drafted from the orchestrator's recommendation. Nothing is dispatched until you approve." + skip_note,
                grounded=True,
            )
        else:
            self.step(
                self.act_index,
                "act",
                "done",
                "Act: no actions to approve",
                "There is nothing to send to the field." + skip_note,
                grounded=True,
            )

        handoff = build_handoff(run, drafts)
        self.emit("recommendation", rec.model_dump_json())
        self.emit("drafts", json.dumps([d.model_dump() for d in drafts]))
        self.emit("done", json.dumps({"run_id": self.run_id, "handoff": handoff}))
        self.result = AgentRunResult(
            run_id=self.run_id, steps=self.ordered_steps(), recommendation=rec, drafts=drafts, handoff=handoff
        )

    def fail(self, err: Exception) -> None:
        """The run crashed: say so plainly, with no recommendation and no drafts (never a fake result)."""
        self.step(
            self.orchestrator_index,
            "orchestrator",
            "error",
            "Orchestrator: the run failed",
            f"The run stopped before a recommendation: {type(err).__name__}: {err}. Nothing was drafted.",
        )
        self.emit("done", json.dumps({"run_id": self.run_id, "handoff": None}))
        self.result = AgentRunResult(run_id=self.run_id, steps=self.ordered_steps(), recommendation=None, drafts=[])

    def ordered_steps(self) -> list[AgentStep]:
        """The final version of every step, by index."""
        return [self.steps[i] for i in sorted(self.steps)]


# ---------------------------------------------------------------------------
# Drafts and handoff (built only from the run)
# ---------------------------------------------------------------------------


def _facility(ctx: IncidentContext, event_id: str) -> str:
    """Facility of an incident in the context (the event ID if it isn't found)."""
    return next((e.facility_id for e in ctx.incidents if e.id == event_id), event_id)


def build_drafts(run: OrchestratorRun, run_id: str) -> list[DraftAction]:
    """The operator's approval cards: one per plan action, then one per inspection. All ``pending`` (rule 6).

    Order: pump-failure actions most urgent first (as ``selected_plans``), then inspections in the orchestrator's
    priority order. Plans whose only action is 'none' get no draft. Times in labels come from ops.json (truck
    response time) and the plan itself (shut-in start, repair end), as HH:MM UTC.
    """
    ctx, rec, ds = run.context, run.recommendation, run.context.dataset
    events: dict[str, Event] = {e.id: e for e in ctx.incidents}
    drafts: list[DraftAction] = []

    def add(label: str, action, event_id: str) -> None:
        drafts.append(
            DraftAction(
                id=f"{run_id}-d{len(drafts) + 1}",
                kind=action.type,
                label=label,
                action=action,
                event_id=event_id,
                status="pending",
            )
        )

    for plan in rec.selected_plans:
        eid = plan.event_ids[0]
        e = events[eid]
        for a in plan.actions:
            if a.type == "dispatch_truck" and a.facility_id and a.trucks:
                response_min = ds.ops.systems[e.system_id].facilities[a.facility_id].response_min
                first = ctx.at_unix + int(response_min * 60)
                trips = a.trips_per_truck or 1
                each = " each" if a.trucks > 1 else ""
                add(
                    f"Send {a.trucks} truck{s(a.trucks)} to {a.facility_id} ({trips} trip{s(trips)}{each}, first "
                    f"arrives ~{hhmm(first)})",
                    a,
                    eid,
                )
            elif a.type == "shut_in_wells" and a.wells:
                start = ctx.at_unix + int(round((a.start_after_min or 0) * 60))
                repair_end = ctx.plans[e.system_id].options[eid].scenario.repair_end_unix
                add(f"Shut in {' + '.join(a.wells)} at {hhmm(start)} until the repair ends ({hhmm(repair_end)})", a, eid)
    for insp in rec.inspections:
        e = next((x for x in ctx.inspections() if x.asset_id == insp.asset_id), None)
        if e is None:
            continue
        what = "for a leak" if e.category == "pipeline_leak" else "(pump wearing out)"
        add(f"Inspect {insp.asset_id} {what}", insp, e.id)
    return drafts


def build_handoff(run: OrchestratorRun, drafts: list[DraftAction]) -> str:
    """30-second shift handoff for the next operator: what's happening, the plan, what waits for approval.

    Deterministic template (no LLM): every number comes from the run.
    """
    ctx, rec, d = run.context, run.recommendation, run.decision
    when = fmt_time(ctx.at_unix)
    if not ctx.incidents:
        return f"{when}. No ongoing incidents; nothing needs action."
    parts = [f"{when}."]
    failures = urgency_order(ctx.failures())
    by_system: dict[str, list[Event]] = {}
    for e in failures:
        by_system.setdefault(e.system_id, []).append(e)
    for sid, evs in by_system.items():
        since = ", ".join(f"{e.asset_id} since {hhmm(e.onset_unix)}" for e in evs)
        parts.append(f"{sid} has {len(evs)} pump failure{s(len(evs))} ({since}).")
    if failures and failures[0].hours_to_limit is not None:
        e0 = failures[0]
        parts.append(f"{e0.facility_id} reaches shutdown in {e0.hours_to_limit.p50:.1f} h without action.")
    leaks = [e.asset_id for e in ctx.incidents if e.category == "pipeline_leak"]
    worn = [e.asset_id for e in ctx.incidents if e.category == "pump_degradation"]
    if leaks:
        parts.append(f"Leak{s(len(leaks))} on {', '.join(leaks)}.")
    if worn:
        parts.append(f"Worn pump{s(len(worn))}: {', '.join(worn)}.")
    if rec.selected_plans:
        plans = "; ".join(f"{_facility(ctx, p.event_ids[0])}: {p.label}" for p in rec.selected_plans)
        parts.append(f"Plan: {plans}. Total ${rec.estimated_cost_usd:,.0f} vs ${d.do_nothing_cost:,.0f} doing nothing.")
    if rec.inspections:
        first, *rest = [i.asset_id for i in rec.inspections]
        parts.append(f"Inspect {first} first" + (f", then {', '.join(rest)}." if rest else "."))
    if drafts:
        parts.append(f"{len(drafts)} action{s(len(drafts))} wait{'s' if len(drafts) == 1 else ''} for your approval.")
    if rec.missing_assessments:
        parts.append(f"Not assessed: {', '.join(rec.missing_assessments)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Running and storing
# ---------------------------------------------------------------------------


def start_run(ds: Dataset, at: int | None = None, agents: tuple[BaseAgent, ...] = AGENTS) -> tuple[str, AsyncIterator[SSE]]:
    """Start a Sentinel run in the background and return (run ID, the stream of its SSE events).

    Must be called from a running event loop (the async route). The run continues and is stored even if nobody
    reads the stream to the end.

    Args:
        ds: the loaded dataset.
        at: the moment to analyse, unix seconds (None = the dataset's 'now'; the time machine passes a position).
        agents: the specialists to run (tests pass fakes).
    """
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    queue: asyncio.Queue[Any] = asyncio.Queue()
    task = asyncio.create_task(_execute(ds, at, agents, run_id, queue))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    async def stream() -> AsyncIterator[SSE]:
        while (item := await queue.get()) is not _END:
            yield item

    return run_id, stream()


async def _execute(ds: Dataset, at: int | None, agents: tuple[BaseAgent, ...], run_id: str, queue: asyncio.Queue) -> None:
    """Run the orchestrator, stream its progress, then store the finished run (whatever happened)."""
    b = _RunBuilder(run_id, agents, queue)
    try:
        run = await orchestrate(ds, at, agents=agents, on_event=b.on_event)
        b.finish(run)
    except Exception as err:  # a bug or bad data: surfaced as an error step, never a fake result
        b.fail(err)
    finally:
        if b.result is not None:
            _store(ds.id, b.result)
        queue.put_nowait(_END)


def _store(ds_id: str, result: AgentRunResult) -> None:
    """Keep a finished run (dropping the oldest beyond ``MAX_RUNS_PER_DATASET``)."""
    runs = _runs.setdefault(ds_id, OrderedDict())
    runs[result.run_id] = result
    while len(runs) > MAX_RUNS_PER_DATASET:
        runs.popitem(last=False)


def get_run(ds_id: str, run_id: str) -> AgentRunResult | None:
    """A finished run by ID (None if unknown). Phase 8 finds drafts to approve through this."""
    return _runs.get(ds_id, {}).get(run_id)


def latest_run(ds_id: str) -> AgentRunResult | None:
    """The most recent finished run for a dataset, if any."""
    runs = _runs.get(ds_id)
    return next(reversed(runs.values())) if runs else None
