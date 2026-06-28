"""records_keeper_agent — system of record (SYSTEM, connector-backed).

Production: the CRM connector (integration: mcp in config.yaml, tool
upsert_record; Salesforce / HubSpot) runs and this shapes its upsert
acknowledgement into yields (@sdk.intelligence role). Dev: returns a
local-store stand-in — a synthetic crm_record_id and a synced_ok ack, so
campaign/outcome state has a terminus day-0 before the CRM_MCP_URL
connector is wired.

Yields (config.yaml): crm_record_id, synced_ok.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "crm_record_id": "",
    "synced_ok": False,
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


def _shape_crm_ack(raw: dict[str, Any]) -> dict[str, Any]:
    """@sdk.intelligence: map the CRM's upsert_record ack into yields."""
    record_id = (
        raw.get("crm_record_id")
        or raw.get("record_id")
        or raw.get("id")
        or store.new_id("crm")
    )
    ok = raw.get("synced_ok")
    if ok is None:
        ok = bool(raw.get("success", True)) and not raw.get("error")
    return {
        "crm_record_id": str(record_id),
        "synced_ok": bool(ok),
    }


async def records_keeper_agent(llm_response, input_data, context):
    """Write campaign + outcome state to the CRM. Connector path shapes the
    upsert ack; dev path returns a synthetic synced_ok record."""
    input_data = input_data or {}
    # payload is the declared input; read it tolerantly for logging context.
    payload = _pick(input_data, "payload", {})

    if isinstance(llm_response, dict) and llm_response:
        computed = _shape_crm_ack(llm_response)
        source = "crm_connector"
    else:
        computed = {
            "crm_record_id": store.new_id("crm"),
            "synced_ok": True,
        }
        source = "dev_store"

    logger.info(
        f"records_keeper_agent — synced ({source}) record={computed['crm_record_id']}, "
        f"payload_keys={list(payload.keys()) if isinstance(payload, dict) else 'n/a'}"
    )
    return {**_DEFAULTS, **computed}
