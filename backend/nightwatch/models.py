"""The data contract: every shape the backend sends or receives (task T0.2).

Where this sits in the app
--------------------------
Everything the frontend, the Sentinel agent and the tests exchange is defined here,
once. The API (``api.py``) returns these models, FastAPI turns them into an OpenAPI
spec, and ``make types`` turns that spec into TypeScript (``frontend/src/api/types.ts``).
So if a field is described here, the frontend sees the same description.

Rules for changing this file
----------------------------
* Changing a model changes the contract for all four teammates. Say so in the PR,
  then run ``make types`` so the frontend types stay in sync (CI checks this).
* Every field needs a ``description=`` with its units (bbl, bpd, psi, %, unix seconds).
* Field names follow SPEC.md and the optional ``events.json`` shape where they overlap.

Two kinds of models
-------------------
* ``FileModel``: mirrors the organizers' input files (network.json, ops.json). Unknown
  extra keys are ignored so a new dataset with extra fields still loads.
* ``ApiModel``: what our API returns. Unknown keys are rejected, so a typo in a fixture
  or a response fails loudly in tests instead of silently disappearing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FileModel(BaseModel):
    """Base for models that mirror the organizers' input files.

    ``extra="ignore"`` lets us load a dataset (e.g. the unseen ``test/`` network) even if
    it carries keys we don't use yet.
    """

    model_config = ConfigDict(extra="ignore")


class ApiModel(BaseModel):
    """Base for everything our API returns or accepts.

    ``extra="forbid"`` makes fixtures and responses fail validation on unknown keys,
    which catches typos and contract drift early.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Shared value types (string enums). Kept as Literals so TypeScript gets unions.
# ---------------------------------------------------------------------------

EventCategory = Literal["pump_failure", "pipeline_leak", "pump_degradation", "other"]
"""The three problems from SPEC.md, plus ``other`` (allowed by the events.json shape)."""

EventStatus = Literal["ongoing", "resolved"]
"""``ongoing`` = still happening at the time asked about; ``resolved`` = ended before it."""

ActionType = Literal["dispatch_truck", "shut_in_wells", "field_inspection", "none"]
"""Action types from SPEC.md "events.json". ``none`` means "do nothing / accept the trip"."""

PlanKind = Literal["cheapest", "long_run", "do_nothing", "alternative"]
"""Which role a plan plays in the plan panel (plan.md "Cheapest now vs best in the long run")."""

AgentStepName = Literal["triage", "investigate", "plan", "verify", "act", "report"]
"""Sentinel's six workflow steps, in order (plan.md "Sentinel")."""


# ---------------------------------------------------------------------------
# Input files: network.json
# ---------------------------------------------------------------------------


class Well(FileModel):
    """One well. Its water fills the facility's tank; its oil and gas are the revenue we lose if it's shut in."""

    id: str = Field(description="Well ID, e.g. 'S1_WELL_C2'.")
    baseline_water_bpd: float = Field(description="Normal water production, barrels per day (bpd).")
    oil_bpd: float = Field(description="Oil production, barrels per day. Lost while the well is shut in.")
    gas_mcfd: float = Field(description="Gas production, mcf (thousand cubic feet) per day. Lost while shut in.")


class Tank(FileModel):
    """The facility's water tank. Level % × capacity = volume in barrels."""

    id: str = Field(description="Tank ID, e.g. 'S1_TANK_C'.")
    capacity_bbl: float = Field(description="Tank size in barrels (bbl).")


class Pump(FileModel):
    """The pump that empties the tank into the pipeline. It starts at start_level_pct and stops at stop_level_pct."""

    id: str = Field(description="Pump ID, e.g. 'S1_PUMP_C'.")
    max_flow_bpd: float = Field(description="A healthy pump's flow while running, bpd.")
    start_level_pct: float = Field(description="Tank level (%) at which the pump switches on.")
    stop_level_pct: float = Field(description="Tank level (%) at which the pump switches off.")


