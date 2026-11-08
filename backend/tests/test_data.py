"""Loader tests (ARCHITECTURE.md §3.1, §3.3 steps 1-3): history folders load; bad files fail clearly.

What's covered:
* All five history folders load in under 3 s, "now" is the last telemetry row, and the series line up.
* The time range and reading interval come from the data (nothing hardcoded), including for a
  made-up dataset whose interval isn't 5 minutes.
* ``index_at`` / ``value_at`` / ``window`` never look past the requested time (the detector relies on this).
* Malformed telemetry raises ``ValueError`` naming the problem; soft problems become ``warnings``.
* The cache reuses a loaded dataset and reloads it when a file changes.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nightwatch import api
from nightwatch.config import REPO_ROOT, get_settings
from nightwatch.data import (
    UnknownDatasetError,
    expected_series,
    get_dataset,
    load_dataset,
    scan_datasets,
    series_key,
)

# Every dataset folder shipped with the repo (history/*). test/ arrives at judging.
HISTORY_DIRS = sorted(p for p in (REPO_ROOT / "history").iterdir() if (p / "network.json").is_file())
FULL_WEEK = REPO_ROOT / "history" / "full_week"

# Every history folder must load in under this many seconds (keeps the dataset switcher snappy).
LOAD_BUDGET_S = 3.0


def _write_dataset(folder: Path, rows: list[tuple[int, str, str, str]]) -> Path:
    """Create a tiny dataset folder: real network.json/ops.json plus the given telemetry rows.

    Args:
        folder: Folder to create.
        rows: ``(ts_unix, asset_id, metric, value)`` tuples; ``value`` is written as given so tests can
            plant bad values.

    Returns:
        The folder, ready to pass to ``load_dataset``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("network.json", "ops.json"):
        shutil.copy(FULL_WEEK / name, folder / name)
    with open(folder / "telemetry.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "ts_unix", "asset_id", "metric", "value"])
        for ts, asset, metric, value in rows:
            w.writerow([f"t{ts}", ts, asset, metric, value])
    return folder


@pytest.fixture(scope="module")
def full_week():
    """The full_week dataset, loaded once for the read-only checks below."""
    return load_dataset(FULL_WEEK)


@pytest.mark.parametrize("folder", HISTORY_DIRS, ids=lambda p: p.name)
def test_history_folder_loads_fast_and_clean(folder: Path) -> None:
    """Each history folder loads in < 3 s, with every expected series and no data-quality warnings."""
    start = time.perf_counter()
    ds = load_dataset(folder)
    assert time.perf_counter() - start < LOAD_BUDGET_S
    assert ds.warnings == ()
    assert set(ds.values) == expected_series(ds.network)
    # Every series shares the time axis and has no gaps (SPEC: every sensor reports every interval).
    assert all(arr.shape == ds.times.shape for arr in ds.values.values())
    assert not any(np.isnan(arr).any() for arr in ds.values.values())


@pytest.mark.parametrize("folder", HISTORY_DIRS, ids=lambda p: p.name)
def test_now_is_last_row_and_grid_is_regular(folder: Path) -> None:
    """'Now' is the last ts_unix in the file (checked with an independent read), and the grid has no holes."""
    ds = load_dataset(folder)
    last_in_file = int(pd.read_csv(folder / "telemetry.csv", usecols=["ts_unix"])["ts_unix"].max())
    assert ds.now_unix == last_in_file == ds.times[-1]
    tr = ds.time_range
    assert tr.start_unix == ds.times[0] and tr.now_unix == last_in_file
    assert (tr.now_unix - tr.start_unix) // tr.step_s + 1 == len(ds.times)  # no missing intervals
    assert np.all(np.diff(ds.times) == tr.step_s)


def test_full_week_shape(full_week) -> None:
    """full_week is one week at 5-minute steps: 2,016 readings per series, 195 series (15 facilities)."""
    assert len(full_week.times) == 2016
    assert full_week.step_s == 300
    assert len(full_week.values) == 195
    assert len(full_week.facilities) == 15
    assert full_week.system_of["S1_FACILITY_A"] == "S1"


def test_a_snapshot_is_a_prefix_of_full_week(full_week) -> None:
    """README: a snapshot's telemetry is the first part of full_week's, so its 'now' is just earlier."""
    snap = load_dataset(REPO_ROOT / "history" / "snapshot_2026-10-08T1200")
    n = len(snap.times)
    assert snap.now_unix < full_week.now_unix
    assert np.array_equal(snap.times, full_week.times[:n])
    key = series_key("S2_TANK_B", "level_pct")
    np.testing.assert_array_equal(snap.values[key], full_week.values[key][:n])


def test_series_are_read_only(full_week) -> None:
    """The loaded dataset is cached and shared, so nobody may modify its arrays by accident."""
    with pytest.raises(ValueError):
        full_week.times[0] = 0
    with pytest.raises(ValueError):
        full_week.series("S2_TANK_B", "level_pct")[0] = 0.0


def test_index_at_never_looks_ahead(full_week) -> None:
    """index_at returns the last reading at or before t, clamped to the first and last readings."""
    t = full_week.times
    assert full_week.index_at(int(t[10])) == 10  # exactly on a reading
    assert full_week.index_at(int(t[10]) + 1) == 10  # between readings: the earlier one
    assert full_week.index_at(int(t[10]) - 1) == 9
    assert full_week.index_at(int(t[0]) - 10_000) == 0  # before the data: clamp
    assert full_week.index_at(int(t[-1]) + 10_000) == len(t) - 1  # after 'now': clamp


def test_value_at_matches_series(full_week) -> None:
    """value_at is the series value at index_at(t)."""
    arr = full_week.series("S1_TANK_A", "level_pct")
    t = int(full_week.times[100]) + 150
    assert full_week.value_at("S1_TANK_A", "level_pct", t) == arr[100]


def test_window_is_inclusive_and_ordered(full_week) -> None:
    """window(t0, t1) holds exactly the readings with t0 <= time <= t1."""
    t = full_week.times
    times, vals = full_week.window("S1_TANK_A", "level_pct", int(t[5]), int(t[9]))
    assert list(times) == list(t[5:10]) and len(vals) == 5
    empty_t, empty_v = full_week.window("S1_TANK_A", "level_pct", int(t[9]), int(t[5]))
    assert len(empty_t) == 0 and len(empty_v) == 0


def test_unknown_series_is_a_clear_keyerror(full_week) -> None:
    """Asking for a sensor that doesn't exist names it instead of failing obscurely."""
    assert not full_week.has_series("S1_TANK_A", "nope")
    with pytest.raises(KeyError, match=r"S1_TANK_A\.nope"):
        full_week.series("S1_TANK_A", "nope")


def test_interval_comes_from_the_data_not_a_constant(tmp_path: Path) -> None:
    """A dataset on a 10-minute grid reports step_s=600: nothing assumes 5 minutes."""
    rows = [(1_000_000 + i * 600, "S1_TANK_A", "level_pct", str(50 + i)) for i in range(6)]
    ds = load_dataset(_write_dataset(tmp_path / "ten_min", rows))
    assert ds.step_s == 600
    assert ds.time_range.start_unix == 1_000_000 and ds.time_range.now_unix == 1_000_000 + 5 * 600


def test_rows_may_arrive_out_of_order(tmp_path: Path) -> None:
    """Readings are placed by timestamp, so shuffled rows still give a sorted time axis."""
    rows = [(1_000_000 + i * 300, "S1_TANK_A", "level_pct", str(i)) for i in (3, 0, 2, 1)]
    ds = load_dataset(_write_dataset(tmp_path / "shuffled", rows))
    assert list(ds.times) == [1_000_000 + i * 300 for i in range(4)]
    assert list(ds.series("S1_TANK_A", "level_pct")) == [0.0, 1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda f: (f / "telemetry.csv").write_text("ts_unix,asset_id,metric,value\n1,A,m,1\n"), "missing column"),
        (lambda f: (f / "telemetry.csv").write_text("timestamp,ts_unix,asset_id,metric,value\n"), "no readings"),
        (
            lambda f: (f / "telemetry.csv").write_text(
                "timestamp,ts_unix,asset_id,metric,value\nx,100,A,m,1\nx,abc,A,m,2\n"
            ),
            "ts_unix",
        ),
        (
            lambda f: (f / "telemetry.csv").write_text(
                "timestamp,ts_unix,asset_id,metric,value\nx,100,A,m,1\nx,100,A,m,2\nx,400,A,m,3\n"
            ),
            "duplicate",
        ),
        (
            lambda f: (f / "telemetry.csv").write_text("timestamp,ts_unix,asset_id,metric,value\nx,100,A,m,1\n"),
            "two timestamps",
        ),
        (lambda f: (f / "network.json").write_text("{not json"), "network.json"),
        (lambda f: (f / "ops.json").write_text("{}"), "ops.json"),
        (lambda f: (f / "ops.json").unlink(), "missing"),
    ],
    ids=["missing-column", "no-rows", "bad-timestamp", "duplicate", "one-timestamp", "bad-network", "bad-ops", "no-ops"],
)
def test_bad_dataset_fails_with_a_clear_message(tmp_path: Path, mutate, message: str) -> None:
    """Each kind of broken input raises ValueError whose text names the file or the problem."""
    folder = _write_dataset(tmp_path / "bad", [(100, "S1_TANK_A", "level_pct", "50"), (400, "S1_TANK_A", "level_pct", "51")])
    mutate(folder)
    with pytest.raises(ValueError, match=message):
        load_dataset(folder)


