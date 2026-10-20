# Outbound Email Signal Router

**Platform:** n8n · **Trigger:** Smartlead webhook (every campaign event) · **Systems:** Smartlead, Google Sheets, Clay, HubSpot (via Clay/Make), Slack
**Files:** [`outbound-email-signal-router.n8n.json`](../../workflows/outbound-email-signal-router.n8n.json) (49 nodes) · [`outbound-email-signal-router-regional.n8n.json`](../../workflows/outbound-email-signal-router-regional.n8n.json) (32 nodes, leaner regional variant)

## The revenue problem

A cold email program creates a lot of events: sends, opens, clicks, replies, bounces. Only a few of them need a human. The expensive failures are:

- **A positive reply that sits unseen** in a shared inbox while the prospect cools off.
- **SDRs chasing noise**: out-of-office auto-replies counted as replies, or "opens" fired by corporate email security scanners.
- **Replies from a different address** than the one emailed (an assistant, a personal alias). These drop out of tracking.
- **No single record** of where each lead stands, so follow-up depends on memory.

This workflow is the layer between the sending tool and the sales team. It classifies every event, keeps a per-lead status record, and sends only the signals that need action to the right place.

## What it does

| Event | Handling |
|---|---|
| **Reply** | Out-of-office replies (Smartlead category 6) go to a dedicated Clay table and a Slack alert, so they can be re-sequenced instead of counted as engagement. A **first** reply is enriched with full lead and campaign context from the Smartlead API, logged, and routed to the SDR hand-off table. A reply from an address Smartlead doesn't recognise is flagged as an **alias reply** for a human to match. Repeat replies from known leads go to the CRM-sync path. |
| **Sent** | Looks up the lead in Smartlead and logs the first email as a new tracking row with full lead context. Later sequence steps update the row as a follow-up. |
| **Open** | Pulls the lead's message history from Smartlead to identify which sequence step was opened. **Opens that arrive within 20 seconds of the send are dropped as security-scanner opens.** It then records the first open, increments repeat opens, or flags **opens after the lead already clicked or replied** (a re-engagement signal). |
| **Link click** | Matched to the lead by its unique tracking ID and counted only while the lead is in a sent or opened state. The first click goes to the SDR hand-off; later clicks sync to HubSpot. |
| **Bounce** | Slack alert with campaign and address, so deliverability problems surface per campaign. |
| **Unknown event / router error** | Separate Slack alerts for fallback and error outputs, with the execution ID for debugging. |

## Architecture

```mermaid
flowchart LR
    SL["Smartlead webhook"] --> R{"Route by event_type"}

    R -->|EMAIL_REPLY| OOO{"Out-of-office?"}
    OOO -->|yes| OOOC["Clay: OOO table"] --> OOOS["Slack: OOO alert"]
    OOO -->|no| FR{"Lead already in tracking sheet?"}
    FR -->|no: first reply| API["Smartlead API: lead + campaign context"]
    API --> AL{"Known lead address?"}
    AL -->|yes| LOG["Sheet: log reply"] --> SDR["Clay: SDR hand-off table"]
    AL -->|no: alias| ALC["Clay: alias table"] --> ALS["Slack: alias reply alert"]
    FR -->|yes: repeat reply| CRM["CRM sync table"]

    R -->|EMAIL_SENT| LK["Smartlead API: lead"] --> FE{"First email in sequence?"}
    FE -->|yes| NEW["Sheet: create tracking row"]
    FE -->|no| FUP["Sheet: mark follow-up sent"]

    R -->|EMAIL_LINK_CLICK| TR{"Tracked link?"}
    TR -->|yes| CS{"Lead in sent or opened state?"}
    CS -->|yes| CU["Sheet: log click"] --> FC{"First click?"}
    FC -->|yes| CSDR["Clay: SDR hand-off"]
    FC -->|no| CHS["HubSpot sync"]

    R -->|EMAIL_OPEN| MH["Smartlead API: message history"]
    MH --> SEQ["Match sequence step"] --> BOT{"More than 20s after send?"}
    BOT -->|no: scanner| DROP["Ignore"]
    BOT -->|yes: human| ST{"Lead status"}
    ST -->|first open| O1["Sheet: create row"]
    ST -->|repeat open| O2["Sheet: increment opens"]
    ST -->|after click or reply| O3["Sheet: flag re-engagement"]

    R -->|EMAIL_BOUNCED| B["Slack: bounce alert"]
    R -->|fallback| FB["Slack: fallback alert"]
    R -->|error| ER["Slack: error alert"]
```