class Pipeline(FileModel):
    """The pipeline from the pump to the system's disposal site."""

    id: str = Field(description="Pipeline ID, e.g. 'S1_PIPELINE_C'.")
    max_bpd: float = Field(description="Pipeline capacity, bpd.")
    max_pressure_psi: float = Field(description="Pipeline pressure rating, psi.")


class Facility(FileModel):
    """One facility: a few wells feeding one tank, one pump and one pipeline."""

    id: str = Field(description="Facility ID, e.g. 'S1_FACILITY_C'.")
    lat: float = Field(description="Latitude (placeholder coordinates from the organizers).")
    lon: float = Field(description="Longitude (placeholder coordinates from the organizers).")
    wells: list[Well] = Field(description="Wells feeding this facility's tank.")
    tank: Tank = Field(description="The facility's tank.")
    pump: Pump = Field(description="The facility's pump.")
    pipeline: Pipeline = Field(description="The facility's pipeline to the disposal site.")


class DisposalSite(FileModel):
    """Where a system's pipelines end. No sensors, never fails, fixed pressure."""

    id: str = Field(description="Disposal site ID, e.g. 'S1_DISPOSAL'.")
    lat: float = Field(description="Latitude.")
    lon: float = Field(description="Longitude.")
    pressure_psi: float = Field(description="Fixed disposal pressure, psi. Healthy pipeline/pump pressures sit above it.")


class System(FileModel):
    """A group of facilities sharing one disposal site and one truck fleet."""

    id: str = Field(description="System ID, e.g. 'S1'.")
    center: list[float] = Field(description="Map center as [lat, lon].")
    truck_yard: list[float] = Field(description="Truck yard location as [lat, lon]. Trucks start every job from here.")
    disposal_site: DisposalSite = Field(description="The system's disposal site.")
    facilities: list[Facility] = Field(description="Facilities in this system.")


class TankLimits(FileModel):
    """Tank limits in percent full. The same for every tank (SPEC: only defaults.tank_limits_pct)."""

    warning: float = Field(description="Warning level, % full.")
    shutdown: float = Field(description="Shutdown level, % full. At this level the wells shut in automatically.")


class NetworkDefaults(FileModel):
    """Network-wide defaults block of network.json."""

    tank_limits_pct: TankLimits = Field(description="Tank limits used for every tank.")


class Network(FileModel):
    """Parsed network.json: the equipment list. Read every number from here, never hardcode it."""

    meta: dict[str, Any] = Field(default_factory=dict, description="Units and timezone info from the file.")
    defaults: NetworkDefaults = Field(description="Network-wide defaults (tank limits).")
    systems: list[System] = Field(description="All systems in the network.")


# ---------------------------------------------------------------------------
# Input files: ops.json
# ---------------------------------------------------------------------------


class Prices(FileModel):
    """Prices used to value lost production."""

    oil_per_bbl: float = Field(description="Oil price, $ per barrel.")
    gas_per_mcf: float = Field(description="Gas price, $ per mcf.")


class Trucking(FileModel):
    """Emergency truck economics (same for every system)."""

    truck_capacity_bbl: float = Field(description="Barrels removed per truck trip.")
    cost_per_trip: float = Field(description="Flat price of one truck trip, $.")


class FacilityLogistics(FileModel):
    """Truck travel times for one facility."""

    response_min: float = Field(description="Minutes from the call until a truck first reaches the facility from the yard.")
    cycle_min: float = Field(description="Minutes for one round trip: haul away, empty, come back.")


class SystemOps(FileModel):
    """One system's truck fleet and per-facility travel times."""

    emergency_fleet_size: int = Field(description="Number of trucks this system has. Shared by all its facilities.")
    facilities: dict[str, FacilityLogistics] = Field(description="Travel times keyed by facility ID.")


class Ops(FileModel):
    """Parsed ops.json: prices, repair times and trucks."""

    dataset_id: str | None = Field(default=None, description="Organizers' dataset label, if present.")
    prices: Prices = Field(description="Oil and gas prices.")
    expected_repair_hours: dict[str, float] = Field(
        description="Repair duration in hours by problem type, e.g. {'pump_failure': 6}. Counted from onset."
    )
    emergency_trucking: Trucking = Field(description="Truck capacity and price per trip.")
    systems: dict[str, SystemOps] = Field(description="Fleet and travel times keyed by system ID.")


