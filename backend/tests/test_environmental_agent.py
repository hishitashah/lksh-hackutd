"""Environmental Impact Agent tests (ARCHITECTURE.md §6, §17 "Agent tests").

Checks the §6.4 rules: observed / possible / not-confirmed are kept apart, no spill is ever claimed, no measurement
is invented, risk levels follow the documented thresholds in config.py, every option gets a risk note, and Nemotron
is only used for grounded explanations. No live LLM: ``llm.set_llm_override`` stands in for Nemotron.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from nightwatch import llm
from nightwatch.agents.base_agent import build_context
from nightwatch.agents.environmental_agent import EnvironmentalAgent, unaccounted_water
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.models import AgentAssessment
from nightwatch.monitoring.anomaly_detector import detect
from nightwatch.monitoring.baseline import pump_baseline, running_mask
from nightwatch.monitoring.telemetry_processor import load_dataset

HISTORY = REPO_ROOT / "history"
THURSDAY = HISTORY / "snapshot_2026-10-08T1200"
FRIDAY_18 = 1791568800  # Fri 18:00 in full_week: S1/S2 leaks ~14 h and ~8 h in


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """No NVIDIA key and no override: nothing calls the real API."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    get_settings.cache_clear()
    llm.set_llm_override(None)
    yield
    llm.set_llm_override(None)
    get_settings.cache_clear()


def run_env(folder=THURSDAY, at=None) -> AgentAssessment:
    """Run the environmental agent on a dataset at a time (None = 'now')."""
    return asyncio.run(EnvironmentalAgent().run(build_context(load_dataset(folder), at)))


def test_returns_a_structured_assessment() -> None:
    """Thu 12:00: completed, with a defined risk level and all §6.4 parts present."""
    a = run_env()
    assert a.agent == "environment" and a.status == "completed"
    assert a.risk_level in ("low", "moderate", "high")
    assert a.findings and a.evidence and a.options and a.safeguards and a.uncertainties and a.explanation


def test_observed_possible_and_not_confirmed_are_separate() -> None:
    """§6.4: every finding says whether it's observed, only possible, or not confirmed."""
    a = run_env()
    assert all(f.startswith(("Observed:", "Possible consequence:", "Not confirmed:")) for f in a.findings)
    assert any(f.startswith("Not confirmed:") and "S5_PIPELINE_B" in f for f in a.findings)


def test_never_claims_a_spill_or_critical() -> None:
    """§6.4: an abnormal reading is never reported as a spill; 'critical' needs a confirmed release."""
    for folder, at in ((THURSDAY, None), (HISTORY / "full_week", FRIDAY_18)):
        a = run_env(folder, at)
        assert a.risk_level != "critical"
        text = " ".join(a.findings + a.safeguards + [a.explanation]).lower()
        text = text.replace("no spill is confirmed", "")  # the agent's required negation is fine
        for claim in ("spill occurred", "spill is confirmed", "confirmed spill", "spilled", "contamination occurred"):
            assert claim not in text, claim


def test_unaccounted_water_is_measured_not_invented() -> None:
    """The leak volume equals an independent sum over the telemetry, and the metric reports exactly that."""
    ds = load_dataset(THURSDAY)
    leak = next(e for e in detect(ds) if e.category == "pipeline_leak" and e.status == "ongoing")
    f = ds.facilities[leak.facility_id]
    mask = running_mask(ds, f) & (ds.times >= leak.onset_unix)
    gap = ds.series(f.pump.id, "flow_bpd")[mask] - ds.series(f.pipeline.id, "receive_flow_bpd")[mask]
    expected = np.clip(gap - pump_baseline(ds, f).flow_gap_bpd.median, 0, None).sum() * ds.step_s / 86400
    v = unaccounted_water(ds, leak, ds.now_unix)
    assert v.unaccounted_bbl == pytest.approx(expected, abs=0.1)
    assert run_env().metrics["unaccounted_water_bbl"] == v.unaccounted_bbl
    assert set(run_env().metrics) == {
        "leaks",
        "worn_pumps",
        "pump_failures",
        "unaccounted_water_bbl",
        "max_missing_rate_bpd",
        "truck_trips_planned",
    }


def test_leak_risk_follows_the_documented_thresholds(monkeypatch) -> None:
    """Thu 12:00: the S5 leak (~15 bbl) is moderate; Fri 18:00 a ~37 bbl leak is high; lowering the threshold
    turns Thursday high (no hidden constants)."""
    assert run_env().risk_level == "moderate"
    assert run_env(HISTORY / "full_week", FRIDAY_18).risk_level == "high"
    monkeypatch.setenv("ENV_LEAK_HIGH_BBL", "10")
    get_settings.cache_clear()
    assert run_env().risk_level == "high"


def test_pump_failures_are_low_with_the_containment_reason() -> None:
    """Pump failures are contained by the automatic shut-in (SPEC); every planned option is rated with a note."""
    a = run_env()
    ctx = build_context(load_dataset(THURSDAY))
    for eid in ctx.plans["S2"].selection:
        opts = [o for o in a.options if o.event_id == eid]
        assert opts and all(o.risk_level == "low" and o.note for o in opts)
        assert any(o.plan_label.startswith("Do nothing") for o in opts)
    assert any("automatic shut-in" in f and "nothing overflows" in f for f in a.findings)


def test_leak_options_compare_inspecting_and_delaying() -> None:
    """§6.3 B: delaying a leak inspection is rated, with how much water each hour of delay adds."""
    a = run_env()
    delay = next(o for o in a.options if o.plan_label == "Delay the inspection of S5_PIPELINE_B")
    assert "per hour" in delay.note and delay.risk_level == "moderate"
    assert a.suggested_interventions[0] == "Send inspector to S5_PIPELINE_B"


def test_no_incidents_means_no_concerns() -> None:
    """Mon 01:00, nothing has gone wrong: low risk, no invented options."""
    a = run_env(HISTORY / "full_week", 1791158400 + 3600)
    assert a.risk_level == "low" and a.options == []
    assert "no environmental concerns" in a.findings[0]


def test_grounded_nemotron_text_is_used() -> None:
    """Nemotron's explanation is used when its numbers all come from the facts."""
    llm.set_llm_override(lambda s, u: "Risk is moderate: about 14.9 bbl are unaccounted on S5_PIPELINE_B; inspect it first.")
    a = run_env()
    assert a.llm_used is True and "14.9 bbl" in a.explanation


def test_ungrounded_or_failing_nemotron_falls_back() -> None:
    """A made-up number, or an API error, falls back to the deterministic text with llm_used False."""
    llm.set_llm_override(lambda s, u: "About 500 bbl spilled.")
    assert run_env().llm_used is False

    def boom(s: str, u: str) -> str:
        raise ConnectionError("down")

    llm.set_llm_override(boom)
    a = run_env()
    assert a.status == "completed" and a.llm_used is False
