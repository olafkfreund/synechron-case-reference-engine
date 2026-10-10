---
status: approved
issue: 130
author: olafkfreund
---

# Intent: destination: local override ignores OLLAMA_API_BASE, so confidential text can go to ollama.com

## Problem

`llm.allowed` decides whether a model may read a data class. It relies on
`llm.destination` (`app/llm.py:31-49`), which classifies a model as local,
our-cloud or third-party.

An operator can set `destination: local` or `our-cloud` explicitly in
`*_MODEL_OPTIONS`, which is needed when a Docker host name hides the address.
That override is refused when it contradicts a known third party (`:38`), but
the check only looks at the model name and `opts["api_base"]`.

The env var `OLLAMA_API_BASE` is used by the inferred path (`:45`) and by
LiteLLM when it sends the request, but this check ignores it. So with
`OLLAMA_API_BASE=https://ollama.com` and `destination: local`, the model
counts as local. Confidential presale text is then sent to Ollama Cloud, a
third party, with no approval in the approvals table. The README promises that
"local" is refused for ollama.com.

This breaks the core rule: confidential data only goes to local or our-cloud
models.

## Proposed outcome

An explicit local or our-cloud override is refused whenever the address the
request will actually go to is ollama.com. That address is `api_base` if set,
otherwise `OLLAMA_API_BASE` for Ollama models.

## Affected users and systems

- `app/llm.py` (`destination`), and through it every model call: extraction,
  the research rewrite and summaries.
- Tests: `tests/test_llm.py`.

## Constraints

- Fail closed: a contradiction raises, as today, and is never downgraded
  silently.
- One expression works out the effective address, shared by the override
  check and the inferred path.
- The existing `openai/...` with `destination: local` case is pinned on
  purpose (`tests/test_llm.py:80`) and does not change.

## Open questions

None.

## Approved answers

None needed.
