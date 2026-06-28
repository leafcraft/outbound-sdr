"""prospector_agent — list builder (SYSTEM, connector-backed).

Production: the Apollo connector (integration: mcp in config.yaml,
tool search_people) runs and this shapes its raw response into the
canonical lead-list yields (@sdk.intelligence role) — dropping invalids
and marking validated. Dev: returns a local-store stand-in — a small
synthetic, validated lead list written to _shared/store.put_lead_list so
the chain (campaign → sourcing → enrichment) runs day-0 before the
APOLLO_MCP_URL connector is wired.

Yields (config.yaml): list_id, campaign_id, lead_count, leads, validated.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

# Full-shape: every declared yield with a safe default.
_DEFAULTS: dict[str, Any] = {
    "list_id": "",
    "campaign_id": "",
    "lead_count": 0,
    "leads": [],
    "validated": False,
}

# Cap synthetic dev leads so dev runs stay cheap/fast.
_DEV_CAP = 10

# Synthetic firmographic seeds for plausible dev leads.
_DEV_SEEDS = [
    ("Avery Chen", "VP Sales", "Northwind Logistics", "Logistics", "201-500"),
    ("Jordan Patel", "Head of Growth", "Brightpath Analytics", "Software", "51-200"),
    ("Sam Rivera", "Director of Ops", "Cobalt Manufacturing", "Manufacturing", "501-1000"),
    ("Taylor Brooks", "CMO", "Lumen Health", "Healthcare", "1001-5000"),
    ("Morgan Lee", "RevOps Lead", "Drift Capital", "Financial Services", "11-50"),
    ("Casey Nguyen", "VP Marketing", "Ironwood Retail", "Retail", "501-1000"),
    ("Riley Foster", "Head of Sales", "Atlas Robotics", "Robotics", "51-200"),
    ("Quinn Adams", "Director of Demand Gen", "Verde Energy", "Energy", "201-500"),
    ("Skyler Ward", "VP Revenue", "Pinnacle Media", "Media", "11-50"),
    ("Drew Ellis", "Growth Lead", "Solstice Foods", "CPG", "201-500"),
]


def _pick(input_data: dict[str, Any], key: str, default: Any) -> Any:
    """Shape-tolerant read: input_data, then upstream_yields (flat or
    keyed by agent), then human_data. Keeps the agent runnable whether the
    conductor passes inputs flat or nested."""
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
    human = input_data.get("human_data") or {}
    if isinstance(human, dict) and human.get(key) not in (None, ""):
        return human[key]
    return default


def _email(name: str, company: str) -> str:
    first = name.split()[0].lower()
    domain = "".join(ch for ch in company.lower() if ch.isalnum())
    return f"{first}@{domain}.com"


def _shape_connector_leads(raw: dict[str, Any], campaign_id: str) -> list[dict[str, Any]]:
    """@sdk.intelligence: turn Apollo's raw search_people response into
    canonical lead dicts; drop invalids (no email)."""
    people = (
        raw.get("people")
        or raw.get("results")
        or raw.get("leads")
        or raw.get("data")
        or []
    )
    if not isinstance(people, list):
        people = []
    leads: list[dict[str, Any]] = []
    for p in people:
        if not isinstance(p, dict):
            continue
        email = p.get("email") or p.get("work_email") or ""
        if not email:
            continue  # invalid — drop
        name = p.get("name") or " ".join(
            x for x in [p.get("first_name", ""), p.get("last_name", "")] if x
        ).strip()
        leads.append({
            "lead_id": store.new_id("lead"),
            "name": name,
            "title": p.get("title") or p.get("headline", ""),
            "company": p.get("company") or p.get("organization_name", ""),
            "email": email,
            "linkedin": p.get("linkedin") or p.get("linkedin_url", ""),
            "firmographics": p.get("firmographics") or {
                "industry": p.get("industry", ""),
                "employee_count": p.get("employee_count", ""),
            },
            "validated": True,
        })
    return leads


def _synth_leads(target_size: int, icp_filter: dict[str, Any]) -> list[dict[str, Any]]:
    """Dev stand-in: synthesize plausible validated leads, capped."""
    try:
        n = int(target_size) if target_size else len(_DEV_SEEDS)
    except (TypeError, ValueError):
        n = len(_DEV_SEEDS)
    n = max(1, min(n, _DEV_CAP))
    industry_hint = ""
    if isinstance(icp_filter, dict):
        industry_hint = str(icp_filter.get("industry", "") or "")
    leads: list[dict[str, Any]] = []
    for i in range(n):
        name, title, company, industry, size = _DEV_SEEDS[i % len(_DEV_SEEDS)]
        leads.append({
            "lead_id": store.new_id("lead"),
            "name": name,
            "title": title,
            "company": company,
            "email": _email(name, company),
            "linkedin": f"https://linkedin.com/in/{name.replace(' ', '').lower()}",
            "firmographics": {
                "industry": industry_hint or industry,
                "employee_count": size,
            },
            "validated": True,
        })
    return leads


async def prospector_agent(llm_response, input_data, context):
    """Pull + validate a target lead list. Connector path shapes Apollo's
    response; dev path synthesizes a small validated list into the store."""
    input_data = input_data or {}
    campaign_id = _pick(input_data, "campaign_id", "")
    icp_filter = _pick(input_data, "icp_filter", {}) or {}
    target_size = _pick(input_data, "target_size", 0)

    # When a connector is configured the SDK passes its raw response as the
    # first arg — a dict means "shape this" (@sdk.intelligence role).
    if isinstance(llm_response, dict) and llm_response:
        leads = _shape_connector_leads(llm_response, campaign_id)
        source = "apollo_connector"
    else:
        leads = _synth_leads(target_size, icp_filter)
        source = "dev_store"

    list_id = store.new_id("list")
    store.put_lead_list(list_id, campaign_id, leads)
    logger.info(
        f"prospector_agent — {len(leads)} validated leads ({source}) "
        f"for campaign={campaign_id!r}, list={list_id}"
    )

    computed = {
        "list_id": list_id,
        "campaign_id": campaign_id,
        "lead_count": len(leads),
        "leads": leads,
        "validated": True,
    }
    return {**_DEFAULTS, **computed}
