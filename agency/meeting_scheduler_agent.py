"""meeting_scheduler_agent — calendar (SYSTEM, connector-backed).

Production: the calendar connector (integration: mcp in the configuration, tool
book_meeting; Google Calendar / Cal.com) runs and this maps its booking
response into yields (@sdk.intelligence role). Dev: returns a local-store
stand-in — a synthetic meeting_id and a placeholder ISO slot marked
booked, so the human → booking chain runs day-0 before the
vault connection 'calendar_mcp' is wired.

Yields (set in the configuration): lead_id, meeting_id, slot, booked_ok.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "lead_id": "",
    "meeting_id": "",
    "slot": "",
    "booked_ok": False,
}


def _pick(input_data: dict[str, Any], key: str, default: Any) -> Any:
    """Shape-tolerant read: input_data, then upstream_yields (flat or
    keyed by agent), then human_data."""
    if not isinstance(input_data, dict):
        return default
    if input_data.get(key) not in (None, ""):
        return input_data[key]
    upstream = input_data.get("upstream_yields") or input_data.get("upstream_results") or {}
    if isinstance(upstream, dict):
        if upstream.get(key) not in (None, ""):
            return upstream[key]
        for v in upstream.values():
            if isinstance(v, dict) and v.get(key) not in (None, ""):
                return v[key]
    human = input_data.get("human_data") or {}
    if isinstance(human, dict) and human.get(key) not in (None, ""):
        return human[key]
    return default


def _placeholder_slot() -> str:
    """A plausible near-future ISO slot (next business-ish day, 10:00 UTC)."""
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    return slot.isoformat()


def _shape_booking(raw: dict[str, Any], lead_id: str) -> dict[str, Any]:
    """@sdk.intelligence: map the calendar's book_meeting response into
    yields."""
    meeting_id = (
        raw.get("meeting_id")
        or raw.get("event_id")
        or raw.get("id")
        or store.new_id("mtg")
    )
    slot = (
        raw.get("slot")
        or raw.get("start")
        or raw.get("start_time")
        or raw.get("scheduled_at")
        or _placeholder_slot()
    )
    ok = raw.get("booked_ok")
    if ok is None:
        ok = bool(raw.get("success", True)) and not raw.get("error")
    return {
        "lead_id": raw.get("lead_id") or lead_id,
        "meeting_id": str(meeting_id),
        "slot": str(slot),
        "booked_ok": bool(ok),
    }


async def meeting_scheduler_agent(llm_response, input_data, context):
    """Book the meeting. Connector path shapes the calendar response; dev
    path returns a synthetic booked slot."""
    input_data = input_data or {}
    lead_id = _pick(input_data, "lead_id", "")

    if isinstance(llm_response, dict) and llm_response:
        computed = _shape_booking(llm_response, lead_id)
        source = "calendar_connector"
    else:
        computed = {
            "lead_id": lead_id,
            "meeting_id": store.new_id("mtg"),
            "slot": _placeholder_slot(),
            "booked_ok": True,
        }
        source = "dev_store"

    logger.info(
        f"meeting_scheduler_agent — booked ({source}) lead={lead_id!r}, "
        f"meeting={computed['meeting_id']}, slot={computed['slot']}"
    )
    return {**_DEFAULTS, **computed}
