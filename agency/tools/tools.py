"""Runtime tools for the outbound-SDR mesh (@global_tool).

The centerpiece is the ENRICHMENT LOOP (next_enrichment_task /
write_sequence_step / enrichment_status). Per the design, the TOOL owns
the invariant "every lead × every sequence step gets written" — it holds
the cursor and keeps returning done=false until the whole list is
exhausted. The LLM only writes one step at a time; it cannot end the job
early because the cursor, not the model, decides when it's finished.

Research/campaign tools are dev stand-ins; swap their bodies for real
SERP/Surfer/CRM calls (or move them to MCP connectors) in production.
"""
from __future__ import annotations

import threading
from typing import Any

from leafmesh import LeafMeshLogger, global_tool

from agency._shared import store

logger = LeafMeshLogger(__name__)

# Per-campaign cursor over (lead_index, step_index). In-process dev state;
# back it with Redis for multi-replica. The sequence shape (steps) is read
# from the campaign record so the tool knows how many steps per lead.
_LOCK = threading.Lock()
_CURSOR: dict[str, dict[str, int]] = {}

# Default sequence shape if the campaign didn't pin one.
_DEFAULT_STEPS = [
    {"step_no": 1, "channel": "email"},
    {"step_no": 2, "channel": "email"},
    {"step_no": 3, "channel": "email"},
    {"step_no": 4, "channel": "linkedin"},
]


def _steps_for(campaign_id: str) -> list[dict[str, Any]]:
    camp = store.get_campaign(campaign_id)
    shape = camp.get("sequence_steps")
    return shape if isinstance(shape, list) and shape else _DEFAULT_STEPS


# ── Backlog fan-out: atomic claim (one batch per copy) ───────────────
@global_tool()
def claim_leads(campaign_id: str, list_id: str = "", batch: int = 4) -> dict[str, Any]:
    """Atomically claim a DISJOINT batch of leads for THIS enrichment copy.

    With scaling.copies.count: N, several copies of the writer run in parallel; each calls
    this to grab its own batch so no lead is written twice. Returns the claimed
    leads AND the sequence step plan. When the backlog is exhausted returns
    done=true (claim empty). Announces available:false when there is no list to
    claim from — never a fabricated lead.

    Args:
        campaign_id: The campaign whose list to claim from.
        list_id: The lead list id (falls back to the campaign record).
        batch: Max leads to claim in this call.
    """
    lid = list_id or store.get_campaign(campaign_id).get("list_id", "")
    if not lid or not store.get_lead_list(lid):
        return {"available": False, "done": True, "claimed": [], "count": 0,
                "reason": "no lead list to claim from (source leads first / wire the vault connection 'apollo_mcp')"}
    picked = store.claim_leads(campaign_id, lid, batch)
    return {
        "available": True,
        "done": len(picked) == 0,
        "claimed": picked,
        "count": len(picked),
        "steps": _steps_for(campaign_id),
        "list_id": lid,
    }


# ── Enrichment loop (single-copy cursor; retained for direct use) ─────
@global_tool()
def next_enrichment_task(campaign_id: str, list_id: str = "") -> dict[str, Any]:
    """Return the NEXT {lead, step_no, channel, prior_steps} to write, or
    {"done": true} when every lead × every step is complete. The job is
    not finished until this returns done=true — keep calling it."""
    leads = store.get_lead_list(list_id) if list_id else []
    if not leads:
        # list_id may live on the campaign record
        lid = store.get_campaign(campaign_id).get("list_id", "")
        leads = store.get_lead_list(lid)
    steps = _steps_for(campaign_id)
    total_leads, total_steps = len(leads), len(steps)
    if total_leads == 0:
        return {"done": True, "reason": "no_leads"}

    with _LOCK:
        cur = _CURSOR.setdefault(campaign_id, {"lead": 0, "step": 0})
        li, si = cur["lead"], cur["step"]
        if li >= total_leads:
            return {"done": True, "leads_total": total_leads}
        lead = leads[li]
        step = steps[si]
        prior = store.sequence_for(campaign_id, lead.get("lead_id", str(li)))
        # advance cursor
        si += 1
        if si >= total_steps:
            si = 0
            li += 1
        cur["lead"], cur["step"] = li, si

    return {
        "done": False,
        "lead": lead,
        "step_no": step["step_no"],
        "channel": step["channel"],
        "prior_steps": prior,
        "progress": f"lead {li}/{total_leads}",
    }


@global_tool()
def write_sequence_step(campaign_id: str, lead_id: str, step_no: int,
                        channel: str, subject: str, body: str,
                        personalization_basis: str = "") -> dict[str, Any]:
    """Persist one written step. Called by the LLM after it writes."""
    store.put_sequence_step(campaign_id, lead_id, {
        "step_no": step_no, "channel": channel, "subject": subject,
        "body": body, "personalization_basis": personalization_basis,
    })
    return {"saved": True, "lead_id": lead_id, "step_no": step_no}


@global_tool()
def enrichment_status(campaign_id: str, list_id: str = "") -> dict[str, Any]:
    """Report completeness across the list × steps."""
    lid = list_id or store.get_campaign(campaign_id).get("list_id", "")
    leads = store.get_lead_list(lid)
    steps = _steps_for(campaign_id)
    want = len(steps)
    complete = sum(
        1 for ld in leads
        if len(store.sequence_for(campaign_id, ld.get("lead_id", ""))) >= want
    )
    written = sum(len(store.sequence_for(campaign_id, ld.get("lead_id", ""))) for ld in leads)
    return {
        "campaign_id": campaign_id,
        "leads_total": len(leads),
        "leads_complete": complete,
        "sequences_written": written,
        "all_sequences_complete": len(leads) > 0 and complete == len(leads),
    }


# ── GTM research tools (dev stand-ins) ───────────────────────────────
@global_tool()
def web_research(query: str) -> dict[str, Any]:
    """Internet research (SERP/Surfer). Dev stub returns an empty,
    clearly-marked result; wire the vault connection 'serp_mcp' for live research."""
    logger.info(f"[web_research] {query!r} (dev stub — wire SERP/Surfer)")
    return {"query": query, "results": [], "note": "dev stub — wire the vault connection 'serp_mcp'"}


@global_tool()
def find_market_events(company_id: str) -> dict[str, Any]:
    """Relevant market/company events for the weekly GTM sweep (dev stub)."""
    return {"company_id": company_id, "events": [], "note": "dev stub — wire your events source"}


@global_tool()
def assemble_campaign(gtm_id: str, offering_id: str, target_size: int = 100,
                      sequence_shape: str = "3xemail+linkedin") -> dict[str, Any]:
    """Lock a campaign plan and persist it."""
    steps = _DEFAULT_STEPS
    camp = store.put_campaign({
        "gtm_id": gtm_id, "offering_id": offering_id,
        "target_size": target_size, "sequence_shape": sequence_shape,
        "sequence_steps": steps, "status": "planned",
    })
    return {"campaign_id": camp["campaign_id"], "target_size": target_size,
            "sequence_shape": sequence_shape}
