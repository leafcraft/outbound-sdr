# Outbound SDR — Product Requirements / Build Doc

> An autonomous, GTM-led **outbound** sales-development operation. Not
> inbound lead handling — this team *originates* demand: it researches
> the onboarded company, forms a go-to-market strategy, turns strategy
> ideas into offerings, sources and enriches target leads, writes
> hyper-personalised multi-step sequences, ships them through an email
> sequencer, and works the replies to booked meetings — with humans on
> the accountability gates and every system-of-record kept in the
> customer's own tools.
>
> Built on LeafMesh as an **operation of self-reliant agents**, not a
> pipeline of nodes. Every agent prepares its own inputs
> (`@pre_compose`), does its own work end-to-end (runtime tool-calling +
> `@chain`), shapes its own handoffs (`@compose`), and makes sense of
> what its systems return (`@sdk.intelligence`). The Manager supervises;
> it does not route every step.

---

## 1. The operation at a glance

```
 onboard a company (mesh request: name, website, linkedin, …)
        │
   ┌────▼─────────────────────────────────────────────────────────┐
   │ gtm_strategist_agent  (team head, LLM)                        │
   │  • deep research (SERP/Surfer tools) → writes a GTM doc       │
   │  • weekly cron: find events/ideas → append to right GTM,      │
   │    or spin a NEW GTM when the strategy itself shifted         │
   │  • when an idea is flagged offering-type → call offering      │
   └────┬──────────────────────────────────────────────────────────┘
        │ idea.is_offering_candidate == true
   ┌────▼─────────────────────────────────────────────────────────┐
   │ offer_architect_agent  (LLM)                                 │
   │  • turn an idea into a structured OFFERING                    │
   │    (pains, solution, outcomes, objections, …)                │
   │  • A/B variants × persona × industry                         │
   │  • weekly cron: build offerings for ideas that lack one      │
   └────┬──────────────────────────────────────────────────────────┘
        │ (human says "run a campaign"  OR  offering ready + flagged)
   ┌────▼─────────────────────────────────────────────────────────┐
   │ campaign_strategist_agent  (LLM)                             │
   │  • inherits GTM + offering, assembles a campaign plan        │
   │  • kicks off sourcing, then steps out — the chain flows      │
   │    FORWARD (no return-to-conductor; agents are self-reliant) │
   └──┬───────────────────────────────────────────────────────────┘
      │ approved
 ┌────▼────────┐    leads    ┌──────────────────────┐   sequences  ┌───────────────────────┐
 │prospector   │────────────▶│outreach_writer_agent │─────────────▶│campaign_launcher_agent│
 │_agent       │  found      │(LLM + LOOPING TOOL)  │  written     │(system → sequencer)   │
 │(Apollo MCP) │             │per-lead 3-email +    │              │EmailBison / Instantly │
 │lead list    │             │LinkedIn, hyper-pers. │              │                       │
 └─────────────┘             └──────────────────────┘              └──┬────────────────────┘
   (no leads → halt+log)                                              │ emails sent → replies arrive
                                       ┌──────▼────────────────────┐
                                       │ inbox_monitor → reply_triager│
                                       │ (pull, then LLM-classify)  │
                                       └──────┬────────────────────┘
                                              │ needs a human
                                       ┌──────▼────────────────────┐
                                       │ sdr_human         │
                                       │ review reply → write back  │
                                       │ OR book the meeting        │
                                       └──────┬────────────────────┘
                                              │ book
                                       ┌──────▼────────────────────┐
                                       │ meeting_scheduler_agent       │
                                       │ (system → calendar MCP)    │
                                       └────────────────────────────┘
   every state change → audit_logger_agent (WORM, terminal)
```

**Email-driven.** The outward motion is email (+ LinkedIn) via the
sequencer; the inbound motion is replies pulled from the mailbox /
sequencer. The mail channel is first-class on the agents that send and
the agent that ingests replies.

---

## 2. Actor inventory

