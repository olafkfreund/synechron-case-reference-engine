import json
import os
from urllib.parse import urlparse

import litellm
from pydantic import BaseModel, ValidationError

from app import db
from app.schema import llm_schema

litellm.turn_off_message_logging = True  # document text is never logged
MAX_TOKENS = 8192  # a full ReferenceCase with quotes; Bedrock's default may cut it off


class PolicyError(Exception):
    """A model may not read this data class. Carries the alias and class only, never content."""


def profile(alias: str) -> dict:
    """Optional JSON in <ALIAS>_OPTIONS: destination, think, num_ctx, mode, api_base."""
    var = f"{alias}_OPTIONS"
    try:
        opts = json.loads(os.environ.get(var) or "{}")
    except ValueError:
        opts = None
    if not isinstance(opts, dict):
        raise RuntimeError(f"{var} must be a JSON object")
    return opts


def destination(model: str, opts: dict) -> str:
    """local | our-cloud | third-party. Fails closed: anything unknown is third-party."""
    if opts.get("destination") in ("local", "our-cloud", "third-party"):
        return opts["destination"]
    if model.startswith("bedrock/"):
        return "our-cloud"
    # "cloud" anywhere (name-cloud, name:cloud, any case): the local Ollama forwards those to ollama.com
    if model.startswith("ollama") and "cloud" not in model.lower():
        base = opts.get("api_base") or os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434"
        if urlparse(base).hostname in ("localhost", "127.0.0.1", "::1"):
            return "local"
    return "third-party"


def allowed(dest: str, data_class: str, model: str) -> bool:
    if data_class in ("public", "sanitised") or dest in ("local", "our-cloud"):
        return True
    if data_class != "confidential":  # unknown or misspelled class: fail closed
        return False
    with db.connect() as c:
        return c.execute(
            "select 1 from model_approvals where model=%s and data_class=%s and expires_at > now()",
            (model, data_class)).fetchone() is not None


def complete_json[M: BaseModel](alias: str, system: str, user: str, model_cls: type[M], *, data_class: str) -> M:
    """alias is an env var name (EXTRACT_MODEL | DRAFT_MODEL) holding the LiteLLM model id."""
    model = os.environ.get(alias)
    if not model:
        raise RuntimeError(f"{alias} is not set; set it to a LiteLLM model id (e.g. bedrock/<model-id>)")
    opts = profile(alias)
    if not allowed(destination(model, opts), data_class, model):
        raise PolicyError(f"{alias} may not read {data_class} data")
    ollama = model.startswith("ollama")
    json_mode = opts.get("mode", "json" if ollama else "schema") == "json"
    schema = llm_schema(model_cls)
    extra = {}
    if json_mode:
        system += f"\n\nReply with JSON matching this schema: {json.dumps(schema)}"
        extra["response_format"] = {"type": "json_object"}
    else:
        extra["response_format"] = {"type": "json_schema", "json_schema": {
            "name": model_cls.__name__, "schema": schema, "strict": True}}
    if ollama:
        for k in ("think", "num_ctx", "repeat_penalty", "api_base"):
            if k in opts:
                extra[k] = opts[k]
        # not OLLAMA_API_KEY: LiteLLM reads that one itself and sends it to every Ollama host
        if urlparse(opts.get("api_base", "")).hostname == "ollama.com" and os.environ.get("OLLAMA_CLOUD_KEY"):
            extra["api_key"] = os.environ["OLLAMA_CLOUD_KEY"]
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=MAX_TOKENS,
        **extra,
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise ValueError(f"{alias} reply truncated at max_tokens={MAX_TOKENS}")
    try:
        return model_cls.model_validate_json(choice.message.content or "")
    except ValidationError as e:
        # errors without input values: the reply quotes the document, and job errors are stored
        detail = e.errors(include_input=False, include_url=False)
        raise ValueError(f"{alias} reply is not a valid {model_cls.__name__}: {detail}") from None