# ---------------------------------------------------------------------------
# Datasets and the network endpoint
# ---------------------------------------------------------------------------


class Health(ApiModel):
    """Liveness check. The frontend shows a MOCK DATA badge when ``mock`` is true."""

    ok: bool = Field(description="Always true when the server is up.")
    mock: bool = Field(description="True when routes serve fixtures instead of real results (NIGHTWATCH_MOCK=1).")
    version: str = Field(description="Backend version string.")


class DatasetInfo(ApiModel):
    """A dataset the app can load: a folder holding network.json, ops.json and telemetry.csv."""

    id: str = Field(description="Dataset ID used in URLs, e.g. 'full_week', 'snapshot_2026-10-08T1200', 'test'.")
    path: str = Field(description="Folder path relative to the repo root.")
    source: Literal["folder", "upload"] = Field(description="'folder' = found on disk, 'upload' = dropped into the UI.")


class TimeRange(ApiModel):
    """The dataset's time window, taken from the telemetry (no file states it)."""

    start_unix: int = Field(description="First telemetry timestamp, unix seconds (UTC).")
    now_unix: int = Field(description="Last telemetry timestamp = 'now' for this dataset. Never use the system clock.")
    step_s: int = Field(description="Seconds between readings (300 = 5 minutes).")


class NetworkResponse(ApiModel):
    """Everything static about a dataset: the network, the ops rules and the time window."""

    dataset_id: str = Field(description="Which dataset this describes.")
    network: Network = Field(description="Parsed network.json.")
    ops: Ops = Field(description="Parsed ops.json.")
    time_range: TimeRange = Field(description="Time window from the telemetry.")


# ---------------------------------------------------------------------------
# Events, alerts and live state
# ---------------------------------------------------------------------------


class HoursToLimit(ApiModel):
    """Countdown to the shutdown limit if nothing changes. Only for ongoing pump failures."""

    p50: float = Field(description="Best estimate, hours.")
    p10: float | None = Field(default=None, description="Optimistic bound, hours (optional).")
    p90: float | None = Field(default=None, description="Pessimistic bound, hours (optional).")


class Event(ApiModel):
    """One detected problem. Produced by the detector (detect.py), never read from an answer key."""

    id: str = Field(description="Our event ID, stable for the same problem across time-machine steps.")
    category: EventCategory = Field(description="Problem type.")
    system_id: str = Field(description="System the problem is in.")
    facility_id: str = Field(description="Facility the problem is at.")
    asset_id: str = Field(description="Equipment with the problem (pump or pipeline ID).")
    status: EventStatus = Field(description="'ongoing' at the requested time, or 'resolved' before it.")
    onset_unix: int = Field(description="When the problem started, unix seconds (back-dated by the detector).")
    end_unix: int | None = Field(default=None, description="When it ended, unix seconds. Null while ongoing.")
    confidence: float = Field(ge=0, le=1, description="How sure the detector is, 0-1. Low for slow problems just starting.")
    evidence: list[str] = Field(
        description="Plain-English reasons with numbers, e.g. '140 bpd missing between pump and disposal'."
    )
    level_pct: float | None = Field(default=None, description="Tank level at the requested time, %. Pump failures only.")
    hours_to_limit: HoursToLimit | None = Field(
        default=None, description="Countdown to shutdown. Null for leaks, worn pumps and resolved events."
    )
    trips_at_unix: int | None = Field(
        default=None, description="When the tank reaches shutdown if nobody acts, unix seconds. Pump failures only."
    )
    lost_revenue_per_hour: float | None = Field(
        default=None, description="$ per hour lost if the whole facility shuts in. Used as the alert-order tie-break."
    )


class Alert(ApiModel):
    """An ongoing event as shown in the alerts view, with its one-line action.

    Order (SPEC "Alerts view"): pump failures first, smallest hours_to_limit first, ties go to
    higher lost revenue per hour. Leaks and worn pumps follow in any order.
    """

    event: Event = Field(description="The ongoing event.")
    order: int | None = Field(
        default=None, description="1-based position among pump failures. Null for leaks and worn pumps."
    )
    action_label: str = Field(description="Short action text, e.g. '1 truck, 1 trip · $905' or 'Send inspector'.")


