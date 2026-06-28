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
| `outreach_writer_agent` | Sequence writer | Write per-lead, multi-step, personalised sequences | LLM + **looping enrichment tool** (tool drives the loop, one step per lead) |
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

### Tier 2 — connectors (set the URL and the step goes live)

These five are real MCP `connector_config` URLs — the SDK activates each
when its URL is set; the agent stays on its dev stand-in until then:

| Setting | Enables | Used by |
|---|---|---|
| `APOLLO_MCP_URL` | Real prospect data + validation | prospector |
| `SEQUENCER_MCP_URL` | Ship sequences via EmailBison / Instantly | campaign_launcher |
| `MAILBOX_MCP_URL` | Pull real replies | inbox_monitor |
| `CALENDAR_MCP_URL` | Book real meetings (Google / Cal.com) | meeting_scheduler |
| `CRM_MCP_URL` | Write campaigns + outcomes (Salesforce / HubSpot) | records_keeper |

> ⚠️ **`SERP_MCP_URL` is different.** The GTM research tool in
> `agency/tools/tools.py` ships as a **dev stub that returns no results and
> does not read the var** — setting `SERP_MCP_URL` alone won't enable
> research. Wire the tool body to your SERP/Surfer MCP first. It's in
> `.env` to mark the intended integration point.

### Tier 3 — direct email channel (alternative to the sequencer/mailbox MCPs)

To send + ingest over your own SMTP/IMAP instead of a sequencer platform,
**uncomment the `channels:` blocks** in `configs/config.yaml` and set:

| Setting | What it's for |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `FROM_ADDRESS` | Outbound send |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` | Reply ingest |

> The channel blocks ship **commented out** — an empty SMTP host is
> rejected at boot. Leave them commented to run on Tier 1/2.

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
step real.

## The control gates

- **GTM sign-off** — a *new or materially-changed* GTM goes to a human
  before it drives offerings (routine appends stay autonomous).
- **Campaign approval** — default-on gate before anything sends
  (spend + brand). Toggle off for full-auto.
- **Reply review** — every reply that needs judgment is worked by a
  human, who sends the response or triggers the booking.
- **Audit** — every decision lands in the hash-chained WORM log.

---

*Built with `create-leafmesh`. The config is the department; the PRD is the brief.*
