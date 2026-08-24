"""sdr_coordinator_agent — Campaign Operation Coordinator (AI).

auto_discover matches `sdr_coordinator_agent`. The coordinator owns each
campaign case end to end: it opens the case on a start request and hands it to
the campaign strategist, or answers a status ask from the record. It is PURE
orchestration — it never sources a lead, writes a sequence, or approves a send.

The module is a thin floor over the LLM: it normalises the model's decision to
the full yields contract (conditions fail closed on a missing field) and infers
the action from the trigger when the model leaves it blank.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "action": "",
    "campaign_id": "",
    "gtm_id": "",
    "offering_id": "",
    "stage": "",
    "waiting_on": "",
    "summary": "",
}

_VALID_ACTIONS = {"open_case", "status", "escalate"}


def _parse_json(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {}
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (ValueError, TypeError):
            return {}
    return {}


def _infer_action(input_data: dict) -> str:
    """When the model didn't set one, infer from the trigger/entry point."""
    ep = str(input_data.get("entry_point") or input_data.get("trigger") or "").lower()
    if "status" in ep:
        return "status"
    return "open_case"


def normalise_case(result: Any, ctx: dict) -> dict[str, Any]:
    """Floor: guarantee a valid action + full shape so downstream wires fire
    deterministically (conditions fail closed on absence)."""
    result = result if isinstance(result, dict) else {}
    action = str(result.get("action", "")).strip().lower()
    if action not in _VALID_ACTIONS:
        action = _infer_action(result.get("_input", {}))
    result["action"] = action
    if not result.get("stage"):
        result["stage"] = {"open_case": "assembling", "status": "reported",
                           "escalate": "escalated"}.get(action, "")
    result.pop("_input", None)
    return {**_DEFAULTS, **{k: v for k, v in result.items() if k in _DEFAULTS}}


@chain(normalise_case)
async def sdr_coordinator_agent(llm_response: Any, input_data: dict, context: dict) -> dict[str, Any]:
    """Open the case (hand to the strategist) or answer status; the floor
    guarantees the contract shape."""
    input_data = input_data if isinstance(input_data, dict) else {}
    parsed = _parse_json(llm_response)

    computed: dict[str, Any] = {
        "action": str(parsed.get("action", "")).strip().lower(),
        "campaign_id": str(parsed.get("campaign_id", "")) or str(input_data.get("campaign_id", "")),
        "gtm_id": str(parsed.get("gtm_id", "")) or str(input_data.get("gtm_id", "")),
        "offering_id": str(parsed.get("offering_id", "")) or str(input_data.get("offering_id", "")),
        "stage": str(parsed.get("stage", "")),
        "waiting_on": str(parsed.get("waiting_on", "")),
        "summary": str(parsed.get("summary", "")),
        # carried into the floor for action inference, then dropped
        "_input": input_data,
    }
    logger.info(
        f"sdr_coordinator_agent — action={computed['action']!r} "
        f"gtm_id={computed['gtm_id']!r} offering_id={computed['offering_id']!r}"
    )
    return {**_DEFAULTS, **computed}
