"""campaign_launcher_agent — shipping (SYSTEM, connector-backed).

Production: the EmailBison / Instantly sequencer connector (integration:
mcp in config.yaml, tool upload_campaign) runs and this shapes its raw
response (campaign ref, scheduled count) into yields (@sdk.intelligence
role). Dev: returns a local-store stand-in — a synthetic sequencer_ref
plus a scheduled_count counted from sequences already written to
_shared/store for the campaign, so the chain runs day-0 before the
SEQUENCER_MCP_URL connector is wired.

Yields (config.yaml): campaign_id, sequencer_ref, scheduled_count,
upload_ok.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "campaign_id": "",
    "sequencer_ref": "",
    "scheduled_count": 0,
    "upload_ok": False,
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


def _count_scheduled(campaign_id: str) -> int:
    """Count distinct lead sequences written for this campaign in the dev
    store. Each lead with at least one persisted step = one scheduled
    sequence. Best-effort; returns 0 if nothing is wired."""
    if not campaign_id:
        return 0
    try:
        seqs = store._load("sequences")  # dev-only introspection of the store
    except Exception:
        return 0
    prefix = f"{campaign_id}:"
    return sum(
        1 for key, rec in seqs.items()
        if key.startswith(prefix) and isinstance(rec, dict) and rec.get("steps")
    )


def _shape_connector_result(raw: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    """@sdk.intelligence: map the sequencer's upload_campaign response into
    yields."""
    ref = (
        raw.get("sequencer_ref")
        or raw.get("campaign_ref")
        or raw.get("id")
        or raw.get("campaign_id")
        or store.new_id("seq")
    )
    scheduled = (
        raw.get("scheduled_count")
        or raw.get("scheduled")
        or raw.get("queued")
        or len(raw.get("contacts", []) or [])
    )
    ok = raw.get("upload_ok")
    if ok is None:
        ok = bool(raw.get("success", True)) and not raw.get("error")
    return {
        "campaign_id": campaign_id,
        "sequencer_ref": str(ref),
        "scheduled_count": int(scheduled or 0),
        "upload_ok": bool(ok),
    }


async def campaign_launcher_agent(llm_response, input_data, context):
    """Push built sequences to the sequencer. Connector path shapes its
    ack; dev path returns a synthetic ref + store-counted schedule."""
    input_data = input_data or {}
    campaign_id = _pick(input_data, "campaign_id", "")

    if isinstance(llm_response, dict) and llm_response:
        computed = _shape_connector_result(llm_response, campaign_id)
        source = "sequencer_connector"
    else:
        scheduled = _count_scheduled(campaign_id)
        computed = {
            "campaign_id": campaign_id,
            "sequencer_ref": store.new_id("seq"),
            "scheduled_count": scheduled,
            "upload_ok": True,
        }
        source = "dev_store"

    logger.info(
        f"campaign_launcher_agent — uploaded ({source}) campaign={campaign_id!r}, "
        f"ref={computed['sequencer_ref']}, scheduled={computed['scheduled_count']}"
    )
    return {**_DEFAULTS, **computed}