class FacilityState(ApiModel):
    """A facility's readings at the requested time. Drives the map, skyline and side panels."""

    facility_id: str = Field(description="Facility ID.")
    system_id: str = Field(description="System ID.")
    tank_level_pct: float = Field(description="Tank level, %.")
    pump_status: int = Field(description="Pump status: 0 stopped, 1 running, 2 fault.")
    inflow_bpd: float = Field(description="Water flowing into the tank (sum of wells), bpd.")
    wells_producing: int = Field(description="Wells with status 1 (producing).")
    wells_total: int = Field(description="Wells at this facility.")
    shut_in: bool = Field(description="True when every well is shut in (the facility has tripped).")


class StateResponse(ApiModel):
    """The whole network as of one moment: what the Command view renders."""

    dataset_id: str = Field(description="Dataset this state belongs to.")
    at_unix: int = Field(description="The moment this state describes, unix seconds (time machine position).")
    now_unix: int = Field(description="The dataset's 'now' (last telemetry timestamp).")
    facilities: list[FacilityState] = Field(description="Every facility's readings at at_unix.")
    alerts: list[Alert] = Field(description="Ongoing problems, already in alerts-view order.")
    events: list[Event] = Field(description="Every event that has started by at_unix, ongoing and resolved.")
    lost_revenue_per_hour_total: float = Field(description="$ per hour being lost right now from shut-in wells.")


class SeriesResponse(ApiModel):
    """One sensor's readings over time, for the trend charts."""

    asset_id: str = Field(description="Asset the sensor belongs to.")
    metric: str = Field(description="Metric name from telemetry.csv, e.g. 'level_pct', 'flow_bpd'.")
    points: list[list[float]] = Field(description="[[unix seconds, value], ...] in time order.")


# ---------------------------------------------------------------------------
# Plans (optimizer output) and what-if simulation
# ---------------------------------------------------------------------------


class PlanAction(ApiModel):
    """One step of a plan. Same shape as SPEC 'events.json' recommended_actions."""

    type: ActionType = Field(description="What to do.")
    facility_id: str | None = Field(default=None, description="dispatch_truck: facility the trucks go to.")
    trucks: int | None = Field(default=None, description="dispatch_truck: number of trucks, all called at plan start.")
    trips_per_truck: int | None = Field(default=None, description="dispatch_truck: trips each truck makes (same for all).")
    wells: list[str] | None = Field(default=None, description="shut_in_wells: well IDs to shut in.")
    start_after_min: float | None = Field(
        default=None, description="shut_in_wells: minutes after plan start to shut in (default 0)."
    )
    asset_id: str | None = Field(default=None, description="field_inspection: pump or pipeline to inspect.")


class CostBreakdown(ApiModel):
    """Where a plan's cost comes from. All values in $."""

    trucks: float = Field(description="Truck trips × cost per trip, $.")
    shut_in: float = Field(description="Revenue lost from wells we chose to shut in early, $.")
    trip: float = Field(description="Revenue lost if the tank still reaches shutdown (automatic shut-in), $.")


class Plan(ApiModel):
    """One candidate response to one or more pump failures, with its simulated outcome."""

    kind: PlanKind = Field(description="Role in the plan panel: cheapest, long_run, do_nothing or alternative.")
    event_ids: list[str] = Field(description="Events this plan covers (several when failures share a fleet).")
    actions: list[PlanAction] = Field(description="Steps of the plan.")
    label: str = Field(description="One-line summary, e.g. '1 truck, 1 trip · $905'.")
    cost_total: float = Field(description="Total cost, $ (= trucks + shut_in + trip).")
    cost_breakdown: CostBreakdown = Field(description="Cost split by source.")
    tank_trips: bool = Field(description="True if the tank still reaches the shutdown limit under this plan.")
    trips_at_unix: int | None = Field(
        default=None, description="When the tank reaches shutdown under this plan, if it does."
    )
    peak_level_pct: float = Field(description="Highest tank level reached before the repair ends, %.")


