# GTM & RevOps Automation Workflows

A collection of automation workflows I've built for lead generation, outbound
campaigns, partner management, and internal process automation — built in
**n8n** and **Make (Integromat)**.

> **Note:** These are exported workflow JSON files, sanitized for public
> sharing (API keys, credential IDs, internal spreadsheet IDs, and
> third-party contact info have been redacted/replaced with placeholders).
> They document real architecture and logic but are not plug-and-play —
> you'd need to re-attach your own credentials and IDs to run them.

## Workflows

| Workflow | Platform | What it does |
|---|---|---|
| [Apollo Leads Enricher](workflows/Apollo_leads_enricher_N8N.json) | n8n | Self-serve, form-triggered lead enrichment pipeline: takes an Apollo search URL, runs it through an Apify scraper, loops results into Clay for enrichment, and notifies the requester on Slack when it's done. |
| [Website Inbounds — Free Trial ](workflows/Website_Inbounds_Free_Trial___Whitepaper_N8N.json) | n8n | Routes and qualifies inbound leads from website free-trial and whitepaper form submissions, uses OpenAI for lead scoring/summarization, and syncs to HubSpot with Slack alerts for the team. |
| [Master Email Campaign Workflow](workflows/Master_Email_Campaign_Workflow_N8N.json) | n8n | The core outbound engine: webhook-driven, handles reply detection, branching logic per campaign segment, Google Sheets logging, and Slack notifications across a large multi-branch flow (68 nodes). |
| [Email Campaign](workflows/East_Coast_Email_Campaign_N8N.json) | n8n | A regional variant of the outbound campaign engine, tailored for specific region-targeted sequences. |
| [Physical Events Outreach](workflows/Physical_Events_Outreach_N8N.json) | n8n | Outreach workflow built around physical/in-person event lead follow-up, with webhook triggers and Slack-routed notifications. |
| [Partners Dashboard](workflows/Partners_Dashboard_blueprint.json) | Make | Pulls partner records from Google Sheets, cross-references and enriches them against HubSpot CRM (deals, company associations), and feeds a partner-facing dashboard. |
| [NDA Process Automation](workflows/NDA_Process_Automation_blueprint.json) | Make | Triggered by a Google Form submission (NDA request), routes the request through conditional logic to the right next step automatically. |

## Stack

- **n8n** — self-hosted workflow automation (form triggers, webhooks, HTTP requests, conditional branching, loops)
- **Make (Integromat)** — CRM-integration-heavy workflows, especially HubSpot
- **Integrations used across these workflows:** Google Sheets, Slack, HubSpot, Apify, Clay, OpenAI, Smartlead, Apollo.

## Viewing these workflows

The JSON files can be:
- Viewed directly on GitHub (rendered as JSON)
- Imported into a free n8n instance (n8n.io) or Make account to see the visual canvas and node graph
- Read via the per-workflow notes below for a plain-language walkthrough without needing either tool

---
Built by Amjad — AI Automation & GTM Systems Engineer.
