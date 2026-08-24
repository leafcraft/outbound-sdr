"""LLM Agent — the sequence writer (PRD §4.5).

For every lead, writes a hyper-personalised multi-step sequence (email
1/2/3 + LinkedIn). The critical design: the TOOL drives, the LLM writes.
next_enrichment_task holds the cursor over leads × steps and keeps
returning done=false until the whole list is exhausted; the model writes
one step per call. This module's job is to assert/bound that the loop
actually finished and summarise the counts.

Decorators (bottom-up execution):
  @pre_compose load_list_and_offering — pull the validated lead list +
      the offering/variants + sequence shape so the model has context.
  @chain finalize_sequences — recompute completeness directly from the
      store (the same logic the enrichment_status tool reports), bound
      the loop, and summarise counts (deterministic floor over the LLM's
      self-report).

auto_discover matches function name 'outreach_writer_agent' to YAML config.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
from agency._shared import session_stash

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "campaign_id": "",
    "leads_enriched": 0,
    "sequences_written": 0,
    "all_sequences_complete": False,
    "enrichment_retry_exhausted": False,
}

# Bound the enrichment retry back-edge (Rule 3): at most this many re-runs
# before the stall is escalated to the accountable owner — NEVER an unbounded
# loop back into this agent.
_MAX_ENRICHMENT_RETRIES = 2

# Mirror the tool's default sequence shape so completeness is computed the
# same way the loop tool counts it (steps per lead).
_DEFAULT_STEPS = 4


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


def _campaign_id(input_data: dict, ctx: dict) -> str:
    if isinstance(input_data, dict) and input_data.get("campaign_id"):
        return str(input_data["campaign_id"])
    prepared = (ctx or {}).get("prepared_data", {}) or {}
    cctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    return str(cctx.get("campaign_id", ""))


def _steps_per_lead(campaign_id: str) -> int:
    """How many steps a complete sequence needs (from the campaign record)."""
    camp = store.get_campaign(campaign_id)
    shape = camp.get("sequence_steps")
    if isinstance(shape, list) and shape:
        return len(shape)
    return _DEFAULT_STEPS


def _enrichment_status(campaign_id: str, list_id: str) -> dict[str, Any]:
    """Recompute completeness from the store (mirrors the enrichment_status tool)."""
    lid = list_id or store.get_campaign(campaign_id).get("list_id", "")
    leads = store.get_lead_list(lid) if lid else []
    want = _steps_per_lead(campaign_id)
    complete = 0
    written = 0
    for ld in leads:
        steps = store.sequence_for(campaign_id, ld.get("lead_id", ""))
        written += len(steps)
        if len(steps) >= want:
            complete += 1
    return {
        "leads_total": len(leads),
        "leads_complete": complete,
        "sequences_written": written,
        "all_sequences_complete": len(leads) > 0 and complete == len(leads),
    }


# ── @pre_compose processor ───────────────────────────────────────────
def load_list_and_offering(data: Any, ctx: dict) -> dict[str, Any]:
    """Pull the validated lead list + offering/variants + sequence shape."""
    input_data = data if isinstance(data, dict) else {}
    campaign_id = str(input_data.get("campaign_id", ""))
    camp = store.get_campaign(campaign_id) if campaign_id else {}
    list_id = input_data.get("list_id", "") or camp.get("list_id", "")
    offering = store.get_offering(camp.get("offering_id", "")) if camp.get("offering_id") else {}
    return {
        "campaign_id": campaign_id,
        "list_id": list_id,
        "leads": store.get_lead_list(list_id) if list_id else [],
        "offering": offering,
        "variants": offering.get("variants", []),
        "sequence_shape": camp.get("sequence_shape", ""),
        "steps_per_lead": _steps_per_lead(campaign_id) if campaign_id else _DEFAULT_STEPS,
    }


# ── @chain step ──────────────────────────────────────────────────────
async def finalize_sequences(result: dict, ctx: dict) -> dict[str, Any]:
    """Assert/bound the loop: recompute from the store, don't trust the model."""
    if not isinstance(result, dict):
        result = {}
    prepared = (ctx or {}).get("prepared_data", {}) or {}
    cctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    campaign_id = result.get("campaign_id", "") or cctx.get("campaign_id", "")
    list_id = cctx.get("list_id", "")

    status = _enrichment_status(campaign_id, list_id)

    # Deterministic floor: trust the store's count over the LLM's self-report.
    # (These are recomputed from the GLOBAL store, so they are authoritative
    # even under instances: N — never the summed per-copy self-reports.)
    result["campaign_id"] = campaign_id
    result["leads_enriched"] = status["leads_complete"]
    result["sequences_written"] = status["sequences_written"]
    result["all_sequences_complete"] = status["all_sequences_complete"]

    # Bound the retry back-edge (Rule 3): count re-runs per session; once the
    # budget is spent, flip enrichment_retry_exhausted so the incomplete route
    # goes to the accountable owner (terminal), never back into this agent.
    if status["all_sequences_complete"]:
        result["enrichment_retry_exhausted"] = False
        logger.info(
            f"finalize_sequences — complete campaign={campaign_id} "
            f"{status['leads_complete']} leads, {status['sequences_written']} steps"
        )
    else:
        sid = str((ctx or {}).get("session_id", "")) or campaign_id or "_nosession"
        attempts = session_stash.peek(sid, "enrichment_retries")
        if attempts >= _MAX_ENRICHMENT_RETRIES:
            result["enrichment_retry_exhausted"] = True
        else:
            session_stash.bump(sid, "enrichment_retries")
            result["enrichment_retry_exhausted"] = False
        logger.warning(
            f"finalize_sequences — INCOMPLETE campaign={campaign_id} "
            f"{status['leads_complete']}/{status['leads_total']} leads done, "
            f"retries={attempts} exhausted={result['enrichment_retry_exhausted']}"
        )
    return result


@pre_compose(context_processor=load_list_and_offering)
@chain(finalize_sequences)
async def outreach_writer_agent(llm_response, input_data: dict, context: dict) -> dict[str, Any]:
    """Surface the LLM's self-report; the chain step asserts the truth."""
    if not isinstance(context, dict):
        context = {}
    if not isinstance(input_data, dict):
        input_data = {}

    parsed = _parse_json(llm_response)
    campaign_id = _campaign_id(input_data, context)

    def _int(key: str) -> int:
        try:
            return int(parsed.get(key, 0))
        except (TypeError, ValueError):
            return 0

    computed = {
        "campaign_id": str(parsed.get("campaign_id", "")) or campaign_id,
        "leads_enriched": _int("leads_enriched"),
        "sequences_written": _int("sequences_written"),
        "all_sequences_complete": bool(parsed.get("all_sequences_complete", False)),
    }

    logger.info(
        f"outreach_writer_agent — campaign={computed['campaign_id']} "
        f"self-reported {computed['leads_enriched']} leads / "
        f"{computed['sequences_written']} steps (chain will verify)"
    )
    return {**_DEFAULTS, **computed}