class LongRunAssessment(ApiModel):
    """Why the long-run plan differs from the cheapest one (plan.md "Cheapest now vs best in the long run")."""

    extra_cost_now: float = Field(description="Long-run plan cost minus cheapest plan cost, $.")
    expected_saving: float = Field(description="Expected $ saved later = risk of another failure × cost avoided.")
    reasons: list[str] = Field(description="Plain-English reasons, each backed by data (e.g. 'S2 lost 2 pumps in 30 min').")
    recommend: bool = Field(description="True only if expected_saving > extra_cost_now.")


class SimTrace(ApiModel):
    """A simulated tank-level curve for the what-if chart."""

    label: str = Field(description="Legend label, e.g. 'Do nothing' or 'Cheapest plan'.")
    points: list[list[float]] = Field(description="[[unix seconds, level %], ...] in time order.")
    warning_pct: float = Field(description="Warning limit to draw, % (from network.json).")
    shutdown_pct: float = Field(description="Shutdown limit to draw, % (from network.json).")


class PlanResponse(ApiModel):
    """Everything the plan panel needs for one selected pump failure."""

    event_id: str = Field(description="The selected event.")
    at_unix: int = Field(description="Plan start time ('now' or the time-machine position), unix seconds.")
    cheapest: Plan = Field(description="Exact SPEC optimum. Always present; matched against the answer key.")
    long_run: Plan | None = Field(default=None, description="Long-run plan, only when it differs from cheapest.")
    long_run_assessment: LongRunAssessment | None = Field(
        default=None, description="Why the long-run plan is suggested. Present whenever long_run is."
    )
    do_nothing: Plan = Field(description="Cost of letting the tank trip.")
    alternatives: list[Plan] = Field(description="Next-cheapest plans for 'Show your work', sorted by cost.")
    traces: list[SimTrace] = Field(description="Tank curves: do nothing, cheapest, and long run if present.")


class SimulateRequest(ApiModel):
    """A custom what-if plan from the UI, simulated with the same rules as the optimizer."""

    event_id: str = Field(description="Pump failure to simulate.")
    at_unix: int | None = Field(default=None, description="Plan start, unix seconds. Null = the dataset's 'now'.")
    actions: list[PlanAction] = Field(description="The plan to simulate.")


# ---------------------------------------------------------------------------
# Savings replay
# ---------------------------------------------------------------------------


class ReplayTotals(ApiModel):
    """Week totals for the three replay scenarios, in $."""

    did_nothing: float = Field(description="Revenue lost in the recording, where nobody acted, $.")
    cheapest: float = Field(description="Total cost with the cheapest plan per event, $.")
    long_run: float = Field(description="Total cost with our long-run planner, $.")


class ReplayRow(ApiModel):
    """One pump failure in the savings replay table."""

    event_id: str = Field(description="Event ID. Clicking the row jumps the time machine to its onset.")
    facility_id: str = Field(description="Facility.")
    onset_unix: int = Field(description="When the failure started, unix seconds.")
    did_nothing_cost: float = Field(description="Revenue lost in the recording for this event, $.")
    plan_cost: float = Field(description="Cost of our plan for this event (mode chosen in the request), $.")
    saved: float = Field(description="did_nothing_cost minus plan_cost, $.")
    plan_label: str = Field(description="One-line summary of our plan.")


class ReplayPoint(ApiModel):
    """Cumulative cost at one moment, for the replay chart (three lines)."""

    t_unix: int = Field(description="Time, unix seconds.")
    did_nothing: float = Field(description="Cumulative did-nothing cost so far, $.")
    cheapest: float = Field(description="Cumulative cheapest-plan cost so far, $.")
    long_run: float = Field(description="Cumulative long-run cost so far, $.")


class ReplayResult(ApiModel):
    """The savings replay page: what Nightwatch would have saved over the whole window."""

    dataset_id: str = Field(description="Dataset replayed.")
    mode: Literal["cheapest", "long_run"] = Field(description="Which planner the per-event rows use.")
    totals: ReplayTotals = Field(description="Three headline totals.")
    rows: list[ReplayRow] = Field(description="One row per pump failure, in onset order.")
    cumulative: list[ReplayPoint] = Field(description="Cumulative cost curve over the window.")


