"""Score the monitoring stage against a dataset's answer key (ARCHITECTURE.md §17; dev/test only).

This is the ONLY code that reads ``answer_key.json`` (CLAUDE.md rule 1). The app never does: it finds every
problem from the sensor data. Use this to prove detection works and to catch regressions.

What it measures
----------------
* found / missed per category (a detection matches a label when asset + category agree and their time spans overlap)
* false alarms: detections that match no label (normal readings must not create incidents, §17)
* duplicates: more than one detection matched to the same label
* onset error (hours, detection minus label); pump failures should be exact
* for snapshot folders: whether the set of ongoing incidents equals the key's

Usage (from backend/):
    uv run python scripts/score.py ../history/full_week
    uv run python scripts/score.py ../history/snapshot_2026-10-08T1200 --strict
Exit code 1 with --strict if anything is missed, falsely raised or duplicated.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `nightwatch` importable

from nightwatch.models import Event  # noqa: E402
from nightwatch.monitoring.anomaly_detector import detect  # noqa: E402
from nightwatch.monitoring.telemetry_processor import load_dataset  # noqa: E402

# A detection may extend past the label's end by this much and still match (end detection waits for pump runs).
END_SLACK_S = 6 * 3600


@dataclass
class Score:
    """Results of scoring one dataset."""

    labels: int = 0
    found: int = 0
    missed: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    onset_error_h: dict[str, float] = field(default_factory=dict)
    ongoing_missing: list[str] = field(default_factory=list)
    ongoing_extra: list[str] = field(default_factory=list)


def matches(ev: Event, label: dict) -> bool:
    """True if a detection and a label describe the same problem (same asset + category, overlapping in time)."""
    label_end = label.get("end_unix") or 10**12
    ev_end = ev.end_unix or 10**12
    return (
        ev.category == label["category"]
        and ev.asset_id == label["asset"]
        and ev.onset_unix < label_end + END_SLACK_S
        and ev_end > label["onset_unix"]
    )


def score_dataset(folder: Path) -> Score:
    """Run detection at the dataset's 'now' and compare with its answer key."""
    ds = load_dataset(folder)
    events = detect(ds)
    labels = json.loads((folder / "answer_key.json").read_text())
    s = Score(labels=len(labels))
    used: set[str] = set()
    for label in labels:
        hits = [e for e in events if matches(e, label)]
        if not hits:
            s.missed.append(f"{label['id']} {label['category']} {label['asset']}")
            continue
        best = min(hits, key=lambda e: abs(e.onset_unix - label["onset_unix"]))
        s.found += 1
        s.onset_error_h[label["id"]] = round((best.onset_unix - label["onset_unix"]) / 3600, 2)
        if len(hits) > 1:
            s.duplicates.append(f"{label['id']}: {[h.id for h in hits]}")
        used.update(h.id for h in hits)
    s.false_alarms = [e.id for e in events if e.id not in used]
    want = {(lab["category"], lab["asset"]) for lab in labels if lab.get("status") == "ongoing"}
    got = {(e.category, e.asset_id) for e in events if e.status == "ongoing"}
    s.ongoing_missing = sorted(f"{c} {a}" for c, a in want - got)
    s.ongoing_extra = sorted(f"{c} {a}" for c, a in got - want)
    return s


def main() -> int:
    """CLI: print the score and return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="dataset folder containing answer_key.json")
    parser.add_argument("--strict", action="store_true", help="exit 1 on any miss, false alarm or duplicate")
    args = parser.parse_args()

    s = score_dataset(args.folder)
    print(
        f"{args.folder.name}: found {s.found}/{s.labels}, false alarms {len(s.false_alarms)}, duplicates {len(s.duplicates)}"
    )
    for title, items in (("Missed", s.missed), ("False alarms", s.false_alarms), ("Duplicates", s.duplicates)):
        for item in items:
            print(f"  {title}: {item}")
    print("  Onset error (h):", s.onset_error_h)
    if s.ongoing_missing or s.ongoing_extra:
        print(f"  Ongoing at 'now': missing {s.ongoing_missing}, extra {s.ongoing_extra}")
    bad = s.missed or s.false_alarms or s.duplicates
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
