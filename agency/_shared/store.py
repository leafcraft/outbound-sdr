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


new_id = _new_id
