---
status: approved
issue: 130
intent: intent/2026-10-10-130-local-override-env-base.md
---

# Spec: destination: local override ignores OLLAMA_API_BASE, so confidential text can go to ollama.com

## Design

The fix adds one helper for the address a request will actually reach. It
goes in `app/llm.py`, next to `destination()` (`:31-49`):

```python
def effective_base(model: str, opts: dict) -> str:
    """Where LiteLLM sends the request: api_base, else OLLAMA_API_BASE (which LiteLLM reads) for Ollama models (#130)."""
    if opts.get("api_base"):
        return opts["api_base"]
    return (os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434") if model.startswith("ollama") else ""
```

Both checks in `destination()` use it:

- **The override check** (`:38`) becomes
  `urlparse(effective_base(model, opts)).hostname == "ollama.com"`. An
  explicit local or our-cloud setting is then refused when the env var points
  at ollama.com, the same as when `api_base` does.
- **The inferred path** (`:45`) becomes
  `base = effective_base(model, opts)`. This is the same expression it has
  today, so there is no behaviour change here.

A model that isn't Ollama gets `""` from the helper, the same as today. So
`openai/gpt` with `destination: local` still returns "local", and the pinned
test `tests/test_llm.py:80` is unchanged.

## Alternatives rejected

- **Read only `os.environ` in the override check.** It would duplicate the
  inferred path's expression, which can then drift. One helper keeps the two
  checks agreeing, which is what the constraint asks for.
- **Refuse every explicit override for Ollama models.** That would break the
  Docker case the override exists for: `host.docker.internal` is local but
  can't be inferred.
- **Match subdomains of ollama.com as well.** That is not part of this issue.
  Today's check compares against the exact host, which matches the documented
  value in the README.

## Risks

- **A deploy that sets `OLLAMA_API_BASE=https://ollama.com` with
  `destination: local` now fails with "contradicts" on the first model call.**
  That is the intended fail-closed result: the operator fixes the options or
  approves the model.
- **Related but out of scope:** the `OLLAMA_CLOUD_KEY` line (`:85`) also reads
  only `opts["api_base"]`. With the env var set, the key isn't sent and the
  call fails authentication. That is a refused call, not a leak. The plan may
  reuse `effective_base` there in one line if review agrees.
- **No host impact.** Config is read at call time only.

## Verification

New tests in `tests/test_llm.py`, using `monkeypatch.setenv("OLLAMA_API_BASE", "https://ollama.com")`:

- `destination(LOCAL, {"destination": "local"})` raises "contradicts". This
  fails on main.
- `destination(LOCAL, {"destination": "our-cloud"})` raises as well. This
  fails on main.
- `destination(LOCAL, {})` returns "third-party". This is unchanged and pins
  the shared helper.
- `destination(LOCAL, {"destination": "local", "api_base": "http://host.docker.internal:11434"})`
  returns "local", because an explicit `api_base` wins over the env var.

The full suite passes.