| Agent | Class | One-line |
|---|---|---|
| `gtm_strategist_agent` | 🤖 LLM | Team head — research → GTM doc; weekly idea/event sweep |
| `offer_architect_agent` | 🤖 LLM | Ideas → structured offerings with A/B × persona × industry variants |
| `campaign_strategist_agent` | 🤖 LLM | Campaign planner — assembles a campaign, kicks off the forward chain |
| `outreach_writer_agent` | 🤖 LLM + tool | Per-lead hyper-personalised multi-step sequence writer (looping tool) |
| `reply_triager_agent` | 🤖 LLM | Classifies each pulled reply (positive / objection / OOO / unsub / referral) |
| `prospector_agent` | ⚙️ system (MCP) | Pulls + validates a lead list from Apollo |
| `campaign_launcher_agent` | ⚙️ system (MCP) | Pushes built sequences to EmailBison / Instantly |
| `inbox_monitor_agent` | ⚙️ system (MCP, cron) | Pulls new replies from the mailbox / sequencer |
| `meeting_scheduler_agent` | ⚙️ system (MCP) | Books the meeting on the calendar |
| `records_keeper_agent` | ⚙️ system (MCP) | Writes campaign + outcome state to the CRM |
| `growth_lead_human` | 👤 human | Approves a new/changed GTM before offerings are built |
| `sales_lead_human` | 👤 human | Approves a campaign before it sends (spend + brand gate) |
| `sdr_human` | 👤 human | Reviews replies; writes the response or triggers a booking |
| `audit_logger_agent` | 🧾 external (WORM) | Hash-chained audit — terminus of every flow |

> **External-agent option:** if the customer already runs a best-in-class
> research or enrichment service elsewhere (e.g. a Clay table, a hosted
> Lyzr/own agent), `gtm_strategist`'s research step or `lead_enrichment`
> can be swapped to `agent_type: external` + `framework:` pointing at it.
> Documented per-agent below; default ships as LLM+tools so it runs
> without that dependency.

---

## 3. Data artifacts (the operation's nouns)

- **GTM doc** — `{gtm_id, company_id, market_thesis, icp[], ideas[], created_at, version}`; each **idea** = `{idea_id, title, rationale, idea_type, is_offering_candidate, source_event}`.
- **Offering** — `{offering_id, idea_id, name, description, industry, pains[], solution, business_outcomes[], objections[], variants[]}`; each **variant** = `{variant_id, persona, industry, angle, ab_label}`.
- **Lead list** — `{list_id, campaign_id, leads[]}`; **lead** = `{lead_id, name, title, company, email, linkedin, firmographics{}, validated}`.
- **Enriched sequence** (per lead) — `{lead_id, steps[]}`; **step** = `{step_no, channel(email|linkedin), subject, body, personalization_basis}`.
- **Campaign** — `{campaign_id, gtm_id, offering_id, list_id, status, sequencer_ref}`.
- **Reply** — `{reply_id, lead_id, body, classified_as, suggested_action}`.

All persisted via **connectors to the customer's systems** (CRM,
sequencer, Apollo). A small `_shared/store.py` provides a local
dev-mode fallback so the template runs before connectors are wired
(clearly marked "replace with your system of record").

---

## 4. Per-agent PRD

### 4.1 `gtm_strategist_agent` — team head (LLM)
**Role.** Understand the onboarded company and own its go-to-market
strategy over time.
**Triggers.**
- `onboard_company` entry point (mesh request: `company_name`, `website`,
  `linkedin`, optional extras).
- `weekly_gtm_sweep` — `wake_up: "0 7 * * 1"` (Mon 07:00): look for new
  market events/ideas.
- `direct_call_gtm` for replays.
**Responsibilities.**
1. **Research.** Deep web research via tools (SERP/Surfer) → market
   thesis + ICP + a set of ideas.
2. **Author/maintain the GTM doc.** First run: create it. Weekly: decide
   per idea whether to **append to the matching GTM** or **spin a new
   GTM** because the strategy itself shifted.
