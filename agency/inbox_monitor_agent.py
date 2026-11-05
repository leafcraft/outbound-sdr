"""inbox_monitor_agent — inbound ingest (SYSTEM, connector-backed, cron).

Production: the mailbox connector (integration: mcp in config.yaml, tool
fetch_replies; IMAP/sequencer inbound) runs and this normalises each
returned message into a canonical reply dict (@sdk.intelligence role).
Dev: returns a local-store stand-in — an empty reply list, because there
is no real mailbox to pull from until the vault connection 'mailbox_mcp' is
wired. This lets the cron entry point (pull_replies) run inertly day-0.

Yields (config.yaml): replies, reply_count.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "replies": [],
    "reply_count": 0,
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


def _shape_replies(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """@sdk.intelligence: normalise the mailbox's fetch_replies response
    into canonical reply dicts {reply_id, lead_id, body, ...}."""
    messages = (
        raw.get("replies")
        or raw.get("messages")
        or raw.get("results")
        or raw.get("data")
        or []
    )
    if not isinstance(messages, list):
        messages = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        out.append({
            "reply_id": m.get("reply_id") or m.get("id") or store.new_id("reply"),
            "lead_id": m.get("lead_id") or m.get("contact_id", ""),
            "body": m.get("body") or m.get("text") or m.get("snippet", ""),
            "from": m.get("from") or m.get("sender", ""),
            "subject": m.get("subject", ""),
            "received_at": m.get("received_at") or m.get("date", ""),
        })
    return out


async def inbox_monitor_agent(llm_response, input_data, context):
    """Pull new replies. Connector path normalises the mailbox response;
    dev path returns nothing — there is no real inbox to pull."""
    input_data = input_data or {}

    if isinstance(llm_response, dict) and llm_response:
        replies = _shape_replies(llm_response)
        source = "mailbox_connector"
    else:
        replies = []
        source = "dev_store"

    logger.info(
        f"inbox_monitor_agent — pulled {len(replies)} replies ({source}); "
        f"since={_pick(input_data, 'since', '')!r}"
    )

    computed = {
        "replies": replies,
        "reply_count": len(replies),
    }
    return {**_DEFAULTS, **computed}
