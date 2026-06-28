"""LLM Agent — GTM strategist / team head (PRD §4.1).

Researches the onboarded company and owns its go-to-market strategy over
time: first run authors a GTM doc; weekly it appends ideas/events to the
matching GTM or spins a NEW one when the strategy itself shifted, and
promotes offering-worthy ideas downstream.

Decorators (bottom-up execution):
  @pre_compose load_gtm_context — pull the company record + ALL existing
      GTM docs so the model reasons over "what strategies already exist"
      when deciding append-vs-new (the "pull what GTMs are available" step).
  @compose two payloads — offer_architect_agent gets the flagged idea(s)
      + GTM context; audit_logger_agent gets the decision record.
  @chain persist_gtm — execute the append-vs-new decision against the
      store (deterministic write, not left to the LLM); stamp version.

auto_discover matches function name 'gtm_strategist_agent' to YAML config.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, compose, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

# Full-shape floor — every declared yield, so every return path is complete.
_DEFAULTS: dict[str, Any] = {
    "gtm_id": "",
    "gtm_changed": False,
    "has_offering_candidate": False,
    "offering_candidate_ids": [],
    "market_thesis": "",
    "idea_count": 0,
    "version": 0,
    "company_id": "",
}

_MAX_IDEAS = 50  # clamp idea_count to a sane bound


def _company_id(input_data: dict, context: dict) -> str:
    """Derive a stable company_id from the onboarding input."""
    if isinstance(input_data, dict):
        cid = (
            input_data.get("company_id")
            or input_data.get("company_name")
            or input_data.get("website")
        )
        if cid:
            return str(cid)
    return str((context or {}).get("session_id", "") or "unknown_company")


def _parse_json(text: Any) -> dict[str, Any]:
    """Best-effort extraction of the LLM's JSON contract object."""
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
def load_gtm_context(data: Any, ctx: dict) -> dict[str, Any]:
    """Pull the company record + every existing GTM doc for this company."""
    input_data = data if isinstance(data, dict) else {}
    company_id = _company_id(input_data, ctx or {})
    existing = store.list_gtm_docs(company_id)
    return {
        "company_id": company_id,
        "company_record": {
            "company_name": input_data.get("company_name", ""),
            "website": input_data.get("website", ""),
            "linkedin": input_data.get("linkedin", ""),
            "extra": input_data.get("extra", {}),
        },
        "existing_gtms": existing,
        "existing_gtm_count": len(existing),
    }


# ── @chain step (after LLM, persists the decision) ───────────────────
async def persist_gtm(result: dict, ctx: dict) -> dict[str, Any]:
    """Execute the append-vs-new write the LLM decided. Deterministic."""
    if not isinstance(result, dict):
        result = {}
    prepared = (ctx or {}).get("prepared_data", {}) or {}
    gtm_ctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    company_id = result.get("company_id") or gtm_ctx.get("company_id", "") or "unknown_company"
    existing = gtm_ctx.get("existing_gtms", []) or []

    gtm_changed = bool(result.get("gtm_changed", False))
    ideas = result.get("_ideas")
    if not isinstance(ideas, list):
        ideas = []

    if gtm_changed or not existing:
        # New / materially-shifted strategy → write a fresh GTM doc.
        doc = store.put_gtm_doc({
            "company_id": company_id,
            "market_thesis": result.get("market_thesis", ""),
            "ideas": ideas,
        })
        result["gtm_id"] = doc.get("gtm_id", result.get("gtm_id", ""))
        result["version"] = int(doc.get("version", 1))
        result["gtm_changed"] = True if not existing else gtm_changed
    else:
        # Stable strategy → append ideas to the matched GTM.
        matched_id = result.get("gtm_id") or existing[0].get("gtm_id", "")
        doc = store.append_ideas(matched_id, ideas) if matched_id else (existing[0] or {})
        result["gtm_id"] = doc.get("gtm_id", matched_id)
        result["version"] = int(doc.get("version", 0))

    result.pop("_ideas", None)
    logger.info(
        f"persist_gtm — gtm_id={result['gtm_id']} changed={result['gtm_changed']} "
        f"version={result['version']} ideas={len(ideas)}"
    )
    return result


# ── @compose per-peer payloads ───────────────────────────────────────
def _to_offering_builder(result: dict, ctx: dict) -> dict[str, Any]:
    prepared = (ctx or {}).get("prepared_data", {}) or {}
    gtm_ctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    return {
        "gtm_id": result.get("gtm_id", ""),
        "company_id": result.get("company_id", ""),
        "offering_candidate_ids": result.get("offering_candidate_ids", []),
        "market_thesis": result.get("market_thesis", ""),
        "gtm_context": gtm_ctx.get("existing_gtms", []),
    }


def _to_audit(result: dict, ctx: dict) -> dict[str, Any]:
    return {
        "session_id": (ctx or {}).get("session_id", ""),
        "payload": {
            "event": "gtm_decision",
            "gtm_id": result.get("gtm_id", ""),
            "company_id": result.get("company_id", ""),
            "gtm_changed": result.get("gtm_changed", False),
            "has_offering_candidate": result.get("has_offering_candidate", False),
            "idea_count": result.get("idea_count", 0),
            "version": result.get("version", 0),
        },
    }


@pre_compose(context_processor=load_gtm_context)
@compose(offer_architect_agent=_to_offering_builder, audit_logger_agent=_to_audit)
@chain(persist_gtm)
async def gtm_strategist_agent(llm_response, input_data: dict, context: dict) -> dict[str, Any]:
    """Decide append-vs-new + promote ideas; the chain step persists."""
    if not isinstance(context, dict):
        context = {}
    if not isinstance(input_data, dict):
        input_data = {}

    prepared = context.get("prepared_data", {}) or {}
    gtm_ctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    company_id = gtm_ctx.get("company_id") or _company_id(input_data, context)

    parsed = _parse_json(llm_response)

    candidate_ids = parsed.get("offering_candidate_ids", [])
    if not isinstance(candidate_ids, list):
        candidate_ids = []
    has_candidate = bool(parsed.get("has_offering_candidate", False)) or bool(candidate_ids)

    # idea_count: trust the model's count but clamp to a sane bound.
    try:
        idea_count = int(parsed.get("idea_count", 0))
    except (TypeError, ValueError):
        idea_count = 0
    idea_count = max(0, min(idea_count, _MAX_IDEAS))

    computed = {
        "gtm_id": str(parsed.get("gtm_id", "")),
        "gtm_changed": bool(parsed.get("gtm_changed", False)),
        "has_offering_candidate": has_candidate,
        "offering_candidate_ids": candidate_ids,
        "market_thesis": str(parsed.get("market_thesis", "")),
        "idea_count": idea_count,
        "version": int(parsed.get("version", 0)) if str(parsed.get("version", "")).isdigit() else 0,
        "company_id": company_id,
        # Private carry: structured ideas the LLM produced (consumed by persist_gtm).
        "_ideas": parsed.get("ideas", []) if isinstance(parsed.get("ideas"), list) else [],
    }

    logger.info(
        f"gtm_strategist_agent — company_id={company_id} candidates={len(candidate_ids)} "
        f"idea_count={idea_count} gtm_changed={computed['gtm_changed']}"
    )
    return {**_DEFAULTS, **computed}
