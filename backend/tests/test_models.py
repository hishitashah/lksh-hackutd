"""Contract tests (T0.2 / T0.3): fixtures and the organizers' input files match our models.

Why this matters: the models are shared by all four teammates and the generated TypeScript
types. If a fixture or an input file stops matching, these tests fail before anyone builds
on a broken shape.
"""

from __future__ import annotations

import json

import pytest

from nightwatch.config import REPO_ROOT
from nightwatch.mocks import FIXTURE_MODELS, FIXTURES_DIR, mock_events_export
from nightwatch.models import Network, Ops

# Every dataset folder shipped with the repo (history/*). test/ arrives at judging.
HISTORY_DIRS = sorted(p for p in (REPO_ROOT / "history").iterdir() if (p / "network.json").is_file())


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_fixture_matches_model(name: str) -> None:
    """Each fixture file validates against its model (unknown keys are rejected)."""
    data = json.loads((FIXTURES_DIR / name).read_text())
    FIXTURE_MODELS[name].model_validate(data)


def test_every_fixture_file_is_registered() -> None:
    """No orphan fixtures: every JSON file in fixtures/ has a model in FIXTURE_MODELS."""
    on_disk = {p.name for p in FIXTURES_DIR.glob("*.json")}
    assert on_disk == set(FIXTURE_MODELS)


def test_events_export_builds() -> None:
    """The mock events.json export (built from state + plan fixtures) validates and covers every event."""
    exported = mock_events_export()
    assert len(exported) > 0
    assert all(e.onset_ts.endswith("Z") for e in exported)


@pytest.mark.parametrize("folder", HISTORY_DIRS, ids=lambda p: p.name)
def test_input_files_parse(folder) -> None:
    """network.json and ops.json in every history folder parse into our models.

    Also checks the cross-file links the planner relies on: every facility in network.json
    has truck travel times in ops.json.
    """
    network = Network.model_validate(json.loads((folder / "network.json").read_text()))
    ops = Ops.model_validate(json.loads((folder / "ops.json").read_text()))
    assert network.defaults.tank_limits_pct.shutdown > network.defaults.tank_limits_pct.warning
    for system in network.systems:
        assert system.id in ops.systems, f"{system.id} missing from ops.json"
        for facility in system.facilities:
            assert facility.id in ops.systems[system.id].facilities, f"{facility.id} missing from ops.json"
