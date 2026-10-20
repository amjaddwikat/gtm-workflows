# NDA Request → Signature Automation

**Platform:** n8n · **Triggers:** n8n form, PandaDoc approval webhook, IMAP inbox · **Systems:** PandaDoc, Slack, email
**File:** [`nda-automation.n8n.json`](../../workflows/nda-automation.n8n.json) (13 nodes)

## The revenue problem

An NDA is often the first gate in a deal or partnership. When reps request one by email, legal ops retypes the details into a template and chases approvals, and the deal stalls for days at the very start.

## What it does

1. A rep fills in a form: counterparty, legal address, authorised signatory (name, title, email) and **NDA type** (customer or partner).
2. A PandaDoc document is generated from the **matching template**, with every field filled in through template tokens.
3. The document goes to **internal approval**.
4. When PandaDoc reports approval via webhook, the workflow resumes and **sends the NDA to the counterparty** for signature.
5. A separate listener watches the inbox for incoming PandaDoc documents, pulls out the "open document" link and posts it to the `#nda` Slack channel.

## Architecture

```mermaid
flowchart LR
    F["Form: NDA request"] --> CD["PandaDoc: create from customer or partner template"]
    CD --> W["Wait for document processing"] --> AP["PandaDoc: send for internal approval"]
    WH["PandaDoc approval webhook"] --> OK{"Approved?"}
    OK -->|yes| SEND["PandaDoc: send to counterparty"]
    OK -->|no| RJ{"Rejected?"}

    IM["IMAP: inbox"] --> PD{"Email from PandaDoc?"}
    PD -->|yes| PX["Extract document link"] --> SL["Slack: #nda"]
```

## Design decisions

- **Template selection by NDA type** keeps the legal language under legal's control. The workflow only fills in the fields.
- **There's an approval gate before anything reaches a counterparty.** Automation speeds up the paperwork without skipping legal review.
- **Incoming NDAs go to one Slack channel**, so they don't get lost in individual inboxes.

## How impact should be measured

- **Cycle time:** request submitted → NDA sent to counterparty → signed (PandaDoc timestamps).
- **Touch time removed** from legal ops per NDA.
- **Error rate:** NDAs re-issued because of wrong details.

## Next iteration

1. **Authenticate the approval webhook** (signature or shared secret). It controls whether a legal document is released.
2. **Handle the rejected path:** notify the requester with the reviewer's comments.
3. **Poll the document status** until it reaches `draft` instead of waiting a fixed 10 seconds.
4. Log every request and its status to the CRM against the account.
