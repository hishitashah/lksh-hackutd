"""Telemetry ingestion and validation (ARCHITECTURE.md §3.1, §3.3 steps 1-3).

Where this sits in the app
--------------------------
Every other part of the backend reads sensor data through the ``Dataset`` object built here: the detector, the
countdown, the state service, the ``/series`` route and later the agents. A dataset is a folder holding
``network.json``, ``ops.json`` and ``telemetry.csv`` (README "What you get").

What happens on load
--------------------
1. **Validate**: the 5 columns must exist; non-numeric values and duplicate (time, asset, metric) rows are dropped
   and counted; the 5-minute grid is checked. Problems are reported, never silently ignored.
2. **Reshape**: one row per reading (~393k rows) becomes one time-aligned array per (asset, metric).
3. **Time range**: no file states it (SPEC "Time range"), so it comes from the first and last ``ts_unix``. The last
   one is "now" for this dataset; the system clock is never used.

Continuous processing (§3.1): the data here is a recording, so ``Dataset.replay`` walks it tick by tick, the same
way a live feed would arrive. This is a replay, not a live sensor connection.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import REPO_ROOT, get_settings
from ..models import Facility, Network, Ops, System, TimeRange

# The three files every dataset folder must contain (README "What you get").
DATASET_FILES = ("network.json", "ops.json", "telemetry.csv")

# telemetry.csv columns (SPEC "telemetry.csv").
REQUIRED_COLUMNS = ("timestamp", "ts_unix", "asset_id", "metric", "value")


class TelemetryValidationError(ValueError):
    """The dataset can't be used: a file is missing or the telemetry has the wrong columns."""


@dataclass
class ValidationReport:
    """What validation found while loading (kept so problems are visible, ARCHITECTURE.md §13)."""

    rows_read: int = 0
    rows_dropped_non_numeric: int = 0
    rows_dropped_duplicate: int = 0
    off_grid_timestamps: int = 0
    issues: list[str] = field(default_factory=list)


