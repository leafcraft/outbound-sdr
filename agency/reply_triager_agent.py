"""LLM Agent — reply triage (PRD §4.8).

Classifies each pulled reply and proposes an action + draft response.
The safety-critical bit: routing flags (needs_human / auto_closeable)
are derived deterministically from classified_as as a FLOOR — even if the
LLM under-flags, a positive/objection/question/referral still reaches a
human, and an unsub/OOO/hard-no still auto-closes. The LLM cannot route a
real conversation into the trash.

Decorators (bottom-up execution):
  @pre_compose load_thread_context — pull the original sequence + lead
      enrichment for the reply's lead so classification/drafting is in
      context (dev: best-effort via store.sequence_for).
  @chain route_replies — set needs_human / auto_closeable from
      classified_as per the PRD rules deterministically (the floor).

auto_discover matches function name 'reply_triager_agent' to YAML config.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

_VALID_CLASSES = {
    "positive", "objection", "question", "out_of_office",
    "unsubscribe", "referral", "not_interested",
}

# Per PRD §4.8: anything a person should own → human; the rest auto-close.
_NEEDS_HUMAN = {"positive", "objection", "question", "referral"}
_AUTO_CLOSEABLE = {"unsubscribe", "out_of_office", "not_interested"}

_DEFAULTS: dict[str, Any] = {
    "reply_id": "",
    "lead_id": "",
    "classified_as": "",
    "suggested_action": "",
    "draft_response": "",
    "needs_human": False,
    "auto_closeable": False,
}


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


# ── @pre_compose processor ───────────────────────────────────────────
def load_thread_context(data: Any, ctx: dict) -> dict[str, Any]:
    """Best-effort: pull the lead's original sequence so triage is in-context."""
    input_data = data if isinstance(data, dict) else {}
    reply_id = str(input_data.get("reply_id", ""))
    lead_id = str(input_data.get("lead_id", ""))
    campaign_id = str(input_data.get("campaign_id", ""))
    prior_sequence: list[dict[str, Any]] = []
    if campaign_id and lead_id:
        prior_sequence = store.sequence_for(campaign_id, lead_id)
    return {
        "reply_id": reply_id,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "reply_body": input_data.get("body", "") or input_data.get("reply_body", ""),
        "prior_sequence": prior_sequence,
    }


# ── @chain step (the deterministic routing floor) ────────────────────
async def route_replies(result: dict, ctx: dict) -> dict[str, Any]:
    """Set needs_human / auto_closeable from classified_as. Safety floor."""
    if not isinstance(result, dict):
        result = {}
    classified = str(result.get("classified_as", "")).strip().lower()
    if classified not in _VALID_CLASSES:
        # Unknown / unparseable class → route to a human, never auto-close.
        classified = classified or "question"
        if classified not in _VALID_CLASSES:
            classified = "question"
    result["classified_as"] = classified

    # Deterministic floor: OR the rule onto whatever the LLM said. The LLM
    # may RAISE a flag but cannot LOWER one below the rule.
    rule_human = classified in _NEEDS_HUMAN
    rule_auto = classified in _AUTO_CLOSEABLE
    result["needs_human"] = bool(result.get("needs_human", False)) or rule_human
    result["auto_closeable"] = bool(result.get("auto_closeable", False)) or rule_auto

    # A reply is never both — human ownership wins (no silent auto-close).
    if result["needs_human"]:
        result["auto_closeable"] = False

    # Suggested action floor when the LLM left it blank.
    if not result.get("suggested_action"):
        result["suggested_action"] = (
            "book" if classified == "positive"
            else "reply" if rule_human
            else "suppress" if classified in {"unsubscribe", "not_interested"}
            else "nurture"
        )

    logger.info(
        f"route_replies — reply_id={result.get('reply_id', '')} class={classified} "
        f"needs_human={result['needs_human']} auto_closeable={result['auto_closeable']}"
    )
    return result


@pre_compose(context_processor=load_thread_context)
@chain(route_replies)
async def reply_triager_agent(llm_response, input_data: dict, context: dict) -> dict[str, Any]:
    """Surface the LLM's classification; the chain step enforces routing."""
    if not isinstance(context, dict):
        context = {}
    if not isinstance(input_data, dict):
        input_data = {}

    prepared = context.get("prepared_data", {}) or {}
    tctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}

    parsed = _parse_json(llm_response)

    computed = {
        "reply_id": str(parsed.get("reply_id", "")) or tctx.get("reply_id", "") or str(input_data.get("reply_id", "")),
        "lead_id": str(parsed.get("lead_id", "")) or tctx.get("lead_id", "") or str(input_data.get("lead_id", "")),
        "classified_as": str(parsed.get("classified_as", "")).strip().lower(),
        "suggested_action": str(parsed.get("suggested_action", "")).strip().lower(),
        "draft_response": str(parsed.get("draft_response", "")),
        "needs_human": bool(parsed.get("needs_human", False)),
        "auto_closeable": bool(parsed.get("auto_closeable", False)),
    }

    logger.info(
        f"reply_triager_agent — reply_id={computed['reply_id']} "
        f"llm_class={computed['classified_as']!r} (route_replies enforces floor)"
    )
    return {**_DEFAULTS, **computed}
