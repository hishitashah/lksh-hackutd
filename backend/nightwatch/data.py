"""Dataset loader: turns a dataset folder into fast in-memory arrays (ARCHITECTURE.md §3.1 and §3.3 steps 1-3).

Where this sits in the app
--------------------------
Everything that reads sensor data goes through :class:`Dataset`: the monitoring modules
(``monitoring/``, §14), the planning logic (``planning/``), the API routes (``api.py``) and the agents'
tools. Nothing else parses ``telemetry.csv``, so there is exactly one place
where records are validated and one definition of "now".

What a dataset is
-----------------
A folder holding the three files every dataset has (README "What you get"): ``network.json``,
``ops.json`` and ``telemetry.csv``. ``answer_key.json`` is never read here (CLAUDE.md rule 1).

Rules this module follows (CLAUDE.md)
-------------------------------------
* No hardcoded values: the time range, the reading interval, the asset list and the set of
  expected series are all derived from the loaded files, so the unseen ``test/`` network
  (S6-S11, different counts of everything) loads with no code change.
* "Now" is the last telemetry timestamp, never the system clock (SPEC "Time range").
* All times are UTC unix seconds.

Layout of the in-memory data
----------------------------
``Dataset.times`` is one sorted ``int64`` array of unix seconds. ``Dataset.values`` maps the key
``"ASSET.metric"`` (build it with :func:`series_key`) to a ``float64`` array aligned to
``times``, so ``values[key][i]`` is the reading at ``times[i]``. Arrays are read-only because the
loaded dataset is cached and shared between requests.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import REPO_ROOT, get_settings
from .models import Facility, Network, Ops, TimeRange

# The three files every dataset folder must contain (README "What you get").
DATASET_FILES = ("network.json", "ops.json", "telemetry.csv")

# The five telemetry.csv columns (SPEC "telemetry.csv"). All must be present; ``timestamp`` is the
# human-readable twin of ``ts_unix`` and is not used for anything else.
TELEMETRY_COLUMNS = ("timestamp", "ts_unix", "asset_id", "metric", "value")


class UnknownDatasetError(KeyError):
    """Raised by :func:`get_dataset` when no folder under ``DATA_DIRS`` has the requested ID."""


# ---------------------------------------------------------------------------
# Finding datasets on disk
# ---------------------------------------------------------------------------


def scan_datasets() -> dict[str, Path]:
    """Find every dataset folder under the configured ``DATA_DIRS``.

    A folder is a dataset when it holds all three files. A top-level folder that is a
    dataset itself (``test/``) gets its own name as ID; otherwise each qualifying
    subfolder does (``history/full_week`` -> ``full_week``). Runs on every call, so a
    newly pulled ``test/`` folder shows up without restarting the server.

    Returns:
        Mapping of dataset ID -> absolute folder path, sorted by ID. If two folders share
        a name, the first one found wins.
    """
    found: dict[str, Path] = {}
    for rel in get_settings().data_dir_list:
        root = REPO_ROOT / rel
        if not root.is_dir():
            continue  # e.g. test/ before the organizers release it
        candidates = [root] if _is_dataset(root) else sorted(p for p in root.iterdir() if p.is_dir())
        for folder in candidates:
            if _is_dataset(folder):
                found.setdefault(folder.name, folder)
    return dict(sorted(found.items()))


def _is_dataset(folder: Path) -> bool:
    """True if ``folder`` contains network.json, ops.json and telemetry.csv."""
    return all((folder / name).is_file() for name in DATASET_FILES)


# ---------------------------------------------------------------------------
# The loaded dataset
# ---------------------------------------------------------------------------


def series_key(asset_id: str, metric: str) -> str:
    """Build the ``Dataset.values`` key for one sensor series, e.g. ``'S2_TANK_B.level_pct'``.

    Args:
        asset_id: Asset ID as written in telemetry.csv (``asset_id`` column).
        metric: Metric name as written in telemetry.csv (``metric`` column).
    """
    return f"{asset_id}.{metric}"


@dataclass(frozen=True, eq=False)  # eq=False: numpy arrays can't be compared with ==, so identity is used.
class Dataset:
    """One loaded dataset: the network, the ops rules and every sensor reading, as numpy arrays.

    Build it with :func:`load_dataset` (fresh) or :func:`get_dataset` (cached). Treat it as
    read-only: it is shared between requests.

    Attributes:
        id: Dataset ID used in URLs (the folder name, e.g. ``'full_week'``).
        folder: Absolute path of the dataset folder.
        network: Parsed network.json.
        ops: Parsed ops.json.
        times: Sorted unix-second timestamps (``int64``), one per reading interval.
        values: ``"ASSET.metric"`` -> readings (``float64``) aligned to ``times``. NaN = no reading.
        facilities: Facility ID -> :class:`~nightwatch.models.Facility`.
        system_of: Facility ID -> ID of the system it belongs to.
        step_s: Seconds between readings, the most common gap between timestamps.
        warnings: Soft data-quality notes (gaps, missing series, unknown assets). Empty when clean.
    """

    id: str
    folder: Path
    network: Network
    ops: Ops
    times: np.ndarray
    values: dict[str, np.ndarray]
    facilities: dict[str, Facility]
    system_of: dict[str, str]
    step_s: int
    warnings: tuple[str, ...] = ()

    @property
    def now_unix(self) -> int:
        """'Now' for this dataset: the last telemetry timestamp, unix seconds (SPEC "Time range")."""
        return int(self.times[-1])

    @property
    def time_range(self) -> TimeRange:
        """First timestamp, 'now' and the reading interval, all taken from the telemetry."""
        return TimeRange(start_unix=int(self.times[0]), now_unix=self.now_unix, step_s=self.step_s)

    def index_at(self, t: int) -> int:
        """Index of the last reading at or before ``t`` (so a detector at time ``t`` sees nothing later).

        Args:
            t: Time to look up, unix seconds.

        Returns:
            Index into ``times`` / every ``values`` array. Clamped: a ``t`` before the first
            reading gives 0 and a ``t`` after 'now' gives the last index.
        """
        i = int(np.searchsorted(self.times, t, side="right")) - 1
        return min(max(i, 0), len(self.times) - 1)

    def has_series(self, asset_id: str, metric: str) -> bool:
        """True if telemetry has a series for this asset and metric."""
        return series_key(asset_id, metric) in self.values

    def series(self, asset_id: str, metric: str) -> np.ndarray:
        """The whole series for one sensor, aligned to ``times`` (read-only ``float64``).

        Raises:
            KeyError: if the dataset has no such series.
        """
        try:
            return self.values[series_key(asset_id, metric)]
        except KeyError:
            raise KeyError(f"Dataset '{self.id}' has no series '{series_key(asset_id, metric)}'.") from None

    def value_at(self, asset_id: str, metric: str, t: int) -> float:
        """Reading of one sensor at the last interval at or before ``t`` (NaN if that reading is missing)."""
        return float(self.series(asset_id, metric)[self.index_at(t)])

    def window(self, asset_id: str, metric: str, t0: int, t1: int) -> tuple[np.ndarray, np.ndarray]:
        """Readings with ``t0 <= time <= t1`` (both ends inclusive).

        Args:
            asset_id: Asset ID.
            metric: Metric name.
            t0: Window start, unix seconds.
            t1: Window end, unix seconds.

        Returns:
            ``(times, values)``: unix seconds and readings for the window, in time order. Both are
            empty when ``t1 < t0`` or the window holds no readings.
        """
        lo = int(np.searchsorted(self.times, t0, side="left"))
        hi = int(np.searchsorted(self.times, t1, side="right"))
        return self.times[lo:hi], self.series(asset_id, metric)[lo:hi]


# ---------------------------------------------------------------------------
# Loading and validating
# ---------------------------------------------------------------------------


def expected_series(network: Network) -> set[str]:
    """Every ``"ASSET.metric"`` series the equipment in network.json should report.

    SPEC "telemetry.csv": each well reports ``water_bpd`` and ``status``, each facility
    ``inflow_bpd``, each tank ``level_pct``, each pump ``status`` / ``flow_bpd`` /
    ``discharge_psi`` and each pipeline ``receive_flow_bpd`` / ``pressure_psi``. Used only to
    warn about missing or unexplained series, never to reject a dataset, so a network with
    extra equipment or extra sensors still loads.

    Args:
        network: Parsed network.json.
    """
    expected: set[str] = set()
    for system in network.systems:
        for facility in system.facilities:
            expected.add(series_key(facility.id, "inflow_bpd"))
            expected.add(series_key(facility.tank.id, "level_pct"))
            for metric in ("status", "flow_bpd", "discharge_psi"):
                expected.add(series_key(facility.pump.id, metric))
            for metric in ("receive_flow_bpd", "pressure_psi"):
                expected.add(series_key(facility.pipeline.id, metric))
            for well in facility.wells:
                for metric in ("water_bpd", "status"):
                    expected.add(series_key(well.id, metric))
    return expected


def _read_json_model(path: Path, model):
    """Parse one JSON input file into its pydantic model, naming the file in any error.

    Raises:
        ValueError: if the file isn't valid JSON or doesn't match the model.
    """
    try:
        return model.model_validate(json.loads(path.read_text()))
    except ValueError as err:  # json.JSONDecodeError and pydantic.ValidationError are both ValueErrors.
        raise ValueError(f"{path.name} in {path.parent} is not valid: {err}") from err


def _read_telemetry(csv_path: Path) -> tuple[pd.DataFrame, int]:
    """Read telemetry.csv and check every record is usable.

    Hard errors (the data can't be placed on the time grid): a missing column, no rows, a
    timestamp that isn't a number, or two readings for the same asset, metric and time.
    A reading whose value is empty or not a number is kept as NaN (a missing reading) and
    counted, so the detector can ignore it instead of the whole dataset failing to load.

    Args:
        csv_path: Path to telemetry.csv.

    Returns:
        ``(frame, bad_values)``: a frame with columns ``ts_unix`` (int64), ``asset_id``,
        ``metric`` and ``value`` (float64, NaN = missing), and how many values were bad.

    Raises:
        ValueError: with a message that names the file and the problem.
    """
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing = [c for c in TELEMETRY_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"{csv_path.name} is missing column(s) {missing}; expected {list(TELEMETRY_COLUMNS)}.")
    if raw.empty:
        raise ValueError(f"{csv_path.name} has no readings.")

    ts = pd.to_numeric(raw["ts_unix"], errors="coerce")
    bad_ts = ts.isna() | (ts != np.floor(ts))
    if bad_ts.any():
        row = int(bad_ts.idxmax())
        raise ValueError(
            f"{csv_path.name}: {int(bad_ts.sum())} row(s) have a ts_unix that isn't a whole number "
            f"(first at data row {row + 1}: {raw.at[row, 'ts_unix']!r})."
        )

    values = pd.to_numeric(raw["value"], errors="coerce")
    frame = pd.DataFrame(
        {
            "ts_unix": ts.astype("int64"),
            "asset_id": raw["asset_id"],
            "metric": raw["metric"],
            "value": values.astype("float64"),
        }
    )
    dup = frame.duplicated(["ts_unix", "asset_id", "metric"], keep=False)
    if dup.any():
        first = frame[dup].iloc[0]
        raise ValueError(
            f"{csv_path.name}: {int(frame.duplicated(['ts_unix', 'asset_id', 'metric']).sum())} duplicate reading(s) "
            f"for the same asset, metric and time (first: {first.asset_id} {first.metric} at {first.ts_unix})."
        )
    return frame, int(values.isna().sum())


def load_dataset(folder: Path, ds_id: str | None = None) -> Dataset:
    """Read and validate a dataset folder (§3.3 steps 1-2: receive and validate the telemetry).

    Takes a fraction of a second for a week of data. The app calls :func:`get_dataset` instead, which caches this.

    Args:
        folder: Folder holding network.json, ops.json and telemetry.csv.
        ds_id: ID to give the dataset. Defaults to the folder name.

    Returns:
        A :class:`Dataset` whose ``values`` arrays are pivoted onto one shared, sorted time axis.

    Raises:
        ValueError: if a file is missing, malformed, or the telemetry can't be placed on a time
            grid (see :func:`_read_telemetry`). The message says which file and what is wrong.
    """
    folder = Path(folder)
    absent = [name for name in DATASET_FILES if not (folder / name).is_file()]
    if absent:
        raise ValueError(f"{folder} is not a dataset folder: missing {absent}.")

    network = _read_json_model(folder / "network.json", Network)
    ops = _read_json_model(folder / "ops.json", Ops)
    frame, bad_values = _read_telemetry(folder / "telemetry.csv")

    # One row per timestamp, one column per (asset, metric). A series that lacks a reading at some
    # timestamp gets NaN there instead of shifting later readings out of alignment.
    wide = frame.pivot(index="ts_unix", columns=["asset_id", "metric"], values="value").sort_index()
    times = wide.index.to_numpy(dtype="int64")
    if len(times) < 2:
        raise ValueError("telemetry.csv needs at least two timestamps to work out the reading interval.")

    # Fortran order makes every column contiguous, so each series below is a cheap view of one block.
    block = np.asfortranarray(wide.to_numpy(dtype="float64"))
    values: dict[str, np.ndarray] = {}
    for j, (asset_id, metric) in enumerate(wide.columns):
        arr = block[:, j]
        arr.setflags(write=False)  # cached and shared between requests: nobody may modify it
        values[series_key(asset_id, metric)] = arr
    times.setflags(write=False)

    # SPEC "telemetry.csv": a reading every interval. The interval isn't stated anywhere, so take the
    # most common gap between timestamps rather than assuming 5 minutes.
    gaps, counts = np.unique(np.diff(times), return_counts=True)
    step_s = int(gaps[counts.argmax()])

    facilities = {f.id: f for s in network.systems for f in s.facilities}
    system_of = {f.id: s.id for s in network.systems for f in s.facilities}

    notes: list[str] = []
    if bad_values:
        notes.append(f"{bad_values} reading(s) had an empty or non-numeric value and are treated as missing.")
    irregular = int(np.count_nonzero(np.diff(times) != step_s))
    if irregular:
        notes.append(f"{irregular} gap(s) between timestamps differ from the usual {step_s} s interval.")
    gaps_in_series = int(np.isnan(block).sum()) - bad_values
    if gaps_in_series > 0:
        notes.append(f"{gaps_in_series} reading(s) are absent (a series has no row at some timestamp).")
    expected = expected_series(network)
    no_telemetry = sorted(expected - values.keys())
    if no_telemetry:
        notes.append(f"{len(no_telemetry)} series in network.json have no telemetry (e.g. {no_telemetry[0]}).")
    unexplained = sorted(values.keys() - expected)
    if unexplained:
        notes.append(f"{len(unexplained)} telemetry series aren't described by network.json (e.g. {unexplained[0]}).")

    return Dataset(
        id=ds_id or folder.name,
        folder=folder,
        network=network,
        ops=ops,
        times=times,
        values=values,
        facilities=facilities,
        system_of=system_of,
        step_s=step_s,
        warnings=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

# folder path -> (signature of the three files, loaded dataset). The signature is each file's
# (mtime, size), so editing a file in place (e.g. a temp copy of network.json with a different tank
# limit) reloads it, and an unchanged folder is parsed only once.
_CACHE: dict[str, tuple[tuple[tuple[int, int], ...], Dataset]] = {}
_CACHE_LOCK = threading.Lock()  # FastAPI runs sync routes in a thread pool; load each folder once, not twice.


def _signature(folder: Path) -> tuple[tuple[int, int], ...]:
    """(mtime in ns, size in bytes) of the three dataset files, to notice when any of them changes."""
    stats = [(folder / name).stat() for name in DATASET_FILES]
    return tuple((s.st_mtime_ns, s.st_size) for s in stats)


def get_dataset(ds_id: str) -> Dataset:
    """Return the dataset with this ID, loading it on first use and reloading it if its files change.

    Args:
        ds_id: ID from :func:`scan_datasets` (e.g. ``'full_week'``).

    Raises:
        UnknownDatasetError: if no folder under ``DATA_DIRS`` has this ID (the API answers 404).
        ValueError: if the folder exists but can't be loaded (the API answers 422).
    """
    folder = scan_datasets().get(ds_id)
    if folder is None:
        raise UnknownDatasetError(ds_id)
    with _CACHE_LOCK:
        signature = _signature(folder)
        cached = _CACHE.get(str(folder))
        if cached is None or cached[0] != signature:
            cached = (signature, load_dataset(folder, ds_id))
            _CACHE[str(folder)] = cached
        return cached[1]
