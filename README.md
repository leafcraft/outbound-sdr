# outbound-sdr — Outbound SDR

A GTM-led **outbound** sales-development operation: it researches the
onboarded company, forms a go-to-market strategy, turns ideas into
offerings, sources and enriches target leads, writes hyper-personalised
multi-step sequences, ships them through an email sequencer, and works
the replies to booked meetings — humans on the gates, your tools as the
systems of record.

**Read `PRD.md` first** — it specs every agent (role, responsibilities,
pre_compose / chain / conditional_chain / compose / sdk.intelligence,
tools, integrations, wiring).

## Why it works in an organization

Pipeline generation is the growth bottleneck — and most outbound is
**generic spray** that burns sending domains and brand. This operation runs
the full GTM motion end to end: research the company, form a strategy, turn
it into offerings, source + enrich real leads, write **hyper-personalised**
multi-step sequences, ship through your sequencer, and work replies to
**booked meetings** — with humans approving the GTM and **every send**
(spend + brand protection). It uses the tools the team already pays for
(Apollo, EmailBison/Instantly, calendar, CRM). The looping enrichment
tool means **the tool keeps time, not the LLM** — sequences personalise at
scale without runaway token cost. ROI: more qualified meetings per rep,
higher reply rates from real personalisation, brand-safe because a human
gates every campaign.

## The team (14 agents)

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `gtm_strategist_agent` | Team head | Research the onboarded company → a GTM strategy doc; sweep for new ideas/events weekly | LLM; web-research tool (SERP — stub until wired); routes to offer_architect; wake_up sweeps |
| `offer_architect_agent` | Offer designer | Turn GTM ideas into concrete offerings + A/B × persona × industry variants | LLM; routes to campaign_strategist |
| `campaign_strategist_agent` | Campaign conductor | Assemble a campaign (offering + audience + sequence plan) and drive it | LLM; orchestrates prospector → outreach_writer → launcher |
| `outreach_writer_agent` | Sequence writer | Write per-lead, multi-step, personalised sequences | LLM + **looping enrichment tool** (tool drives the loop, one step per lead); 3 parallel copies (`scaling.copies.count: 3`), each claiming a disjoint batch with `claim_leads` |
| `reply_triager_agent` | Reply classifier | Classify each inbound reply (interested / objection / OOO / unsubscribe) and propose the next action | LLM; routes to sdr_human / scheduler |
| `prospector_agent` | Lead sourcing | Pull + validate the target lead list | Programmatic (MCP — Apollo) |
| `campaign_launcher_agent` | Sequencer | Ship the approved sequences | Programmatic (MCP — EmailBison/Instantly) |
| `inbox_monitor_agent` | Reply ingest | Pull new replies on a cron | Programmatic (MCP — mailbox) |
| `meeting_scheduler_agent` | Booker | Book the meeting on the calendar | Programmatic (MCP — calendar) |
| `records_keeper_agent` | CRM sync | Write campaign + outcomes back to CRM | Programmatic (MCP — Salesforce/HubSpot) |
| `growth_lead_human` | GTM approver | Sign off a new or materially-changed GTM before it drives offerings | Human gate |
| `sales_lead_human` | Campaign approver | Approve a campaign before anything sends (spend + brand) | Human gate |
| `sdr_human` | Reply worker | Work each reply that needs judgment → response or booking | Human gate |
| `audit_logger_agent` | WORM audit | Hash-chain every decision + send | External (custom sink); terminus |

2.4.484's `scaling.copies.mode: auto` with `claim_tool: claim_leads` could replace
the writer's manual backlog split — an owner decision, not made in this template.

## Configuration — what to set, in three tiers

Everything is set in `.env` (`cp env .env`). The whole LLM operation
(**research → GTM → offerings → campaign → enrichment → sequences**) runs
on **Tier 1 alone** against a local store (`./data/`); lead sourcing
synthesises a sample list and the sequencer/calendar/CRM return stand-ins.
Tiers 2–3 make those steps hit real systems. **No host prerequisites** —
unlike the dev-squad there's no Claude Agent SDK; everything external is a
connector URL.

### Tier 1 — required to boot (dev mode)

| Setting | What it's for | Notes |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | LeafMesh runtime license | **Required.** |
| `ANTHROPIC_API_KEY` | The GTM / offering / campaign / enrichment / triage LLMs (Claude) | **Required.** |
| `OPENAI_API_KEY` | The Manager model | **Required** unless you change `manager.model`. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | State store | Defaults to `localhost:6379`. `docker run -d -p 6379:6379 redis:7-alpine`. |

### Tier 2 — connectors (a vault connection, then uncomment the block)

Each system's address + token is entered **only in the vault** (Studio →
Infrastructure → Integrations → MCP, or an "Add custom" record with service type
Tools whose base URL is the address) — the SDK refuses a `url:` / `token:` written in
the config, even as `${ENV_VAR}`. Each agent's connector block ships
**commented** in `configs/config.yaml`, already picking its connection by name;
create the connection, then uncomment it — in `configs/config.yaml` before the
first start, in Studio's YAML editor after it (see *Changing the configuration
after the first start*). The agent stays on its dev stand-in until then:

| Vault connection | Enables | Used by |
|---|---|---|
| `apollo_mcp` | Real prospect data + validation | prospector |
| `sequencer_mcp` | Ship sequences via EmailBison / Instantly | campaign_launcher |
| `mailbox_mcp` | Pull real replies | inbox_monitor |
| `calendar_mcp` | Book real meetings (Google / Cal.com) | meeting_scheduler |
| `crm_mcp` | Write campaigns + outcomes (Salesforce / HubSpot) | records_keeper |
| `serp_mcp` | Web research (SERP / Surfer) as MCP tools | gtm_strategist (its commented `mcp:` block) |

