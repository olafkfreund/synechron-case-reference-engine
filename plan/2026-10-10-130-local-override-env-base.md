---
status: approved
issue: 130
spec: spec/2026-10-10-130-local-override-env-base.md
---

# Plan: destination: local override ignores OLLAMA_API_BASE, so confidential text can go to ollama.com

These decisions are copied from the approved spec:

- **One helper.** `effective_base(model, opts)` returns `api_base` if it is
  set. Otherwise, for Ollama models, it returns `OLLAMA_API_BASE`, defaulting
  to `http://localhost:11434`. For any other model it returns `""`.
- **Both checks in `destination()` use it.** In the override check, an
  explicit local or our-cloud setting is refused when the effective host is
  `ollama.com`. The inferred path gets the same expression it has today.
- **The pinned case is unchanged.** `openai/gpt` with `destination: local`
  stays "local".
- **The `OLLAMA_CLOUD_KEY` line (`:85`) is out of scope.** It is a refused
  call, not a leak, so it is not changed here.

**Size:** 2 file-editing steps in 2 files. That is below the coder threshold.

## Steps

1. `app/llm.py`:
   - **Above `destination()` (`:31`):** add the helper from the spec:
     ```python
     def effective_base(model: str, opts: dict) -> str:
         """Where LiteLLM sends the request: api_base, else OLLAMA_API_BASE (which LiteLLM reads) for Ollama models (#130)."""
         if opts.get("api_base"):
             return opts["api_base"]
         return (os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434") if model.startswith("ollama") else ""
     ```
   - **`:38`:** change to
     `if "cloud" in model.lower() or urlparse(effective_base(model, opts)).hostname == "ollama.com":`.
   - **`:45`:** change to `base = effective_base(model, opts)`.

   Verify: `pytest -q tests/test_llm.py` passes, including
   `test_destination_inference_and_override` and the pinned
   `d("openai/gpt", {"destination": "local"}) == "local"`.

   Traps:
   - Read the env var at call time, never at import, because tests use
     `monkeypatch.setenv`.
   - `urlparse("")` has hostname `None`, which is fine for non-Ollama models.

2. `tests/test_llm.py`: after `test_destination_inference_and_override`
   (`:71`), add `test_override_checks_env_base(monkeypatch)`. Call
   `monkeypatch.setenv("OLLAMA_API_BASE", "https://ollama.com")` and assert:
   - `llm.destination(LOCAL, {"destination": "local"})` and
     `{"destination": "our-cloud"}` each raise `RuntimeError` matching
     "contradicts";
   - `llm.destination(LOCAL, {}) == "third-party"`;
   - `llm.destination(LOCAL, {"destination": "local", "api_base": "http://host.docker.internal:11434"}) == "local"`.

   Verify: the first two asserts fail on main. Stash `app/llm.py`, rebuild,
   run, then pop and rebuild.

   Traps:
   - If the Docker test environment already sets `OLLAMA_API_BASE`, other
     tests are unaffected, because `monkeypatch` restores it. Check with
     `grep -rn OLLAMA_API_BASE docker-compose*.yml .env*`.

## Tests

The full suite passes, and the new test fails on main.

## Rollback

Revert the commit.