# ---------------------------------------------------------------------------
# events.json export (SPEC "events.json (optional)")
# ---------------------------------------------------------------------------


class ExportEvent(ApiModel):
    """One event in the SPEC events.json shape, for the export download."""

    event_id: str = Field(description="Event ID.")
    system_id: str = Field(description="System ID.")
    facility_id: str = Field(description="Facility ID.")
    asset_id: str = Field(description="Asset ID.")
    category: EventCategory = Field(description="Problem type.")
    status: EventStatus = Field(description="ongoing or resolved.")
    onset_ts: str = Field(description="Onset as ISO 8601 UTC, e.g. '2026-10-11T21:00:00Z'.")
    onset_unix: int = Field(description="Onset, unix seconds.")
    end_ts: str | None = Field(default=None, description="End as ISO 8601 UTC. Required when resolved.")
    end_unix: int | None = Field(default=None, description="End, unix seconds. Required when resolved.")
    confidence: float = Field(ge=0, le=1, description="Detector confidence, 0-1.")
    evidence: list[str] = Field(description="Plain-English reasons.")
    level_pct: float | None = Field(default=None, description="Tank level %, pump failures only.")
    hours_to_limit: HoursToLimit | None = Field(default=None, description="Countdown, ongoing pump failures only.")
    recommended_actions: list[PlanAction] = Field(description="The recommended plan's actions.")


# ---------------------------------------------------------------------------
# Sentinel agent
# ---------------------------------------------------------------------------


class ToolCall(ApiModel):
    """One tool Sentinel called during a step, shown in the expandable trace."""

    name: str = Field(description="Tool name, e.g. 'optimize_plan'.")
    args: dict[str, Any] = Field(description="Arguments Sentinel passed.")
    result_summary: str = Field(description="Short summary of what the tool returned.")


class AgentStep(ApiModel):
    """One streamed step of a Sentinel run (one of the six workflow steps)."""

    run_id: str = Field(description="ID of the run this step belongs to.")
    index: int = Field(description="Position in the run, starting at 0.")
    step: AgentStepName = Field(description="Which workflow step this is.")
    status: Literal["running", "done", "error", "waiting_approval"] = Field(description="Step status.")
    title: str = Field(description="Short heading for the trace, e.g. 'Plan: 3 failures share 2 trucks'.")
    message: str = Field(description="What Sentinel concluded in this step, in plain English.")
    tool_calls: list[ToolCall] = Field(description="Tools called in this step, in order.")
    grounded: bool | None = Field(
        default=None, description="True if every number in message came from a tool result (grounding check)."
    )
    elapsed_s: float = Field(description="Seconds since the run started.")


class DraftAction(ApiModel):
    """An action Sentinel drafted that waits in the approval queue for the operator."""

    id: str = Field(description="Draft ID, used when approving or rejecting.")
    kind: ActionType = Field(description="What the draft would do.")
    label: str = Field(description="One-line description for the approval card.")
    action: PlanAction = Field(description="The exact action that runs on approval.")
    event_id: str = Field(description="Event this action responds to.")
    status: Literal["pending", "approved", "rejected"] = Field(description="Approval status.")


class AgentRunResult(ApiModel):
    """A complete Sentinel run: all steps, the drafted actions and the shift handoff."""

    run_id: str = Field(description="Run ID.")
    steps: list[AgentStep] = Field(description="All six steps, in order.")
    drafts: list[DraftAction] = Field(description="Actions waiting for operator approval.")
    handoff: str | None = Field(default=None, description="30-second shift handoff text from the report step.")


class ApproveRequest(ApiModel):
    """Operator decision on one drafted action."""

    run_id: str = Field(description="Run the draft belongs to.")
    action_id: str = Field(description="DraftAction.id to approve or reject.")
    approve: bool = Field(description="True = approve (Sentinel then notifies the field crew), False = reject.")