Every Google Sheets step retries up to 3 times, 5 seconds apart. If it still fails, it posts to the same **Step Failed Alert** in Slack with the step name, lead, campaign and execution ID.

## Design decisions

- **One webhook, one router.** Every Smartlead campaign points at a single endpoint, and routing happens on `event_type`. A new campaign needs no new automation.
- **The sheet holds lead state.** A per-lead row (status, opens, clicks, replies, "sent to SDRs") makes the workflow idempotent at the business level: an SDR is only notified on the *first* reply, however many replies follow.
- **The Smartlead API is used for context, not the webhook payload.** Reply and open events carry very little. Pulling lead and message history gives SDRs the campaign, sequence step and custom fields at hand-off, and tells real leads apart from alias replies.
- **Clay is the hand-off layer.** SDR assignment, enrichment and CRM writes live in Clay tables. Ops can change routing rules without editing the workflow.
- **The router has error and fallback outputs that alert in Slack.** Unknown event types are reported, not silently dropped.

## Failure modes designed for

| Failure | Mitigation |
|---|---|
| OOO auto-replies inflate the reply count | Classified by Smartlead reply category and routed separately |
| Security scanners trigger opens and clicks | Opens within 20s of send are dropped (covered by tests); clicks are gated on lead status |
| Lead replies from a different address | Smartlead lookup miss → alias path with a human alert |
| Duplicate SDR notifications on reply threads | First-reply check against the tracking sheet before routing to SDRs |
| Sheets API throttling under event bursts | Bounded retries (3 tries, 5s apart), then a Slack alert. Never an infinite loop. |
| Unexpected event type or router failure | Dedicated Slack alerts with the execution ID |

## How impact should be measured

The sales team owned pipeline targets. These are the metrics this system is built to move, and where to get them:

- **Reply-to-SDR latency:** time from the Smartlead reply event to the SDR hand-off (n8n execution timestamps).
- **Triage automation rate:** % of replies auto-classified as OOO, alias, first or repeat vs. needing manual sorting.
- **Positive reply → meeting conversion** by campaign (Smartlead categories + HubSpot meetings).
- **Bounce rate per campaign** as a deliverability guardrail (bounce alerts + Smartlead stats).
- **Signal quality:** share of opens and clicks discarded as automated.

## Hardening pass

A review of the export found four problems, now fixed and covered by [tests](../../tests/test_workflows.py):

| Found | Fixed |
|---|---|
| The bot-open check was inverted: it kept opens arriving *within* 20s of the send (scanners) and dropped the human ones | Operator corrected. Tests replay a 3-second open (ignored) and a 45-minute open (recorded). |
| The router's **Email Sent** and **Link Clicked** outputs were unwired, so first-email logging and click hand-off never ran | Reconnected. A test asserts that every router output is wired. |
| 20 nodes were unreachable: an older copy of the reply path and a stray duplicate | Removed (68 → 49 nodes, including the new alert). A test asserts there are no unreachable nodes. |
| A failed sheet write looped back on itself indefinitely (wait → retry → wait…) | Replaced with n8n's bounded retry plus one Slack failure alert. A test asserts that no cycle is unbounded. |

## Next iteration

1. **Replace waits with idempotent upserts.** Fixed delays reduce sheet race conditions but don't remove them. A keyed upsert (`campaign_id + lead_id`) in Postgres or HubSpot would.
2. **Verify webhook origin.** Today the random endpoint path is the secret. If Smartlead can sign webhooks or send a custom header, check it on arrival, the way the NDA flow checks PandaDoc's signature.
3. **Merge the two variants** into one workflow with per-campaign configuration.