> The launcher treats the sequencer as wired (`real_send`) as soon as the vault
> record `sequencer_mcp` exists — nothing in `.env`. Until then it stages the
> launch pack in `./out/` and says so. The day-0 `web_research` tool in `agency/tools/tools.py` is a **dev stub
> that returns no results** — real research comes from the `serp_mcp` tools
> (or wire that tool body to your SERP/Surfer API).

### Tier 3 — direct email channel (alternative to the sequencer/mailbox MCPs)

To send + ingest over your own SMTP/IMAP instead of a sequencer platform,
create an **email connection in the vault** (SMTP/IMAP host, user, password —
never in `.env` or the config) and add a `channels: {email: {connection:
<name>}}` block on the sending agent. This template ships no `channels:`
block; leave it out to run on Tier 1/2.

## Run it

```bash
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
cp env .env                # fill at least Tier 1
python validate_config.py  # pre-flight: wiring, yields, dead-code
python main.py             # API at http://127.0.0.1:18820/docs
```

Onboard a company to start the operation:

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/call \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"onboard_company",
       "input_data":{"company_name":"Acme Robotics",
                     "website":"https://acme.example",
                     "linkedin":"https://linkedin.com/company/acme"}}'
```

Then run a campaign (human-initiated):

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/call \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"start_campaign",
       "input_data":{"gtm_id":"<from above>","offering_id":"<offering>"}}'
```

In dev mode the LLM chain (GTM → offering → campaign → enrichment) runs
against the local store; wire the Tier 2 connectors above to make each
step real. The GTM sign-off, campaign approval and reply review land in
**Studio → Inbox** — answer them there.

## The control gates

- **GTM sign-off** — a *new or materially-changed* GTM goes to a human
  before it drives offerings (routine appends stay autonomous).
- **Campaign approval** — the Sales Lead approves before any lead is
  sourced or any email sent (spend + brand). The approval is written onto
  the campaign record by the first agent behind the gate, and the launcher
  re-reads it before anything is staged or sent; an unapproved campaign
  sources nothing. There is no switch to turn it off.
- **Reply review** — every reply that needs judgment is worked by a
  human: `book` triggers the booking; `send_reply` logs a reply the SDR sent
  from their own mailbox to the CRM (the pod sends nothing on that branch);
  anything else is recorded.
- **Silence** — if nobody answers a gate in time (Growth Lead and Sales
  Lead: 24 h; SDR: 4 h), the request waits no longer: its Inbox
  card closes and the ticket records that no reply came. Nothing is sourced,
  sent or approved on silence, and nobody is chased automatically.
- **The Manager** escalates to the Growth/RevOps Lead only when an agent
  hits an error a retry cannot fix, when 5 different agents fail in one
  session, or when restarts run out. Enrichment stalls (after the retry
  budget) and blocked launches reach the Growth/RevOps Lead by their own
  wires.
- **Audit** — every decision lands in the hash-chained WORM log.

---

*Built with `create-leafmesh`. The config is the department; the PRD is the brief.*

## Changing the configuration after the first start

`configs/config.yaml` is only the first-start seed. On the first start (an empty store) the agency imports it as version 1 and from then on keeps its configuration in the store. After that the file is not read again: an edit to it is **not** applied, even after a restart (the boot log says "that edit was NOT applied").

To change a running agency:

- **Studio** — its settings pages, or the raw YAML editor.
- **API** — `PUT /api/yaml/raw` with an `X-API-Key` header and the body `{"yaml_content": "<the whole YAML>", "save_to_disk": true}`, or `PATCH /api/yaml/config` with `{"updates": {...}, "save_to_disk": true}`.

Each save is a new version and applies live on every pod — no restart. (A few settings, such as brokers, make the agency reload itself in place within seconds.) So wherever this README or a comment says "uncomment X in `configs/config.yaml`", do it in the file before the first start, or in Studio's YAML editor after it.

## Where the data store is set

From leafmesh 2.4.482 the store (Redis or Postgres) is set **only in `.env`** — never in `configs/config.yaml`. A `persistence:` block in the YAML is ignored with a warning on leafmesh 2.4.482–2.4.485; from 2.4.486 the agency refuses to start if that block differs from `.env`, and names each `.env` line to add. The project's `.env` sets the store explicitly (`LEAFMESH_STORE`, `LEAFMESH_AGENCY_NAME`, `REDIS_*`).

- Redis (default): `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`; optional `REDIS_DEFAULT_TTL`, `REDIS_SESSION_TTL`, `REDIS_SSL*`, `REDIS_CLUSTER_NODES`.
- Postgres instead: `LEAFMESH_STORE=postgres` plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (optional `POSTGRES_SSLMODE`).
- Or the whole server in one value: `REDIS_URL=redis://user:pw@host:6379/0` (`rediss://` turns TLS on) or `DATABASE_URL=postgresql://user:pw@host:5432/db`. The single names fill in what the URL leaves out.
- With Docker Compose the app reaches Redis as `redis` (the service name); compose sets that for you.
- Check it in Studio → **Infrastructure → Datastores**: it shows whether the store is reachable (never the address or password).

## Your vault key — back it up

`create-leafmesh` put a `LEAFMESH_VAULT_KEY` in your `.env` when it made this project. It is the master key that encrypts every connection and credential you store in the vault (in Redis / Postgres).

- **Back it up like a master password.** Lose it and every stored connection has to be entered again.
- **Never change or regenerate it** for an existing project — a new key locks the existing vault. To change it, use `leafmesh vault rotate-key`.
- Every copy of the app (every replica) must get the **same** value. Docker Compose passes it in from `.env` as an environment variable.
- Never commit `.env` (it is already in `.gitignore`).