@dataclass(eq=False)
class Dataset:
    """One loaded dataset: the network, the ops rules and every sensor as a time-aligned array.

    Attributes:
        id: dataset ID used in URLs (folder name, e.g. 'full_week', 'test').
        folder: absolute folder path.
        network / ops: parsed network.json / ops.json.
        times: sorted unix seconds of every reading step (int64).
        step_s: seconds between steps (300 = 5 minutes).
        validation: what validation found.
    """

    id: str
    folder: Path
    network: Network
    ops: Ops
    times: np.ndarray
    step_s: int
    validation: ValidationReport
    _series: dict[tuple[str, str], np.ndarray] = field(repr=False)
    facilities: dict[str, Facility] = field(init=False, repr=False)
    systems: dict[str, System] = field(init=False, repr=False)
    system_of: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Build lookups so code can go from a facility ID to its system without scanning the network."""
        self.facilities = {f.id: f for s in self.network.systems for f in s.facilities}
        self.systems = {s.id: s for s in self.network.systems}
        self.system_of = {f.id: s.id for s in self.network.systems for f in s.facilities}

    # --- time ------------------------------------------------------------------

    @property
    def now_unix(self) -> int:
        """The dataset's 'now': its last telemetry timestamp (SPEC "Time range")."""
        return int(self.times[-1])

    @property
    def time_range(self) -> TimeRange:
        """First timestamp, 'now', and the step, as the API returns them."""
        return TimeRange(start_unix=int(self.times[0]), now_unix=self.now_unix, step_s=self.step_s)

    def clamp(self, at: int | None) -> int:
        """Turn an optional requested time into one inside the data window (None = 'now')."""
        if at is None:
            return self.now_unix
        return int(min(max(at, int(self.times[0])), self.now_unix))

    def index_at(self, at: int) -> int:
        """Index of the last reading step at or before ``at`` (-1 if ``at`` is before the data starts)."""
        return int(np.searchsorted(self.times, at, side="right")) - 1

    def replay(self, start: int | None = None, until: int | None = None, every_s: int | None = None) -> Iterator[int]:
        """Yield reading times in order, as a live feed would deliver them (ARCHITECTURE.md §3.1).

        Args:
            start / until: unix seconds bounds (default: the whole window).
            every_s: yield every Nth step only, e.g. 3600 for hourly checks (default: every step).
        """
        lo, hi = self.clamp(start if start is not None else int(self.times[0])), self.clamp(until)
        stride = max(1, (every_s or self.step_s) // self.step_s)
        for t in self.times[(self.times >= lo) & (self.times <= hi)][::stride]:
            yield int(t)

    # --- readings --------------------------------------------------------------

    def has(self, asset_id: str, metric: str) -> bool:
        """True if the telemetry has this (asset, metric) series."""
        return (asset_id, metric) in self._series

    def series(self, asset_id: str, metric: str) -> np.ndarray:
        """The full series for one sensor, aligned with ``times`` (NaN where a reading is missing)."""
        try:
            return self._series[(asset_id, metric)]
        except KeyError:
            raise KeyError(f"No telemetry for asset '{asset_id}' metric '{metric}'.") from None

    def value_at(self, asset_id: str, metric: str, at: int) -> float:
        """The latest reading of a sensor at or before ``at`` (NaN if there is none yet)."""
        i = self.index_at(at)
        return float(self.series(asset_id, metric)[i]) if i >= 0 else float("nan")

    def window(self, asset_id: str, metric: str, t0: int | None, t1: int | None) -> tuple[np.ndarray, np.ndarray]:
        """Readings of one sensor between ``t0`` and ``t1`` inclusive (None = start / 'now'): (times, values)."""
        lo = int(self.times[0]) if t0 is None else t0
        hi = self.now_unix if t1 is None else t1
        mask = (self.times >= lo) & (self.times <= hi)
        return self.times[mask], self.series(asset_id, metric)[mask]


# ---------------------------------------------------------------------------
# Finding and loading datasets
# ---------------------------------------------------------------------------


def is_dataset(folder: Path) -> bool:
    """True if ``folder`` contains network.json, ops.json and telemetry.csv."""
    return all((folder / name).is_file() for name in DATASET_FILES)


def scan_datasets() -> dict[str, Path]:
    """Find every dataset folder under the configured ``DATA_DIRS``.

    A top-level folder that is a dataset itself (``test/``) gets its own name as ID; otherwise each qualifying
    subfolder does (``history/full_week`` → ``full_week``). Runs on every request, so a newly pulled ``test/`` folder
    shows up without a restart. If two folders share a name, the first one found wins.
    """
    found: dict[str, Path] = {}
    for rel in get_settings().data_dir_list:
        root = REPO_ROOT / rel
        if not root.is_dir():
            continue  # e.g. test/ before the organizers release it
        candidates = [root] if is_dataset(root) else sorted(p for p in root.iterdir() if p.is_dir())
        for folder in candidates:
            if is_dataset(folder):
                found.setdefault(folder.name, folder)
    return dict(sorted(found.items()))


def get_dataset(ds_id: str) -> Dataset:
    """Load (or reuse) the dataset with this ID. Raises KeyError if no such folder exists.

    Cached per folder and telemetry modification time, so an edited or replaced file is reloaded.
    """
    folder = scan_datasets().get(ds_id)
    if folder is None:
        raise KeyError(ds_id)
    return _load_cached(str(folder), (folder / "telemetry.csv").stat().st_mtime)


@lru_cache(maxsize=8)
def _load_cached(folder: str, _mtime: float) -> Dataset:
    """Cache wrapper: ``_mtime`` is part of the key so a changed file is reloaded."""
    return load_dataset(Path(folder))


def load_dataset(folder: Path) -> Dataset:
    """Read and validate one dataset folder (ARCHITECTURE.md §3.3 steps 1-3).

    Raises:
        TelemetryValidationError: a file is missing, or telemetry.csv lacks a required column or has no readings.
    """
    missing = [name for name in DATASET_FILES if not (folder / name).is_file()]
    if missing:
        raise TelemetryValidationError(f"{folder.name}: missing {', '.join(missing)}.")

    network = Network.model_validate(json.loads((folder / "network.json").read_text()))
    ops = Ops.model_validate(json.loads((folder / "ops.json").read_text()))
    report = ValidationReport()

    df = pd.read_csv(folder / "telemetry.csv")
    absent = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if absent:
        raise TelemetryValidationError(f"{folder.name}/telemetry.csv: missing column(s) {', '.join(absent)}.")
    report.rows_read = len(df)

    # Step 2 (validate records): values and timestamps must be numeric. Bad rows are dropped and counted.
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["ts_unix"] = pd.to_numeric(df["ts_unix"], errors="coerce")
    bad = df["value"].isna() | df["ts_unix"].isna()
    report.rows_dropped_non_numeric = int(bad.sum())
    df = df[~bad]
    dup = df.duplicated(subset=["ts_unix", "asset_id", "metric"], keep="last")
    report.rows_dropped_duplicate = int(dup.sum())
    df = df[~dup]
    if df.empty:
        raise TelemetryValidationError(f"{folder.name}/telemetry.csv has no valid readings.")
    df = df.astype({"ts_unix": "int64"})

    # Step 3 (identify asset and metric): one array per (asset, metric), aligned on one time axis.
    wide = df.pivot(index="ts_unix", columns=["asset_id", "metric"], values="value").sort_index()
    times = wide.index.to_numpy(dtype=np.int64)
    gaps = np.diff(times)
    step_s = int(pd.Series(gaps).mode().iloc[0]) if len(gaps) else 0
    if step_s:
        report.off_grid_timestamps = int(((times - times[0]) % step_s != 0).sum())
    for label, count in (
        ("non-numeric rows dropped", report.rows_dropped_non_numeric),
        ("duplicate rows dropped", report.rows_dropped_duplicate),
        ("timestamps off the reading grid", report.off_grid_timestamps),
    ):
        if count:
            report.issues.append(f"{count} {label}")

    series = {(str(asset), str(metric)): wide[(asset, metric)].to_numpy(dtype=float) for asset, metric in wide.columns}
    return Dataset(
        id=folder.name,
        folder=folder,
        network=network,
        ops=ops,
        times=times,
        step_s=step_s,
        validation=report,
        _series=series,
    )
