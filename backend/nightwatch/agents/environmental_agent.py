"""Environmental Impact Agent (ARCHITECTURE.md §6, Phase 4).

Responsibility
--------------
How could each incident, and each way of handling it, affect the environment? The cheapest option shouldn't win if
it brings unacceptable environmental consequences (§6.1), so this agent rates every option the planner produced.

What the data supports (and what it doesn't)
--------------------------------------------
There are **no environmental sensors** in the dataset (no soil, water or wildlife readings). Everything here comes
from the flow, pressure and tank telemetry, the network files and SPEC's equipment rules:

* **Pipeline leak:** water leaves the pump but doesn't arrive at the disposal site. The agent measures how much:
  *unaccounted water* = the pump-flow minus receive-flow excess (above the asset's normal gap, ``baseline.py``),
  added up over the readings taken while pumping. That is an **observed condition**. Where the water went is
  **not confirmed**: no sensor measures it, so the agent never says a spill occurred (§6.4).
* **Pump failure:** the tank fills, but SPEC says that at the shutdown limit the wells shut in automatically and
  nothing overflows. Containment holds by design; overflow is only a **possible consequence** if that safety
  system failed, which isn't monitored (an uncertainty).
* **Worn pump:** no release; the possible consequence is a later failure, which is then contained.
* **Interventions:** trucks move produced water on roads (exposure = number of truck trips; a road spill is
  possible but not observed); shut-ins move no water; doing nothing on a pump failure ends in an automatic shut-in.
  Delaying a leak inspection adds unaccounted water at the current rate.

Risk levels (criteria in ``config.py``; §6.3 "only defined criteria")
---------------------------------------------------------------------
* ``low``: no loss of containment observed (pump failures, worn pumps, and every option for a pump failure).
* ``moderate``: a leak is observed, below the high thresholds.
* ``high``: a leak with unaccounted water ≥ ``env_leak_high_bbl`` or a missing rate ≥ ``env_leak_high_rate_bpd``.
* ``critical``: **never assigned here**: it's reserved for a confirmed release, which these sensors can't confirm.

Numbers are deterministic; Nemotron only writes the explanation (``nightwatch/llm.py``), and ``llm_used`` says
whether it did.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import get_settings
from ..llm import explain
from ..models import AgentAssessment, Event, OptionAssessment, RiskLevel
from ..monitoring.anomaly_detector import fmt_time
from ..monitoring.baseline import pump_baseline, running_mask
from ..monitoring.telemetry_processor import Dataset
from ..planning.intervention_planner import Candidate
from .base_agent import BaseAgent, IncidentContext

RISK_ORDER: tuple[RiskLevel, ...] = ("low", "moderate", "high", "critical")
MAX_PLAN_OPTIONS = 3  # per pump failure: the selected plan, up to 2 alternatives, and doing nothing


@dataclass(frozen=True)
class LeakVolume:
    """How much water a leak has left unaccounted for, measured from telemetry."""

    unaccounted_bbl: float  # added up over the readings taken while pumping, since onset
    rate_bpd: float  # current missing rate: average of the latest pumping readings
    pumping_hours: float  # hours of pumping since onset (a leak can only be measured while the pump runs)
    pressure_drop_psi: float  # pipeline pressure below its normal level, latest pumping readings
    last_pumping_unix: int | None


def unaccounted_water(ds: Dataset, event: Event, at: int) -> LeakVolume:
    """Water that left the pump but didn't arrive, since the leak's onset (deterministic, from telemetry).

    For each 5-minute reading while the pump runs: excess = (pump flow − receive flow) − the asset's normal gap,
    floored at 0; volume = Σ excess × (step ÷ 1 day). Readings with the pump stopped carry no flow, so no loss.
    """
    facility = ds.facilities[event.facility_id]
    base = pump_baseline(ds, facility)
    if base is None:
        return LeakVolume(0.0, 0.0, 0.0, 0.0, None)
    mask = running_mask(ds, facility) & (ds.times >= event.onset_unix) & (ds.times <= at)
    if not mask.any():
        return LeakVolume(0.0, 0.0, 0.0, 0.0, None)
    gap = ds.series(facility.pump.id, "flow_bpd")[mask] - ds.series(facility.pipeline.id, "receive_flow_bpd")[mask]
    excess = np.clip(gap - base.flow_gap_bpd.median, 0.0, None)
    window = get_settings().detect_window_samples
    pressure = ds.series(facility.pipeline.id, "pressure_psi")[mask]
    return LeakVolume(
        unaccounted_bbl=round(float(np.nansum(excess)) * ds.step_s / 86400, 1),
        rate_bpd=round(float(np.nanmean(excess[-window:])), 1),
        pumping_hours=round(int(mask.sum()) * ds.step_s / 3600, 1),
        pressure_drop_psi=round(base.pipeline_pressure_psi.median - float(np.nanmean(pressure[-window:])), 1),
        last_pumping_unix=int(ds.times[mask][-1]),
    )


def leak_risk(volume: LeakVolume) -> RiskLevel:
    """Risk level of an observed leak, from the documented thresholds in ``config.py``."""
    cfg = get_settings()
    if volume.unaccounted_bbl >= cfg.env_leak_high_bbl or volume.rate_bpd >= cfg.env_leak_high_rate_bpd:
        return "high"
    return "moderate"


def highest(levels: list[RiskLevel]) -> RiskLevel:
    """The most serious level in a list (``low`` for an empty list)."""
    return max(levels, key=RISK_ORDER.index) if levels else "low"


class EnvironmentalAgent(BaseAgent):
    """Rates the environmental consequences of each incident and each intervention option."""

    name = "environment"
    responsibility = "Environmental consequences of incidents and interventions (ARCHITECTURE.md §6)."

    async def assess(self, ctx: IncidentContext) -> AgentAssessment:
        """Build the structured environmental assessment for every ongoing incident."""
        ds = ctx.dataset
        cfg = get_settings()
        shutdown = ds.network.defaults.tank_limits_pct.shutdown
        findings: list[str] = []
        evidence: list[str] = []
        options: list[OptionAssessment] = []
        safeguards: list[str] = []
        suggested: list[tuple[int, float, str]] = []  # (risk rank, bbl, label) to order the inspections
        levels: list[RiskLevel] = []
        uncertainties = [
            "The dataset has no environmental sensors (soil, water or wildlife), so impacts are inferred from flow, "
            "pressure and tank readings only.",
            "'critical' is reserved for a confirmed release; these sensors can't confirm one, so it is never assigned.",
        ]
        leaks = [e for e in ctx.incidents if e.category == "pipeline_leak"]
        worn = [e for e in ctx.incidents if e.category == "pump_degradation"]
        failures = ctx.failures()
        total_bbl = max_rate = 0.0

        # --- Pipeline leaks: the only observed loss of containment in this data ---------------------------
        for e in leaks:
            v = unaccounted_water(ds, e, ctx.at_unix)
            risk = leak_risk(v)
            levels.append(risk)
            total_bbl += v.unaccounted_bbl
            max_rate = max(max_rate, v.rate_bpd)
            per_hour = round(v.rate_bpd / 24, 1)
            findings += [
                f"Observed: about {v.unaccounted_bbl:g} bbl of water left the pump but did not arrive through "
                f"{e.asset_id} since {fmt_time(e.onset_unix)} ({v.pumping_hours:g} h of pumping); "
                f"currently about {v.rate_bpd:g} bpd missing.",
                f"Possible consequence: produced water escaping containment along {e.asset_id}; each hour of delay "
                f"adds about {per_hour:g} bbl while the pump runs.",
                f"Not confirmed: where the water from {e.asset_id} went. No sensor measures it, so no spill is confirmed.",
            ]
            evidence += [
                f"{e.asset_id}: pipeline pressure {v.pressure_drop_psi:g} psi below normal while pumping (telemetry).",
                f"{e.asset_id}: {v.unaccounted_bbl:g} bbl unaccounted, {v.rate_bpd:g} bpd missing now (telemetry).",
            ]
            if v.last_pumping_unix is not None and ctx.at_unix - v.last_pumping_unix > 3600:
                uncertainties.append(
                    f"{e.asset_id} was last measured while pumping at {fmt_time(v.last_pumping_unix)}; "
                    "the volume is only updated when the pump runs."
                )
            if risk != "high" and per_hour > 0:
                hours_to_high = (cfg.env_leak_high_bbl - v.unaccounted_bbl) / per_hour
                delay_note = (
                    f"Adds about {per_hour:g} bbl per hour of pumping; reaches the {cfg.env_leak_high_bbl:g} bbl "
                    f"'high' threshold after about {hours_to_high:.1f} more hours of pumping."
                )
            else:
                delay_note = f"Adds about {per_hour:g} bbl per hour of pumping to the {v.unaccounted_bbl:g} bbl so far."
            label = f"Send inspector to {e.asset_id}"
            options += [
                OptionAssessment(
                    plan_label=label,
                    event_id=e.id,
                    risk_level=risk,
                    note="Inspecting now stops further unaccounted water soonest.",
                ),
                OptionAssessment(
                    plan_label=f"Delay the inspection of {e.asset_id}", event_id=e.id, risk_level=risk, note=delay_note
                ),
            ]
            safeguards += [
                f"Inspect {e.asset_id} first: about {per_hour:g} bbl more becomes unaccounted each hour the pump runs.",
                f"Give the inspection crew the leak's start ({fmt_time(e.onset_unix)}) and the volume estimate "
                f"({v.unaccounted_bbl:g} bbl) so they know where to look.",
            ]
            suggested.append((RISK_ORDER.index(risk), v.unaccounted_bbl, label))

        # --- Worn pumps: no release; may progress to a failure -----------------------------------------------
        for e in worn:
            levels.append("low")
            findings += [
                f"Observed: {e.asset_id} is moving less water than normal, with no water missing downstream.",
                f"Possible consequence: {e.asset_id} may fail; a failure fills the tank, which the automatic "
                "shut-in contains.",
            ]
            evidence += [f"{e.asset_id}: {line} (detector)." for line in e.evidence[:2]]
            label = f"Send inspector to {e.asset_id}"
            options += [
                OptionAssessment(
                    plan_label=label,
                    event_id=e.id,
                    risk_level="low",
                    note="Finds the problem before it becomes a pump failure.",
                ),
                OptionAssessment(
                    plan_label=f"Delay the inspection of {e.asset_id}",
                    event_id=e.id,
                    risk_level="low",
                    note="No release expected; the risk is a later pump failure, which is contained.",
                ),
            ]
            safeguards.append(f"Inspect {e.asset_id} before it fails.")
            suggested.append((0, 0.0, label))

        # --- Pump failures: contained by the automatic shut-in (SPEC) --------------------------------------
        truck_trips = 0
        for e in failures:
            levels.append("low")
            level_txt = f"{e.level_pct:g}%" if e.level_pct is not None else "unknown level"
            hours = f"{e.hours_to_limit.p50:g} h" if e.hours_to_limit is not None else "no countdown"
            findings += [
                f"Observed: {e.facility_id}'s tank is at {level_txt} and filling; shutdown at {shutdown:g}% in {hours}.",
                f"Possible consequence: overflow at {e.facility_id} only if the automatic shut-in failed; by design "
                f"the wells stop at {shutdown:g}% and nothing overflows (SPEC).",
            ]
            evidence.append(f"SPEC: at the {shutdown:g}% shutdown limit the wells shut in automatically; nothing overflows.")
            sp = ctx.plans.get(e.system_id)
            if sp is None or e.id not in sp.selection:
                continue
            chosen = sp.selection[e.id]
            if chosen.trucks:
                truck_trips += chosen.trucks.trucks * chosen.trucks.trips_per_truck
            for c in _plan_options(sp.options[e.id].candidates, chosen, sp.options[e.id].do_nothing):
                options.append(
                    OptionAssessment(
                        plan_label=c.label,
                        event_id=e.id,
                        risk_level="low",
                        note=_plan_note(c, shutdown, ds.ops.emergency_trucking.truck_capacity_bbl),
                    )
                )
            safeguards.append(
                f"Keep {e.facility_id}'s tank under its {shutdown:g}% limit with the selected plan; the automatic "
                "shut-in is the last line of containment."
            )
            suggested.append((0, 0.0, chosen.label))
        if truck_trips:
            safeguards.append(
                f"Truck crews: confirm each load ({ds.ops.emergency_trucking.truck_capacity_bbl:g} bbl per truck trip) "
                "reaches the disposal site."
            )
        if failures:
            uncertainties.append(
                "The automatic shut-in is assumed to work as SPEC describes; it isn't monitored separately."
            )
        if leaks:
            uncertainties.append("A leak can only be measured while its pump runs; volumes between pump runs are not seen.")

        overall = highest(levels)
        if not ctx.incidents:
            findings = ["Observed: no ongoing incidents, so no environmental concerns are observed at this time."]
        metrics = {
            "leaks": float(len(leaks)),
            "worn_pumps": float(len(worn)),
            "pump_failures": float(len(failures)),
            "unaccounted_water_bbl": round(total_bbl, 1),
            "max_missing_rate_bpd": round(max_rate, 1),
            "truck_trips_planned": float(truck_trips),
        }
        top_leak = max(leaks, key=lambda e: unaccounted_water(ds, e, ctx.at_unix).unaccounted_bbl, default=None)
        if top_leak is not None:
            v = unaccounted_water(ds, top_leak, ctx.at_unix)
            headline = f"{overall.capitalize()}: about {v.unaccounted_bbl:g} bbl unaccounted on {top_leak.asset_id}"
        else:
            headline = f"{overall.capitalize()}: no loss of containment observed"
        fallback = _fallback(overall, leaks, failures, shutdown, total_bbl, max_rate)
        text = await explain(
            "environmental impact agent",
            "Explain the environmental risk of these incidents and options. Separate what is observed from what is "
            "only possible, and never say a spill occurred or is confirmed.",
            {
                "risk_level": overall,
                "findings": findings,
                "metrics": metrics,
                "safeguards": safeguards,
                "uncertainties": uncertainties,
            },
            fallback,
        )
        return AgentAssessment(
            agent="environment",
            incident_ids=ctx.incident_ids(),
            status="completed",
            assessment=headline,
            risk_level=overall,
            findings=findings,
            evidence=evidence,
            metrics=metrics,
            options=options,
            uncertainties=uncertainties,
            suggested_interventions=[label for *_, label in sorted(suggested, key=lambda s: (-s[0], -s[1]))],
            safeguards=safeguards,
            explanation=text.text,
            llm_used=text.llm_used,
        )


def _plan_options(candidates: list[Candidate], chosen: Candidate, do_nothing: Candidate) -> list[Candidate]:
    """The selected plan, a couple of alternatives, and doing nothing (the options the orchestrator will compare)."""
    picked = [chosen]
    for c in candidates:
        if len(picked) >= MAX_PLAN_OPTIONS:
            break
        if c.label not in {p.label for p in picked} and c is not do_nothing:
            picked.append(c)
    if do_nothing.label not in {p.label for p in picked}:
        picked.append(do_nothing)
    return picked


def _plan_note(c: Candidate, shutdown: float, truck_bbl: float) -> str:
    """Environmental note for one pump-failure option (all are low risk: containment holds by design)."""
    parts: list[str] = []
    if c.trucks:
        trips = c.trucks.trucks * c.trucks.trips_per_truck
        parts.append(
            f"{trips} truck trip{'s move' if trips > 1 else ' moves'} {trips * truck_bbl:g} bbl of produced water on roads; "
            "a road spill is possible but not observed"
        )
    if c.shut_ins:
        parts.append("shut-in wells stop producing water, so none is moved")
    if not parts:
        parts.append("no water is moved")
    if c.outcome.tank_trips:
        parts.append(f"the tank reaches {shutdown:g}% and the automatic shut-in keeps the water contained")
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def _fallback(
    overall: RiskLevel, leaks: list[Event], failures: list[Event], shutdown: float, total_bbl: float, max_rate: float
) -> str:
    """Deterministic explanation used when Nemotron is unavailable (and as its reference)."""
    parts = [f"Overall environmental risk is {overall}."]
    if leaks:
        parts.append(
            f"{len(leaks)} leak{'s' if len(leaks) > 1 else ''} left about {total_bbl:g} bbl of water unaccounted for "
            f"(observed), with up to {max_rate:g} bpd still missing while pumping; where it went isn't measured, so "
            "no spill is confirmed. Inspect the leaks first."
        )
    if failures:
        parts.append(
            f"The {len(failures)} pump failure{'s' if len(failures) > 1 else ''} are contained by design: at "
            f"{shutdown:g}% the wells shut in automatically, so their options carry low environmental risk; trucking "
            "adds road trips but no observed release."
        )
    if not leaks and not failures:
        parts.append("No loss of containment is observed.")
    return " ".join(parts)
