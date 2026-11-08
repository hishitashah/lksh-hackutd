"""Human approval and field dispatch (ARCHITECTURE.md §10, §15 Phase 8).

Where this sits in the app
--------------------------
Sentinel (``sentinel_service.py``) drafts actions from the orchestrator's recommendation, all ``pending``. This
module is the only place a draft moves on, and only on an operator decision (rule 6):

    pending ──reject──▶ rejected                     (never dispatched, never shown in the field)
    pending ──approve─▶ approved ──▶ dispatched      (sent to the /field phone page right away)

The model also has ``completed`` (the field crew marks an order done); that button is not built yet.

* ``approve``         - ``POST /agent/approve``: validates, applies the decision, dispatches on approval
* ``list_dispatches`` - ``GET /dispatches``: orders sent to the field, newest first
* ``subscribe``       - ``GET /field/stream``: every current order, then each new one live

Dispatch safety rules (checked when the operator approves, so older or duplicate runs can't double-dispatch):
* **no duplicate order:** the same kind of order for the same incident (inspections: the same asset) can be
  dispatched once;
* **never more trucks than the system has** (§16): trucks in this system's dispatched orders + this order's trucks
  must fit the ``emergency_fleet_size`` in ops.json. Without a "done" button, dispatched trucks stay busy for the
  session.
A failed rule answers 409 and leaves the draft ``pending``: nothing half-happens.

Traceability: the draft inside its stored Sentinel run is updated in place (``GET /agent/runs/{id}`` shows each
draft's status), each change is stamped (``decided_at``, ``dispatched_at``) and logged.

Timestamps are the wall clock (ISO 8601 UTC): they record when a person acted. They are not the dataset's analysis
"now" (rule 3), which stays the last telemetry timestamp; plan times in labels stay in data time.

Storage is in memory: restarting the server clears every order (use it for a clean demo).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from ..config import get_settings
from ..models import ApproveRequest, DraftAction
from ..monitoring.telemetry_processor import Dataset
from . import sentinel_service

log = logging.getLogger("nightwatch.dispatch")

_dispatched: dict[str, list[DraftAction]] = {}  # dataset ID -> orders sent to the field, in dispatch order
# dataset ID -> open /field/stream connections: (their event loop, their queue). The loop is kept so an approval
# handled on another thread can still hand the order over safely (call_soon_threadsafe).
_subscribers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}


class DispatchError(Exception):
    """A decision that can't be applied; ``status_code`` is the HTTP answer the route gives."""

    status_code = 400


class NotFound(DispatchError):
    """Unknown run or draft (HTTP 404)."""

    status_code = 404


class Conflict(DispatchError):
    """The draft isn't pending, or dispatching it would break a safety rule (HTTP 409)."""

    status_code = 409


def now_iso() -> str:
    """The wall-clock time now, ISO 8601 UTC (e.g. '2026-11-08T10:32:05Z'). Only for stamping operator actions."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def approve(ds: Dataset, req: ApproveRequest) -> DraftAction:
    """Apply the operator's decision on one draft; on approval, dispatch it to the field immediately.

    Args:
        ds: the dataset the run belongs to (runs are looked up per dataset).
        req: ``{run_id, action_id, approve}``.
    Returns:
        The updated ``DraftAction``: ``dispatched`` (approved and sent) or ``rejected``.
    Raises:
        NotFound: unknown run (for this dataset) or draft.
        Conflict: the draft was already decided, or a dispatch safety rule fails (the draft stays pending).
    """
    run = sentinel_service.get_run(ds.id, req.run_id)
    if run is None:
        raise NotFound(f"No finished run '{req.run_id}' for dataset '{ds.id}'.")
    draft = next((d for d in run.drafts if d.id == req.action_id), None)
    if draft is None:
        raise NotFound(f"Run '{req.run_id}' has no draft action '{req.action_id}'.")
    if draft.status != "pending":
        when = draft.dispatched_at or draft.decided_at
        raise Conflict(f"'{draft.id}' is already {draft.status}" + (f" (at {when})." if when else "."))

    if not req.approve:
        draft.status, draft.decided_at = "rejected", now_iso()
        log.info("rejected %s (run %s): %s", draft.id, run.run_id, draft.label)
        return draft

    _check_safety(ds, draft)  # raises Conflict before anything changes
    draft.status, draft.decided_at = "approved", now_iso()
    log.info("approved %s (run %s): %s", draft.id, run.run_id, draft.label)
    # Approval is the authorization (§10 step 7): send it to the field right away.
    draft.status, draft.dispatched_at = "dispatched", now_iso()
    _dispatched.setdefault(ds.id, []).append(draft)
    log.info("dispatched %s to the field", draft.id)
    _publish(ds.id, draft)
    return draft


def _check_safety(ds: Dataset, draft: DraftAction) -> None:
    """Dispatch safety rules: no duplicate order, never more trucks than the system has. Raises ``Conflict``."""
    sent = [d for d in _dispatched.get(ds.id, []) if d.status == "dispatched"]
    for d in sent:
        same = (d.kind == draft.kind == "field_inspection" and d.action.asset_id == draft.action.asset_id) or (
            d.kind == draft.kind != "field_inspection" and d.event_id == draft.event_id
        )
        if same:
            raise Conflict(f"This order is already dispatched as '{d.id}' ({d.label}).")
    if draft.kind == "dispatch_truck" and draft.action.facility_id:
        sid = ds.system_of[draft.action.facility_id]
        fleet = ds.ops.systems[sid].emergency_fleet_size
        busy = sum(
            d.action.trucks or 0
            for d in sent
            if d.kind == "dispatch_truck" and d.action.facility_id and ds.system_of.get(d.action.facility_id) == sid
        )
        need = draft.action.trucks or 0
        if busy + need > fleet:
            raise Conflict(
                f"{sid} has {fleet} truck{'s' if fleet != 1 else ''} and {busy} {'are' if busy != 1 else 'is'} already "
                f"dispatched; this order needs {need} more."
            )


def list_dispatches(ds_id: str) -> list[DraftAction]:
    """Orders sent to the field for this dataset (``dispatched`` or later), newest first."""
    return [d.model_copy() for d in reversed(_dispatched.get(ds_id, []))]


async def subscribe(ds_id: str, max_s: float | None = None) -> AsyncIterator[DraftAction]:
    """The live field feed: every current order first (oldest first), then each new one as it's dispatched.

    The connection is registered before the snapshot is read, so no order can slip between the two; an order may
    then arrive twice, which the frontend handles by de-duplicating on ``id``. Ends after ``max_s`` seconds
    (default ``field_stream_max_s``); the browser reconnects on its own.
    """
    limit = max_s if max_s is not None else get_settings().field_stream_max_s
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[DraftAction] = asyncio.Queue()
    entry = (loop, queue)
    subs = _subscribers.setdefault(ds_id, set())
    subs.add(entry)
    try:
        for d in list(_dispatched.get(ds_id, [])):
            yield d.model_copy()
        deadline = loop.time() + limit
        while (remaining := deadline - loop.time()) > 0:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                return
    finally:
        subs.discard(entry)


def _publish(ds_id: str, draft: DraftAction) -> None:
    """Hand a newly dispatched order to every open field stream of this dataset."""
    for loop, queue in list(_subscribers.get(ds_id, ())):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, draft.model_copy())
        except RuntimeError:  # that connection's event loop is gone; it unregisters itself
            pass


def reset(ds_id: str | None = None) -> None:
    """Forget dispatched orders (one dataset, or all). For tests and a clean demo start."""
    if ds_id is None:
        _dispatched.clear()
    else:
        _dispatched.pop(ds_id, None)
