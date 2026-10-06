---
status: draft
issue: 0
author: olafkfreund
---

# Intent: Customer reference engine

## Problem

Synechron's bid and presales teams need strong customer references (client or
industry, challenge, solution, tech stack, measurable outcomes) for bids and
presentations. The evidence exists, but it is buried in case studies, closure
reports, bid responses and decks spread across SharePoint/OneDrive, file shares
and Confluence. Finding a relevant case, checking it, anonymising it and
reformatting it into the corporate template is slow manual work. The result
depends on who you ask, so good work goes unreferenced and references are
inconsistent from one bid to the next.

## Proposed outcome

An org-wide, cloud-hosted portal where a user:

1. Describes a bid ("Tier-1 bank, cloud migration, UK") and gets the most
   relevant approved reference cases, each linked back to its source documents.
2. Downloads a chosen case as a branded Word one-pager, a PowerPoint slide, a
   PDF, or Markdown/plain text for pasting into bid portals.
3. Gets client names anonymised by default ("a Tier-1 UK bank"), unless the
   client is approved as publicly referenceable.

In the background, the portal ingests existing documents from those sources,
extracts one structured, reviewable reference record per case, and keeps those
records current.

## Affected users and systems

- Users: bid/presales teams, account leads, marketing (case-study owners), and
  delivery leads who approve records for their own cases.
- Source systems (read only): SharePoint/OneDrive, file shares, Confluence.
- LLM providers: AWS Bedrock, Anthropic API, Azure (OpenAI/Foundry) and local
  models, chosen per deployment.
- Corporate Word and PowerPoint templates (brand owner: marketing).
- Corporate identity provider, for SSO.

## Constraints

- Client data is confidential. Documents go only to LLM endpoints approved for
  that data (no-retention or in-tenant). No SaaS parsers.
- Users only see cases from documents they can already read in the source system.
- No invented facts. Every metric and claim in an output traces to a source
  document, and a human approves a record before it can be used in outputs.
- Outputs use the corporate templates unchanged. The LLM supplies content, never
  layout.
- Must work with more than one LLM provider and not be locked to one.
- Org-wide use requires SSO, role-based access, and an audit trail of what was
  generated and for whom.
- Scale: hundreds to low thousands of source documents.

## Open questions

- Issue tracker/repo: create a GitHub (or other) issue and repo, replacing `0`
  in this slug?
- Hosting cloud (AWS or Azure)? This sets the default LLM route and the SSO provider.
- Who approves extracted records, and who decides that a client is publicly
  referenceable (legal/account owner)?
- Where are the current corporate Word/PowerPoint templates, and who owns them?
- Is there an existing list of clients and their reference permissions to seed
  the anonymisation rules?
- Success measure for v1: e.g. time to produce a reference set for a bid, or
  share of bids using portal outputs?