3. **Promote offering-worthy ideas.** Only ideas explicitly flagged
   `is_offering_candidate` get handed to the offering builder.
**`@pre_compose`** — `load_gtm_context`: pull the company record + ALL
existing GTM docs for this company, so the model reasons over "what
strategies already exist" when deciding append-vs-new (this is the
"pull what GTMs are available" the spec calls for).
**Runtime.** LLM with tools (`web_research`, `find_market_events`) — the
model calls them as needed to build the thesis.
**`@chain`** — `persist_gtm`: execute the append-vs-new decision against
the store (append idea to an existing `gtm_id`, or write a new GTM
doc), stamp version, and emit the canonical result. Deterministic write,
not left to the LLM.
**`@compose`** — shape two payloads: to `offer_architect_agent` (just
the flagged idea(s) + GTM context) and to `audit_logger_agent` (the
decision record).
**Wires (`can_call`).**
- → `growth_lead_human` when `gtm_changed == true` (a *new* GTM or a
  material thesis shift needs sign-off before it drives offerings).
- → `offer_architect_agent` when `has_offering_candidate == true and gtm_changed == false`.
- → `audit_logger_agent` otherwise.
**Yields.** `gtm_id, gtm_changed, has_offering_candidate, offering_candidate_ids[], market_thesis, idea_count, version, company_id`.
**Tools.** `web_research(query)`, `find_market_events(company_id)`.
**Integrations.** SERP/Surfer via the vault connection `serp_mcp` (a
commented `mcp:` row; the research tool can be MCP-backed or a direct API tool).

### 4.2 `offer_architect_agent` — offering & messaging architect (LLM)
**Role.** Turn a strategy idea into a sellable, message-ready offering
with variants.
**Triggers.**
- Called by `gtm_strategist_agent` (a flagged idea).
- `weekly_offering_sweep` — `wake_up: "0 8 * * 1"`: build offerings for
  ideas that still lack one.
- `direct_call_offering`.
**Responsibilities.**
1. Produce the offering object (name, description, industry, pains,
   solution, business outcomes, objections).
2. Produce **variants**: A/B angle × persona × industry — multiple per
   idea, kept "in hand" for campaigns to draw from.
