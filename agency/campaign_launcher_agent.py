"""campaign_launcher_agent — the FINISHER / send actuator.

auto_discover matches `campaign_launcher_agent`. The launcher sits BEHIND the
send gate (sales_lead approval). It never approves — it re-checks that the
campaign was actually approved and is complete, then launches it. It is the
concrete "@chain fails closed":

  a) approval RE-CHECK — reaching this agent does NOT mean approved. An
     allow-token allowlist + block-token denylist over the recovered verdict,
     PLUS the campaign's approved flag on record; an UNKNOWN/ambiguous verdict
     fails closed.
  b) deterministic invariants a human can't waive: approved on record, leads
     validated, every lead's sequence complete, and no double-launch.
  c) render the real launch pack to ./out; on a tripped floor, DELETE the
     partial and record status=blocked with reasons — never a half-launch.
  d) dev-store fallback that ANNOUNCES itself: with no sequencer wired the pack
     is staged locally (status=staged, launched=false, real_send=false) — the
     launcher NEVER fakes a real send ack.

Two-layer defense: the body decides + short-circuits; the @chain floor
re-checks independently before anything is recorded downstream.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, chain

from agency._shared import store

logger = LeafMeshLogger(__name__)

_OUT_DIR = Path("./out")
_APPROVE_TOKENS = frozenset({"approved", "approve", "yes", "ok", "sign", "signed"})
_BLOCK_TOKENS = frozenset({"rejected", "reject", "no", "deny", "denied",
                           "held", "held_for_review", "hold"})
_DEFAULT_STEPS = 4


def _pick(input_data: dict[str, Any], key: str, default: Any) -> Any:
    """Shape-tolerant read: input_data, then upstream_yields (flat or keyed)."""
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
    return default


def _signal(input_data: dict) -> str:
    """The human verdict token, from wherever the SDK delivered it."""
    sources = [input_data]
    up = input_data.get("upstream_yields")
    if isinstance(up, dict):
        sources.extend(v for v in up.values() if isinstance(v, dict))
    for src in sources:
        for key in ("human_message", "human_decision", "decision", "message"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


def _steps_per_lead(campaign: dict) -> int:
    shape = campaign.get("sequence_steps")
    if isinstance(shape, list) and shape:
        return len(shape)
    return _DEFAULT_STEPS


def _connector_wired() -> bool:
    return bool(os.getenv("SEQUENCER_MCP_URL"))


def _render_pack(campaign: dict, complete_leads: list[dict], real_send: bool) -> str:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    cid = str(campaign.get("campaign_id") or "campaign")
    path = _OUT_DIR / f"{cid}_launch_pack.md"
    lines = [
        f"# Campaign launch pack — {cid}",
        f"Offering: {campaign.get('offering_id', '')}   GTM: {campaign.get('gtm_id', '')}",
        f"Target size: {campaign.get('target_size', 0)}   Sequence: {campaign.get('sequence_shape', '')}",
        f"Approved: {campaign.get('approved')}   Sequences ready: {len(complete_leads)}",
        "",
        "## Scheduled sequences (first-step preview)",
    ]
    for ld in complete_leads:
        steps = store.sequence_for(cid, ld.get("lead_id", ""))
        first = steps[0] if steps else {}
        lines.append(
            f"- {ld.get('name','')} · {ld.get('company','')} — "
            f"\"{first.get('subject','(no subject)')}\" ({len(steps)} steps)"
        )
    lines += ["", ("_Launched to the sequencer._" if real_send else
                   "_STAGED LOCALLY — no sequencer wired (real_send=false). "
                   "Set SEQUENCER_MCP_URL and uncomment the connector to send._")]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _delete_partial(result: dict) -> None:
    p = result.get("artifact_path")
    if p:
        Path(p).unlink(missing_ok=True)
        result["artifact_path"] = ""


def enforce_launch_floor(result: Any, context: dict) -> dict:
    """The finisher floor — launch ONLY an approved, complete campaign; delete
    the pack on any trip. Re-checks independently of the body."""
    result = result if isinstance(result, dict) else {}
    result.setdefault("block_reasons", [])
    reasons: list[str] = list(result.get("block_reasons") or [])

    campaign = result.get("_campaign") or {}
    signal = str(result.get("_signal", ""))

    # (a) approval re-check — fail closed.
    approved_on_record = bool(campaign.get("approved")) and \
        str(campaign.get("status", "")).lower() in ("approved", "launched", "")
    if signal in _BLOCK_TOKENS:
        approved_on_record = False
        reasons.append("approver rejected / held")
    if not approved_on_record and signal not in _APPROVE_TOKENS:
        if not bool(campaign.get("approved")):
            reasons.append("no approval on record (fail closed)")

    approved = approved_on_record or (signal in _APPROVE_TOKENS and bool(campaign.get("approved")))

    # (b) deterministic invariants.
    if store.is_launched(str(campaign.get("campaign_id") or "")) and not result.get("_reissue_ok"):
        reasons.append("campaign already launched (no double-issue)")
    if not result.get("sequences_ready"):
        reasons.append("sequences incomplete for the list")

    result["approved"] = bool(approved)

    if reasons or not approved:
        result["status"] = "blocked"
        result["launched"] = False
        result["scheduled_count"] = 0
        result["block_reasons"] = sorted(set(reasons)) or ["not approved (fail closed)"]
        _delete_partial(result)
        result.pop("_campaign", None); result.pop("_signal", None); result.pop("_reissue_ok", None)
        logger.info(f"[launcher] floor BLOCKED campaign={campaign.get('campaign_id')}: {result['block_reasons']}")
        return result

    # Passed the floor. The body already rendered the pack + set status/launched
    # (real send vs staged). Record the outcome in the dev ledger + outbox.
    cid = str(campaign.get("campaign_id") or "")
    store.mark_launched(cid, {"status": result.get("status"), "launched": result.get("launched"),
                              "scheduled_count": result.get("scheduled_count", 0)})
    store.record_outbox({"campaign_id": cid, "action": "launch",
                         "status": result.get("status"), "artifact_path": result.get("artifact_path", "")})
    store.append_ledger({"campaign_id": cid, "event": "campaign_launched",
                         "status": result.get("status"), "launched": result.get("launched")})
    result.pop("_campaign", None); result.pop("_signal", None); result.pop("_reissue_ok", None)
    logger.info(f"[launcher] {result.get('status')} campaign={cid} -> {result.get('artifact_path')}")
    return result


@chain(enforce_launch_floor)
async def campaign_launcher_agent(llm_response: Any, input_data: dict, context: dict) -> dict[str, Any]:
    """Recover the campaign, verify completeness, render the pack; the floor
    re-checks approval + invariants and fails closed."""
    input_data = input_data if isinstance(input_data, dict) else {}
    campaign_id = str(_pick(input_data, "campaign_id", ""))
    campaign = store.get_campaign(campaign_id) if campaign_id else {}
    signal = _signal(input_data)

    out: dict[str, Any] = {
        "campaign_id": campaign_id,
        "approved": False,
        "sequences_ready": False,
        "launched": False,
        "scheduled_count": 0,
        "artifact_path": "",
        "status": "",
        "block_reasons": [],
        "_campaign": campaign,
        "_signal": signal,
    }

    if not campaign:
        out["status"] = "blocked"
        out["block_reasons"] = ["campaign not found (fail closed)"]
        return out

    # Completeness from the store (authoritative — never the writer's self-report).
    lid = campaign.get("list_id", "") or str(_pick(input_data, "list_id", ""))
    status = store.sequences_complete(campaign_id, lid, _steps_per_lead(campaign))
    complete_leads = [ld for ld in store.get_lead_list(lid)
                      if len(store.sequence_for(campaign_id, ld.get("lead_id", ""))) >= _steps_per_lead(campaign)]
    out["sequences_ready"] = bool(status["all_complete"])
    out["scheduled_count"] = status["leads_complete"]

    # Provisional approval read (the floor makes the binding decision).
    approved_provisional = bool(campaign.get("approved")) and signal not in _BLOCK_TOKENS

    if out["sequences_ready"] and approved_provisional and not store.is_launched(campaign_id):
        real_send = _connector_wired()  # dev: staged locally, never a faked ack
        out["artifact_path"] = _render_pack(campaign, complete_leads, real_send)
        out["launched"] = bool(real_send)
        out["status"] = "launched" if real_send else "staged"

    return out