def test_soft_problems_become_warnings_not_errors(tmp_path: Path) -> None:
    """A bad value, a missing series and an unknown asset still load, and each is reported in warnings."""
    rows = [
        (100, "S1_TANK_A", "level_pct", "50"),
        (400, "S1_TANK_A", "level_pct", "oops"),  # non-numeric -> missing reading
        (700, "S1_TANK_A", "level_pct", "52"),
        (100, "GHOST_ASSET", "level_pct", "1"),  # not in network.json
    ]
    ds = load_dataset(_write_dataset(tmp_path / "soft", rows))
    text = " | ".join(ds.warnings)
    assert "non-numeric" in text and "have no telemetry" in text and "aren't described by network.json" in text
    assert np.isnan(ds.series("S1_TANK_A", "level_pct")[1])  # the bad value is NaN, not a crash or a zero
    assert ds.series("GHOST_ASSET", "level_pct")[0] == 1.0  # unknown series are kept, just flagged


# ---------------------------------------------------------------------------
# Scanning + cache (these read DATA_DIRS, so point it at a temp folder)
# ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    """An empty temp folder that DATA_DIRS points at, so tests can add datasets and watch them appear."""
    monkeypatch.setenv("DATA_DIRS", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_new_folder_shows_up_without_restart(data_dir: Path) -> None:
    """Scanning runs on every call, so a dataset dropped in later is found immediately."""
    assert scan_datasets() == {}
    _write_dataset(data_dir / "later", [(100, "S1_TANK_A", "level_pct", "50"), (400, "S1_TANK_A", "level_pct", "51")])
    assert list(scan_datasets()) == ["later"]


def test_get_dataset_caches_and_reloads_on_change(data_dir: Path) -> None:
    """Same files -> the same object; editing network.json (e.g. a different tank limit) -> a fresh load."""
    folder = _write_dataset(data_dir / "ds", [(100, "S1_TANK_A", "level_pct", "50"), (400, "S1_TANK_A", "level_pct", "51")])
    first = get_dataset("ds")
    assert get_dataset("ds") is first

    network = json.loads((folder / "network.json").read_text())
    network["defaults"]["tank_limits_pct"]["shutdown"] = 77
    (folder / "network.json").write_text(json.dumps(network))
    reloaded = get_dataset("ds")
    assert reloaded is not first
    assert reloaded.network.defaults.tank_limits_pct.shutdown == 77


def test_unknown_dataset_raises(data_dir: Path) -> None:
    """An ID with no folder raises UnknownDatasetError (the API turns it into a 404)."""
    with pytest.raises(UnknownDatasetError):
        get_dataset("nope")


# ---------------------------------------------------------------------------
# The API route that now uses the loader
# ---------------------------------------------------------------------------


def test_network_route_reports_422_for_a_broken_dataset(data_dir: Path) -> None:
    """A dataset folder whose files can't be parsed gives a readable 422, not a 500."""
    folder = _write_dataset(
        data_dir / "broken", [(100, "S1_TANK_A", "level_pct", "50"), (400, "S1_TANK_A", "level_pct", "51")]
    )
    (folder / "ops.json").write_text("{}")
    resp = TestClient(api.app).get("/api/broken/network")
    assert resp.status_code == 422
    assert "ops.json" in resp.json()["detail"]