**`@pre_compose`** — `load_ideas_without_offerings`: pull ideas flagged
offering-candidate that have no offering yet (drives the weekly sweep)
+ the parent GTM context.
**`@chain`** — `persist_offering`: write the offering + all variants to
the store; dedupe against existing.
**`@conditional_chain`** — `expand_variants` only when
`variant_count < target` (don't re-expand a complete offering).
**Wires.** → `audit_logger_agent` always (terminal unless a campaign is
explicitly requested — campaigns are human/owner-initiated, not
auto-fired off an offering).
**Yields.** `offering_id, idea_id, name, industry, variant_count, ready_for_campaign, company_id`.

### 4.3 `campaign_strategist_agent` — the conductor (LLM)
**Role.** Assemble and drive a campaign from a GTM + offering. The only
agent that coordinates sourcing → enrichment → upload.
**Triggers (never auto-fires off an offering).**
- `start_campaign` entry point — a human via the **inbox** ("here's the
  data, run the campaign").
- Called by `gtm_strategist`/`offering_builder` only when an idea/offering
  is explicitly flagged `create_campaign == true`.
- `direct_call_campaign`.
**Responsibilities.**
1. Inherit GTM + offering; define campaign parameters (ICP filter, size,
   sequence shape, channels).
2. Direct `prospector_agent` with sourcing requirements.
3. Hand the validated list + offering context to `outreach_writer_agent`.
4. On enrichment-complete, hand the built sequences to
   `campaign_launcher_agent`.
5. Coordinate intent end-to-end; report status.
**`@pre_compose`** — `load_campaign_inputs`: pull the named GTM +
offering + variants so the LLM plans over real strategy, not a summary.
**Runtime.** LLM tool-calling to assemble the plan (`assemble_campaign`).
**`@chain`** — `persist_campaign`: write the campaign object, mint
`campaign_id`.
**`@compose`** — distinct payloads per peer: to `prospector_agent`
(ICP filter + size + Apollo query), to `outreach_writer_agent` (offering
+ variants + sequence shape), to `campaign_launcher_agent` (sequencer +
campaign ref), to `sales_lead_human` (the human-readable plan).
**Wires.**
- → `sales_lead_human` when `requires_approval == true` (default
  true — spend + brand gate before anything sends).
- → `prospector_agent` when `approved == true` (post-HITL resume).
- The Sales Lead's `approve` goes straight to `prospector_agent`, which
  writes the approval onto the campaign record (`store.record_approval`,
  only from the `sales_lead_human` reply) and sources nothing for an
  unapproved campaign; anything else the Sales Lead says → audit.
**Yields.** `campaign_id, gtm_id, offering_id, target_size, sequence_shape, requires_approval, approved, company_id`.
**Tools.** `assemble_campaign(...)`.

### 4.4 `prospector_agent` — list builder (SYSTEM, Apollo MCP)
**Role.** Pull + validate the target lead list from Apollo (the system
of record for prospect data).
**Class.** `agent_type: programmatic`, `integration: mcp`,
`connector_config` → vault connection `apollo_mcp`, tool `search_people`.
**`@sdk.intelligence`** — `shape_lead_list`: turn Apollo's raw response
into the canonical lead-list yields; drop invalids; mark `validated`.
**Triggers.** Called by `campaign_strategist_agent`; `direct_call_prospector`.
**Wires.** → `outreach_writer_agent` when `lead_count > 0`; →
`campaign_strategist_agent` when `lead_count == 0` (tell the conductor to
widen the filter — a real collaboration loop, bounded).
**Yields.** `list_id, campaign_id, lead_count, leads[], validated`.

### 4.5 `outreach_writer_agent` — the sequence writer (LLM + LOOPING TOOL)
**Role.** For every lead, write a hyper-personalised multi-step sequence
(email 1/2/3 + LinkedIn), grounded in that lead's enrichment.
**The critical design (per spec): the TOOL drives, the LLM writes.**
- A stateful `@global_tool` `next_enrichment_task(campaign_id)` holds the
  cursor over the lead list × sequence steps. Each call returns the
  *next* `{lead, step_no, channel, prior_steps}` and a `done` flag.
- The LLM is invoked per step: "here is this lead's enrichment and the
  prior steps — write step N." It writes one body; `write_sequence_step`
  persists it; the loop continues.
- **The job is not finished until every row × every step is written** —
  the tool, not the LLM, owns that invariant and keeps returning
  `done == false` until the list is exhausted. This is the "expensive
  but the tool keeps time" requirement.
- Consistency/hyper-personalisation: each step's prompt carries the
  lead's context + prior steps so the thread stays coherent, not "way
  too different."
**`@pre_compose`** — `load_list_and_offering`: the validated list + the
offering/variants + sequence shape.
**`@chain`** — `finalize_sequences`: assert every lead has a complete
sequence; bound the loop (no infinite tool calls); summarise counts.
**Wires.** → `campaign_launcher_agent` when `all_sequences_complete == true`
(ship the sequences — forward, self-reliant, NOT back to the conductor); →
`audit_logger_agent` on partial/abort.
**Yields.** `campaign_id, leads_enriched, sequences_written, all_sequences_complete`.
**Tools.** `next_enrichment_task`, `write_sequence_step`, `enrichment_status`.

### 4.6 `campaign_launcher_agent` — shipping (SYSTEM, sequencer MCP)
**Role.** Push the built sequences into the email sequencer that sends
and tracks them.
**Class.** `programmatic` + `integration: mcp` →
vault connection `sequencer_mcp` (EmailBison / Instantly), tool `upload_campaign`.
**`@sdk.intelligence`** — `shape_upload_result`: map the sequencer's
response (campaign ref, scheduled count) into yields.
**Triggers.** Called by `campaign_strategist_agent` (post enrichment).
**Wires.** → `records_keeper_agent` always (record the live campaign); →
`audit_logger_agent` always.
**Yields.** `campaign_id, sequencer_ref, scheduled_count, upload_ok`.
**Channels.** email (the sequencer is the send path).

### 4.7 `inbox_monitor_agent` — inbound ingest (SYSTEM, mailbox MCP, cron)
**Role.** Pull new replies from the mailbox / sequencer.
**Class.** `programmatic` + `integration: mcp` → vault connection `mailbox_mcp`,
tool `fetch_replies`. `wake_up: "*/15 * * * *"` (every 15 min — commented
until the connector is wired).
**`@sdk.intelligence`** — `shape_replies`: normalise each reply.
**Wires.** → `reply_triager_agent` when `reply_count > 0`.
**Yields.** `replies[], reply_count`.
**Channels.** email (IMAP/sequencer inbound).

### 4.8 `reply_triager_agent` — reply triage (LLM)
**Role.** Classify each reply and propose an action.
**Responsibilities.** Per reply → `classified_as ∈ {positive, objection,
question, out_of_office, unsubscribe, referral, not_interested}` +
`suggested_action` + draft response.
**`@pre_compose`** — `load_thread_context`: the original sequence + lead
enrichment for the reply's lead, so classification/drafting is in-context.
**`@chain`** — `route_replies`: bucket by class.
**Wires.**
- → `sdr_human` when `needs_human == true` (positive / objection
  / question / referral — anything a person should own).
- → `records_keeper_agent` when `auto_closeable == true` (unsub / OOO / hard
  no — logged, suppressed, no human time).
- → `audit_logger_agent` always.
**Yields.** `reply_id, lead_id, classified_as, suggested_action, draft_response, needs_human, auto_closeable`.

### 4.9 `sdr_human` — the human desk (HUMAN)
**Role.** Review replies the agents flagged; either send the response or
trigger a booking. The one place a person works the actual conversation.
**`human_interface: default`** (Studio inbox; Slack/email swappable).
**Wires (route on `human_message`).**
- → `meeting_scheduler_agent` when reply = `book`.
- → `records_keeper_agent` when reply = `send_reply` (the SDR sent the
  reply from their own mailbox; the outcome is logged to the CRM — the pod
  sends nothing on this branch).
- → `audit_logger_agent` otherwise (`hold` or anything else).
**Inputs.** `reply_id, lead_id, draft_response, classified_as`.

### 4.10 `meeting_scheduler_agent` — calendar (SYSTEM, calendar MCP)
**Role.** Book the meeting and confirm back.
**Class.** `programmatic` + `integration: mcp` → vault connection `calendar_mcp`,
tools `find_slots`, `book_meeting`.
**`@sdk.intelligence`** — `shape_booking`: map calendar response → yields.
**Wires.** → `records_keeper_agent` always (log the meeting); →
`audit_logger_agent` always.
**Yields.** `lead_id, meeting_id, slot, booked_ok`.

### 4.11 `records_keeper_agent` — system of record (SYSTEM, CRM MCP)
**Role.** Write campaign + outcome state to the CRM (Salesforce/HubSpot).
**Class.** `programmatic` + `integration: mcp` → vault connection `crm_mcp`.
**`@sdk.intelligence`** — `shape_crm_ack`.
**Wires.** → `audit_logger_agent` always.

### 4.12 Human gates summary
- `growth_lead_human` — signs off a **new/changed GTM** before it drives offerings.
- `sales_lead_human` — approves a **campaign before send** (spend + brand).
- `sdr_human` — works **replies** → response or booking.
Nothing proceeds on silence: if nobody answers in time (24 h /
24 h / 4 h), the Inbox card closes and the ticket records that no
reply came; nobody is chased automatically. Each seat routes on
`human_message` with a catch-all to audit, no placeholder `operator_ids`.

---

## 5. Entry points (the doors)
| Entry point | Target | Trigger |
|---|---|---|
| `onboard_company` | `gtm_strategist_agent` | A company is onboarded (mesh request) |
| `weekly_gtm_sweep` | `gtm_strategist_agent` | Mon 07:00 cron — events/ideas |
| `weekly_offering_sweep` | `offer_architect_agent` | Mon 08:00 cron — build missing offerings |
| `start_campaign` | `campaign_strategist_agent` | Human inbox: run a campaign |
| `pull_replies` | `inbox_monitor_agent` | Every 15 min cron (or sequencer webhook) |
| `direct_call_*` | each agent | Debug / replay / programmatic |

---

## 6. Decorator usage map (where & why)
| Agent | @pre_compose | @chain / @conditional_chain | @compose | @sdk.intelligence |
|---|---|---|---|---|
| gtm_strategist | load company + existing GTMs | persist (append-vs-new) | offering vs audit payloads | — |
| offering_builder | load ideas-without-offerings | persist; conditional variant expand | — | — |
| campaign_strategist | load GTM+offering | persist campaign | per-peer payloads (4) | — |
| lead_sourcing | — | — | — | shape Apollo list |
| lead_enrichment | load list+offering | finalize/bound the loop | — | — |
| sequence_uploader | — | — | — | shape sequencer ack |
| reply_puller | — | — | — | shape replies |
| reply_classifier | load thread context | route by class | — | — |
| meeting_booker | — | — | — | shape booking |
| crm_sync | — | — | — | shape CRM ack |

---

## 7. Integrations / connectors (vault connection, commented until wired)
Addresses and tokens are entered only in the vault (Infrastructure →
Integrations → MCP, or an "Add custom" record with service type Tools whose base
URL is the address); each block picks its record by name.

| System | Used by | Vault connection |
|---|---|---|
| SERP / Surfer (web research) | gtm_strategist tools | `serp_mcp` |
| Apollo (prospect data) | lead_sourcing | `apollo_mcp` |
| EmailBison / Instantly (sequencer) | sequence_uploader, sdr send-back | `sequencer_mcp` |
| Mailbox (reply ingest) | reply_puller | `mailbox_mcp` |
| Calendar (Google / Cal.com) | meeting_booker | `calendar_mcp` |
| CRM (Salesforce / HubSpot) | crm_sync | `crm_mcp` |

Connector crons ship **commented out** ("enable after wiring your X
connector"). A `_shared/store.py` gives a local JSON dev fallback so the
LLM chain (GTM → offering → campaign → enrichment) runs end-to-end
before any connector exists.

---

## 8. Reflection — adjustments I made on top of the brief
1. **Split the spec's "reply/calendar coordinator" into three** —
   `reply_puller` (system ingest), `reply_classifier` (LLM judgment),
   `meeting_booker` (system calendar). One agent doing all three would
   mix a connector, a model, and a second connector — three roles. The
   *human reviewer* sits between classify and book, as you described.
2. **Added `sales_lead_human` as a default send gate.** You said
   humans are in the loop "ensuring it's all running" — for an outbound
   motion the load-bearing gate is *before money/brand goes out the
   door*, so a campaign approval step is always on.
3. **Added `records_keeper_agent`.** The operation produces outcomes
   (campaign live, meeting booked) that belong in the CRM; without it,
   results live only in the audit log.
4. **`growth_lead_human` only fires on a NEW/changed GTM**, not every
   weekly append — so routine idea-appends stay autonomous and humans
   are spent only on strategy shifts.
5. **Offerings do NOT auto-launch campaigns.** Per your "campaign is
   human- or owner-initiated" rule, the offering builder terminates at
   audit; a campaign starts only from the inbox or an explicit
   `create_campaign` flag.

Open to changing any of these — they're written so they're easy to undo.

---

## 9. Build status
This PRD is the spec. Implementation order: the configuration (full mesh) →
`_shared` (store, audit, stash) → tools (esp. the looping enrichment
tool) → per-agent pre_compose/chain/compose/intelligence modules →
`leafmesh config check` → tests. Connectors ship inert (env-var) with a local
dev store so the judgment chain runs out of the box.
