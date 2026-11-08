"""Incident lifecycle and duplicate prevention (ARCHITECTURE.md §3.3).

Where this sits in the app
--------------------------
The detector answers "what problems exist at time t?". This module turns a sequence of those answers (a live feed,
or a replay of the recording tick by tick) into **incidents with a lifecycle**:

* ``new``      - first time the problem is seen
* ``active``   - seen again, nothing material changed
* ``updated``  - seen again with new information (level, countdown, evidence)
* ``resolved`` - the problem ended (the detector reports an end time)

The public contract keeps using ``Event.status`` (ongoing / resolved), as the spec asks: new/active/updated all map
to "ongoing". The lifecycle and its history live here, so the problem-solving workflow (orchestrator) and Sentinel
can tell a brand-new incident from one they already handled.

No duplicates: an incident is identified by its asset, category and onset (``Event.id`` = ``asset@onset``). If a
detection for the same asset and category arrives with a slightly different onset while an incident is still open,
it is matched to that incident instead of creating a second one. Resolved incidents are kept as history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..config import get_settings
from ..models import Event
from .anomaly_detector import detect
from .telemetry_processor import Dataset

Lifecycle = Literal["new", "active", "updated", "resolved"]

# Changes smaller than these don't count as an "update" (they're sensor noise).
LEVEL_CHANGE_PCT = 0.5
HOURS_CHANGE = 0.05


@dataclass
class Incident:
    """One tracked problem: its latest detection, lifecycle state and history of state changes."""

    id: str
    event: Event
    lifecycle: Lifecycle
    first_seen_unix: int
    last_seen_unix: int
    history: list[tuple[int, Lifecycle]] = field(default_factory=list)


@dataclass(frozen=True)
class Transition:
    """A lifecycle change emitted while processing one tick (what the problem-solving workflow reacts to)."""

    incident_id: str
    lifecycle: Lifecycle
    at_unix: int


class IncidentManager:
    """Tracks incidents across detection ticks, without duplicates (ARCHITECTURE.md §3.3)."""

    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}

    def _match(self, ev: Event) -> Incident | None:
        """Find the incident a detection belongs to: same ID, or same asset + category still open nearby."""
        if ev.id in self.incidents:
            return self.incidents[ev.id]
        window = get_settings().merge_gap_hours * 3600
        for inc in self.incidents.values():
            same_problem = inc.event.asset_id == ev.asset_id and inc.event.category == ev.category
            if same_problem and inc.lifecycle != "resolved" and abs(inc.event.onset_unix - ev.onset_unix) <= window:
                return inc
        return None

    @staticmethod
    def _changed(old: Event, new: Event) -> bool:
        """True if the new detection carries materially new information."""
        if old.status != new.status or old.end_unix != new.end_unix or old.evidence != new.evidence:
            return True
        if (old.level_pct is None) != (new.level_pct is None):
            return True
        if old.level_pct is not None and abs(old.level_pct - (new.level_pct or 0)) >= LEVEL_CHANGE_PCT:
            return True
        old_h = old.hours_to_limit.p50 if old.hours_to_limit else None
        new_h = new.hours_to_limit.p50 if new.hours_to_limit else None
        return (old_h is None) != (new_h is None) or (
            old_h is not None and new_h is not None and abs(old_h - new_h) >= HOURS_CHANGE
        )

    def process(self, events: list[Event], at: int) -> list[Transition]:
        """Apply one tick of detections and return the lifecycle changes it caused."""
        transitions: list[Transition] = []
        for ev in events:
            inc = self._match(ev)
            if inc is None:
                lifecycle: Lifecycle = "resolved" if ev.status == "resolved" else "new"
                inc = Incident(id=ev.id, event=ev, lifecycle=lifecycle, first_seen_unix=at, last_seen_unix=at)
                self.incidents[ev.id] = inc
            else:
                if inc.lifecycle == "resolved":
                    continue  # already closed; keep the history as it was
                lifecycle = (
                    "resolved" if ev.status == "resolved" else ("updated" if self._changed(inc.event, ev) else "active")
                )
                inc.event = ev.model_copy(update={"id": inc.id})  # keep the original ID: no duplicate incidents
                inc.last_seen_unix = at
            if lifecycle != inc.lifecycle or lifecycle in ("new", "updated") or not inc.history:
                inc.lifecycle = lifecycle
                inc.history.append((at, lifecycle))
                transitions.append(Transition(inc.id, lifecycle, at))
        return transitions

    def open_incidents(self) -> list[Incident]:
        """Incidents not yet resolved, oldest first."""
        return sorted((i for i in self.incidents.values() if i.lifecycle != "resolved"), key=lambda i: i.first_seen_unix)


def replay(ds: Dataset, start: int | None = None, until: int | None = None, every_s: int | None = None) -> IncidentManager:
    """Run monitoring over the recording tick by tick, as a live feed would (ARCHITECTURE.md §3.1, §3.3).

    Args:
        ds: the dataset to replay.
        start / until: unix seconds bounds (default: the whole window).
        every_s: how often to run detection, e.g. 1800 for every 30 minutes (default: every reading).
    """
    manager = IncidentManager()
    for t in ds.replay(start=start, until=until, every_s=every_s):
        manager.process(detect(ds, t), t)
    return manager
