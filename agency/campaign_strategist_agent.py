"""LLM Agent — campaign strategist / the conductor (PRD §4.3).

Assembles and drives a campaign from a GTM + offering. The only agent
that coordinates sourcing → enrichment → upload. Never auto-fires off an
offering — campaigns are human/owner-initiated.

Decorators (bottom-up execution):
  @pre_compose load_campaign_inputs — pull the named GTM (via list) +
      the offering + its variants so the LLM plans over real strategy.
  @compose per-peer payloads — prospector_agent (icp_filter +
      target_size), outreach_writer_agent (offering + variants +
      sequence_shape), sales_lead_human (human-readable plan).
  @chain persist_campaign — write the campaign object + mint campaign_id
      (deterministic).

Approval: requires_approval defaults TRUE; approved=false on first pass;
approved is read from the human resume (input human_message == 'approve').

auto_discover matches function name 'campaign_strategist_agent' to YAML config.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, compose, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "campaign_id": "",
    "gtm_id": "",
    "offering_id": "",
    "target_size": 0,
    "sequence_shape": "",
    "requires_approval": True,
    "approved": False,
    "company_id": "",
}

_DEFAULT_TARGET_SIZE = 100
_DEFAULT_SEQUENCE_SHAPE = "3xemail+linkedin"


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


def _approved_from_resume(input_data: dict) -> bool:
    """True only when a human explicitly approved on the resume."""
    if not isinstance(input_data, dict):
        return False
    msg = str(input_data.get("human_message", "")).strip().lower()
    return msg == "approve"


# ── @pre_compose processor ───────────────────────────────────────────
def load_campaign_inputs(data: Any, ctx: dict) -> dict[str, Any]:
    """Pull the named GTM + offering + variants for the LLM to plan over."""
    input_data = data if isinstance(data, dict) else {}
    offering_id = input_data.get("offering_id", "")
    offering = store.get_offering(offering_id) if offering_id else {}
    company_id = (
        input_data.get("company_id")
        or offering.get("company_id")
        or input_data.get("company_name")
        or ""
    )
    gtm_id = input_data.get("gtm_id", "")
    gtms = store.list_gtm_docs(company_id) if company_id else []
    gtm = next((g for g in gtms if g.get("gtm_id") == gtm_id), (gtms[0] if gtms else {}))
    return {
        "company_id": company_id,
        "gtm_id": gtm.get("gtm_id", gtm_id),
        "gtm": gtm,
        "offering_id": offering_id,
        "offering": offering,
        "variants": offering.get("variants", []),
    }


# ── @chain step ──────────────────────────────────────────────────────
async def persist_campaign(result: dict, ctx: dict) -> dict[str, Any]:
    """Write the campaign object + mint campaign_id (deterministic)."""
    if not isinstance(result, dict):
        result = {}
    campaign = store.put_campaign({
        "campaign_id": result.get("campaign_id") or "",
        "gtm_id": result.get("gtm_id", ""),
        "offering_id": result.get("offering_id", ""),
        "company_id": result.get("company_id", ""),
        "target_size": result.get("target_size", 0),
        "sequence_shape": result.get("sequence_shape", ""),
        "requires_approval": result.get("requires_approval", True),
        "approved": result.get("approved", False),
        "icp_filter": result.get("_icp_filter", {}),
        "status": "approved" if result.get("approved") else "pending_approval",
    })
    result["campaign_id"] = campaign.get("campaign_id", result.get("campaign_id", ""))
    # The approval request travels through a human seat, whose reply carries
    # only the verdict — remember which campaign this session is about so the
    # agent behind the gate can record the verdict against it.
    store.bind_session(str((ctx or {}).get("session_id", "")), result["campaign_id"])
    logger.info(
        f"persist_campaign — campaign_id={result['campaign_id']} "
        f"requires_approval={result.get('requires_approval')} approved={result.get('approved')}"
    )
    return result


# ── @compose per-peer payloads ───────────────────────────────────────
def _to_lead_sourcing(result: dict, ctx: dict) -> dict[str, Any]:
    return {
        "campaign_id": result.get("campaign_id", ""),
        "icp_filter": result.get("_icp_filter", {}),
        "target_size": result.get("target_size", 0),
    }


def _to_lead_enrichment(result: dict, ctx: dict) -> dict[str, Any]:
    prepared = (ctx or {}).get("prepared_data", {}) or {}
    cctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}
    return {
        "campaign_id": result.get("campaign_id", ""),
        "offering": cctx.get("offering", {}),
        "variants": cctx.get("variants", []),
        "sequence_shape": result.get("sequence_shape", ""),
    }


def _to_campaign_approver(result: dict, ctx: dict) -> dict[str, Any]:
    return {
        "campaign_id": result.get("campaign_id", ""),
        "target_size": result.get("target_size", 0),
        "sequence_shape": result.get("sequence_shape", ""),
        "plan": (
            f"Campaign for offering {result.get('offering_id', '')} on GTM "
            f"{result.get('gtm_id', '')}: target {result.get('target_size', 0)} "
            f"leads, sequence {result.get('sequence_shape', '')}."
        ),
    }


@pre_compose(context_processor=load_campaign_inputs)
@compose(
    prospector_agent=_to_lead_sourcing,
    outreach_writer_agent=_to_lead_enrichment,
    sales_lead_human=_to_campaign_approver,
)
@chain(persist_campaign)
async def campaign_strategist_agent(llm_response, input_data: dict, context: dict) -> dict[str, Any]:
    """Assemble the campaign plan; chain persists + mints campaign_id."""
    if not isinstance(context, dict):
        context = {}
    if not isinstance(input_data, dict):
        input_data = {}

    prepared = context.get("prepared_data", {}) or {}
    cctx = prepared.get("context", {}) if isinstance(prepared.get("context"), dict) else {}

    parsed = _parse_json(llm_response)

    try:
        target_size = int(parsed.get("target_size", 0)) or _DEFAULT_TARGET_SIZE
    except (TypeError, ValueError):
        target_size = _DEFAULT_TARGET_SIZE

    sequence_shape = str(parsed.get("sequence_shape", "")) or _DEFAULT_SEQUENCE_SHAPE

    # Approval: default-on gate. approved is only true on a human resume.
    approved = _approved_from_resume(input_data)
    requires_approval = bool(parsed.get("requires_approval", True))
    if not approved:
        requires_approval = True  # never let the LLM waive the send gate pre-approval

    icp_filter = parsed.get("icp_filter", {})
    if not isinstance(icp_filter, dict):
        icp_filter = {}

    computed = {
        "campaign_id": str(parsed.get("campaign_id", "")),
        "gtm_id": str(parsed.get("gtm_id", "")) or cctx.get("gtm_id", ""),
        "offering_id": str(parsed.get("offering_id", "")) or cctx.get("offering_id", ""),
        "target_size": target_size,
        "sequence_shape": sequence_shape,
        "requires_approval": requires_approval,
        "approved": approved,
        "company_id": cctx.get("company_id", "") or str(parsed.get("company_id", "")),
        # Private carry consumed by persist_campaign / compose.
        "_icp_filter": icp_filter,
    }

    logger.info(
        f"campaign_strategist_agent — gtm_id={computed['gtm_id']} "
        f"offering_id={computed['offering_id']} target={target_size} approved={approved}"
    )
    return {**_DEFAULTS, **computed}
