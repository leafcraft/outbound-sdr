"""Dev-mode store for the outbound-SDR artifacts.

Lets the LLM chain (GTM → offering → campaign → enrichment) run end to
end BEFORE any connector is wired. Everything here is a stand-in for a
real system of record — GTM/offering/campaign belong in your CRM or a
strategy store; lead lists in Apollo; sequences in your sequencer.
Replace the read/write calls with connector calls in production; the
shape the rest of the mesh reads stays the same.

Single-process JSON files under ./data/. Swap for Redis/Postgres for a
multi-replica deployment — the function signatures are the contract.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DIR = Path("./data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _path(kind: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{kind}.json"


def _load(kind: str) -> dict[str, Any]:
    p = _path(kind)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save(kind: str, data: dict[str, Any]) -> None:
    p = _path(kind)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


# ── GTM docs ─────────────────────────────────────────────────────────
def list_gtm_docs(company_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        docs = _load("gtm").values()
    return [d for d in docs if d.get("company_id") == company_id]


def put_gtm_doc(doc: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        store = _load("gtm")
        gid = doc.get("gtm_id") or _new_id("gtm")
        doc["gtm_id"] = gid
        doc["version"] = int(doc.get("version", 0)) + 1
        doc["updated_at"] = _now()
        store[gid] = {**store.get(gid, {}), **doc}
        _save("gtm", store)
        return store[gid]


def append_ideas(gtm_id: str, ideas: list[dict[str, Any]]) -> dict[str, Any]:
    with _LOCK:
        store = _load("gtm")
        doc = store.get(gtm_id) or {"gtm_id": gtm_id, "ideas": []}
        doc.setdefault("ideas", []).extend(ideas)
        doc["version"] = int(doc.get("version", 0)) + 1
        doc["updated_at"] = _now()
        store[gtm_id] = doc
        _save("gtm", store)
        return doc


# ── Offerings ────────────────────────────────────────────────────────
def ideas_without_offerings(company_id: str) -> list[dict[str, Any]]:
    built = {o.get("idea_id") for o in _load("offerings").values()}
    out: list[dict[str, Any]] = []
    for doc in list_gtm_docs(company_id):
        for idea in doc.get("ideas", []):
            if idea.get("is_offering_candidate") and idea.get("idea_id") not in built:
                out.append({**idea, "gtm_id": doc.get("gtm_id")})
    return out


def put_offering(offering: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        store = _load("offerings")
        oid = offering.get("offering_id") or _new_id("off")
        offering["offering_id"] = oid
        offering["updated_at"] = _now()
        store[oid] = {**store.get(oid, {}), **offering}
        _save("offerings", store)
        return store[oid]


def get_offering(offering_id: str) -> dict[str, Any]:
    return _load("offerings").get(offering_id, {})


# ── Campaigns ────────────────────────────────────────────────────────
def put_campaign(campaign: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        store = _load("campaigns")
        cid = campaign.get("campaign_id") or _new_id("camp")
        campaign["campaign_id"] = cid
        campaign["updated_at"] = _now()
        store[cid] = {**store.get(cid, {}), **campaign}
        _save("campaigns", store)
        return store[cid]


def get_campaign(campaign_id: str) -> dict[str, Any]:
    return _load("campaigns").get(campaign_id, {})


# ── Campaign approval (the send gate's verdict, on record) ───────────
#    The Sales Lead's reply is the approval. It is written here by the first
#    agent behind the gate (prospector_agent) from the reply itself, and the
#    launcher's floor re-reads it. Persisted (not in-process): an approval can
#    take a business day, and the app may restart in between.
def bind_session(session_id: str, campaign_id: str) -> None:
    """Remember which campaign a session's approval request is about."""
    if not session_id or not campaign_id:
        return
    with _LOCK:
        sessions = _load("sessions")
        sessions[session_id] = {"campaign_id": campaign_id, "updated_at": _now()}
        _save("sessions", sessions)


def campaign_for_session(session_id: str) -> str:
    return str((_load("sessions").get(session_id or "") or {}).get("campaign_id", ""))


