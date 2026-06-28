"""LLM Agent — offering & messaging architect (PRD §4.2).

Turns a strategy idea into a sellable, message-ready OFFERING (pains,
solution, outcomes, objections) plus A/B angle × persona × industry
variants kept "in hand" for campaigns to draw from.

Decorators (bottom-up execution):
  @pre_compose load_ideas_without_offerings — pull offering-candidate
      ideas that still lack an offering (drives the weekly sweep) + the
      parent GTM context, so the model builds from real ideas.
  @conditional_chain expand_variants — only when variant_count < target;
      don't re-expand an already-complete offering.
  @chain persist_offering — write the offering + all variants to the
      store (deterministic), deduping against existing.

auto_discover matches function name 'offer_architect_agent' to YAML config.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, conditional_chain, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

_VARIANT_TARGET = 4  # build out to at least this many variants per offering

_DEFAULTS: dict[str, Any] = {
    "offering_id": "",
    "idea_id": "",
    "name": "",
    "industry": "",
    "variant_count": 0,
    "ready_for_campaign": False,
    "company_id": "",
}


def _company_id(input_data: dict, context: dict) -> str:
    if isinstance(input_data, dict):
        cid = input_data.get("company_id") or input_data.get("company_name")
        if cid:
            return str(cid)
    return str((context or {}).get("session_id", "") or "unknown_company")


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
def load_ideas_without_offerings(data: Any, ctx: dict) -> dict[str, Any]:
    """Pull offering-candidate ideas that have no offering yet + GTM context."""
    input_data = data if isinstance(data, dict) else {}
    company_id = _company_id(input_data, ctx or {})
    pending = store.ideas_without_offerings(company_id)
    # Focus on a specific idea if one was named, else the whole backlog.
    target_id = input_data.get("idea_id", "")
    if target_id:
        focused = [i for i in pending if i.get("idea_id") == target_id]
        pending = focused or pending
    return {
        "company_id": company_id,
        "ideas_without_offerings": pending,
        "pending_count": len(pending),
        "gtm_context": store.list_gtm_docs(company_id),
    }


# ── @conditional_chain predicate + step ──────────────────────────────
def _needs_more_variants(result: dict, ctx: dict) -> bool:
    if not isinstance(result, dict):
        return False
    try:
        return int(result.get("variant_count", 0)) < _VARIANT_TARGET
    except (TypeError, ValueError):
        return True


async def expand_variants(result: dict, ctx: dict) -> dict[str, Any]:
    """Top up to _VARIANT_TARGET variants so campaigns have angles to draw on."""
    if not isinstance(result, dict):
        return result
    have = result.get("_variants")
    if not isinstance(have, list):
        have = []
    industry = result.get("industry", "") or "general"
    base_personas = ["champion", "economic_buyer", "end_user", "skeptic"]
    while len(have) < _VARIANT_TARGET:
        idx = len(have)
        have.append({
            "variant_id": store.new_id("var"),
            "persona": base_personas[idx % len(base_personas)],
            "industry": industry,
            "angle": f"auto_angle_{idx + 1}",
            "ab_label": "A" if idx % 2 == 0 else "B",
        })
    result["_variants"] = have
    result["variant_count"] = len(have)
    # Re-persist with the expanded variant set.
    if result.get("offering_id"):
        store.put_offering({
            "offering_id": result["offering_id"],
            "variants": have,
        })
    logger.info(f"expand_variants — topped up to {len(have)} variants")
    return result


# ── @chain step ──────────────────────────────────────────────────────
async def persist_offering(result: dict, ctx: dict) -> dict[str, Any]:
    """Write the offering + variants to the store (deterministic)."""
    if not isinstance(result, dict):
        result = {}
    variants = result.get("_variants")
    if not isinstance(variants, list):
        variants = []
    offering = store.put_offering({
        "offering_id": result.get("offering_id") or "",
        "idea_id": result.get("idea_id", ""),
        "name": result.get("name", ""),
        "industry": result.get("industry", ""),
        "company_id": result.get("company_id", ""),
        "variants": variants,
    })
    result["offering_id"] = offering.get("offering_id", result.get("offering_id", ""))
    result["variant_count"] = len(variants)
    logger.info(
        f"persist_offering — offering_id={result['offering_id']} "
        f"idea_id={result.get('idea_id', '')} variants={len(variants)}"
    )
    return result


@pre_compose(context_processor=load_ideas_without_offerings)
@conditional_chain(_needs_more_variants, expand_variants)
@chain(persist_offering)
async def offer_architect_agent(llm_response, input_data: dict, context: dict) -> dict[str, Any]:
    """Shape the offering from the LLM; chain persists, conditional expands."""
    if not isinstance(context, dict):
        context = {}
    if not isinstance(input_data, dict):
        input_data = {}

    prepared = context.get("prepared_data", {}) or {}
    off_ctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    company_id = off_ctx.get("company_id") or _company_id(input_data, context)

    parsed = _parse_json(llm_response)

    # idea_id: prefer the model's, else fall back to the first pending idea.
    idea_id = str(parsed.get("idea_id", "")) or input_data.get("idea_id", "")
    if not idea_id:
        pending = off_ctx.get("ideas_without_offerings", []) or []
        if pending:
            idea_id = pending[0].get("idea_id", "")

    variants = parsed.get("variants", [])
    if not isinstance(variants, list):
        variants = []
    try:
        variant_count = int(parsed.get("variant_count", len(variants)))
    except (TypeError, ValueError):
        variant_count = len(variants)

    computed = {
        "offering_id": str(parsed.get("offering_id", "")),
        "idea_id": idea_id,
        "name": str(parsed.get("name", "")),
        "industry": str(parsed.get("industry", "")),
        "variant_count": max(variant_count, len(variants)),
        "ready_for_campaign": bool(parsed.get("ready_for_campaign", False)),
        "company_id": company_id,
        # Private carry consumed by persist_offering / expand_variants.
        "_variants": variants,
    }

    logger.info(
        f"offer_architect_agent — idea_id={idea_id} name={computed['name']!r} "
        f"variants={computed['variant_count']}"
    )
    return {**_DEFAULTS, **computed}
