---
status: approved
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

4. Researches a capability online ("how is SSO federation typically done in
   Salesforce?") and gets a cited comparison of how vendors and the industry do
   it. This also covers capabilities we have not delivered ourselves, so a bid
   can say a feature is standard, out of the box, or industry practice, backed
   by public sources and clearly kept apart from our own case evidence.

In the background, the portal crawls the configured sources (SharePoint sites,
Confluence spaces, file shares) for case documents and information, rather than
relying only on uploads. It finds the documents that describe client work,
extracts one structured, reviewable reference record per case, and keeps those
records current as the sources change.

## Affected users and systems

- Users: bid/presales teams, account leads, marketing (case-study owners), and
  delivery leads who approve records for their own cases.
- Source systems (read only): SharePoint/OneDrive, file shares, Confluence.
- LLM providers: AWS Bedrock by default; Anthropic API, Azure OpenAI and local
  models remain selectable per deployment.
- Corporate Word and PowerPoint templates (brand owner: marketing).
- Corporate identity provider, for SSO.
- A public web search API (outbound only), for online research.
- Hosting: AWS (decided at intent review). Default LLM route is AWS Bedrock.

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
- Scale: hundreds to low thousands of case documents, found among a larger
  crawled corpus.
- Online research never presents public or industry practice as work we have
  delivered. Every public claim carries its source link, and client details are
  never sent to search engines.

## Open questions

- Issue tracker/repo: create a GitHub (or other) issue and repo, replacing `0`
  in this slug?
- Who approves extracted records, and who decides that a client is publicly
  referenceable (legal/account owner)?
- Where are the current corporate Word/PowerPoint templates, and who owns them?
- Is there an existing list of clients and their reference permissions to seed
  the anonymisation rules?
- Success measure for v1: e.g. time to produce a reference set for a bid, or
  share of bids using portal outputs?