def record_approval(campaign_id: str, approver: str, verdict: str) -> dict[str, Any]:
    """Record the approver's verdict on the campaign. Only 'approve' approves;
    anything else leaves the campaign unapproved (status 'not_approved')."""
    with _LOCK:
        store = _load("campaigns")
        camp = store.get(campaign_id)
        if not camp:
            return {}
        ok = str(verdict or "").strip().lower() == "approve"
        camp.update({
            "approved": ok,
            "status": "approved" if ok else "not_approved",
            "approved_by": approver if ok else "",
            "approval_verdict": str(verdict or ""),
            "approval_recorded_at": _now(),
        })
        store[campaign_id] = camp
        _save("campaigns", store)
        return camp


# ── Lead lists ───────────────────────────────────────────────────────
def put_lead_list(list_id: str, campaign_id: str, leads: list[dict[str, Any]]) -> str:
    with _LOCK:
        store = _load("lead_lists")
        lid = list_id or _new_id("list")
        store[lid] = {"list_id": lid, "campaign_id": campaign_id,
                      "leads": leads, "updated_at": _now()}
        _save("lead_lists", store)
        return lid


def get_lead_list(list_id: str) -> list[dict[str, Any]]:
    return (_load("lead_lists").get(list_id) or {}).get("leads", [])


# ── Enriched sequences ───────────────────────────────────────────────
def put_sequence_step(campaign_id: str, lead_id: str, step: dict[str, Any]) -> None:
    with _LOCK:
        store = _load("sequences")
        key = f"{campaign_id}:{lead_id}"
        rec = store.get(key) or {"campaign_id": campaign_id, "lead_id": lead_id, "steps": []}
        rec["steps"].append(step)
        store[key] = rec
        _save("sequences", store)


def sequence_for(campaign_id: str, lead_id: str) -> list[dict[str, Any]]:
    return (_load("sequences").get(f"{campaign_id}:{lead_id}") or {}).get("steps", [])


def sequences_complete(campaign_id: str, list_id: str, steps_per_lead: int) -> dict[str, Any]:
    """Global completeness for a campaign's list — the authoritative count the
    launcher's floor and the writer's @chain both read (never the LLM's
    self-report)."""
    leads = get_lead_list(list_id)
    complete = 0
    written = 0
    for ld in leads:
        n = len(sequence_for(campaign_id, ld.get("lead_id", "")))
        written += n
        if n >= steps_per_lead:
            complete += 1
    return {
        "leads_total": len(leads),
        "leads_complete": complete,
        "sequences_written": written,
        "all_complete": len(leads) > 0 and complete == len(leads),
    }


# ── Atomic lead claiming (fan-out across outreach_writer copies) ───
#    Each copy calls claim_leads to pop a DISJOINT batch so copies never
#    write the same lead twice. In-process + locked (dev); swap for a Redis
#    SPOP / LPOP for multi-replica — the signature is the contract.
def claim_leads(campaign_id: str, list_id: str, batch: int) -> list[dict[str, Any]]:
    """Atomically claim up to `batch` unclaimed leads for this campaign.
    Returns the claimed lead dicts ([] when the backlog is exhausted)."""
    with _LOCK:
        claims = _load("claims")
        claimed_ids = set((claims.get(campaign_id) or {}).get("claimed", []))
        leads = get_lead_list(list_id)
        picked: list[dict[str, Any]] = []
        for ld in leads:
            lid = ld.get("lead_id", "")
            if lid and lid not in claimed_ids:
                picked.append(ld)
                claimed_ids.add(lid)
                if len(picked) >= max(1, int(batch or 1)):
                    break
        claims[campaign_id] = {"claimed": sorted(claimed_ids), "updated_at": _now()}
        _save("claims", claims)
        return picked


# ── Launch tracking (no double-issue) + outbox + append-only ledger ───
def is_launched(campaign_id: str) -> bool:
    return bool(_load("launched").get(campaign_id))


def mark_launched(campaign_id: str, record: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        store = _load("launched")
        store[campaign_id] = {"launched_at": _now(), **record}
        _save("launched", store)
        return store[campaign_id]


def record_outbox(entry: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        outbox = _load("outbox")
        rec = {"recorded_at": _now(), **entry}
        outbox[_new_id("out")] = rec
        _save("outbox", outbox)
    return rec


def append_ledger(entry: dict[str, Any]) -> dict[str, Any]:
    """Append-only campaign ledger (dev mirror of the WORM sink for local demo)."""
    with _LOCK:
        ledger = _load("campaign_ledger")
        rec = {"recorded_at": _now(), **entry}
        ledger[_new_id("led")] = rec
        _save("campaign_ledger", ledger)
    return rec


new_id = _new_id
