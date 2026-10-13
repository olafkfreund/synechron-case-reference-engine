import json
import logging
import os
from urllib.parse import urlparse

import litellm
from pydantic import BaseModel, ValidationError

from app import db
from app.schema import llm_schema

log = logging.getLogger(__name__)
ECHO_RETRIES = 0  # for tests
SCHEMA_WORDS = {"$defs", "properties", "required", "additionalProperties", "type", "title"}
CORRECTION = ("That was the schema, not the answer. Reply with one JSON object containing the data, "
              "using the field names above. Do not include the schema.")

litellm.turn_off_message_logging = True  # document text is never logged
MAX_TOKENS = 8192  # a full ReferenceCase with quotes; Bedrock's default may cut it off


class PolicyError(Exception):
    """A model may not read this data class. Carries the alias and class only, never content."""


def profile(alias: str) -> dict:
    """Optional JSON in <ALIAS>_OPTIONS: destination, think, num_ctx, repeat_penalty, mode, api_base."""
    var = f"{alias}_OPTIONS"
    try:
        opts = json.loads(os.environ.get(var) or "{}")
    except ValueError:
        opts = None
    if not isinstance(opts, dict):
        raise RuntimeError(f"{var} must be a JSON object")
    return opts


def effective_base(model: str, opts: dict) -> str:
    """Where LiteLLM sends the request: api_base, else OLLAMA_API_BASE (which LiteLLM reads) for Ollama models (#130)."""
    if opts.get("api_base"):
        return opts["api_base"]
    return (os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434") if model.startswith("ollama") else ""


def _ollama_cloud(base: str) -> bool:
    host = urlparse(base).hostname or ""
    return host == "ollama.com" or host.endswith(".ollama.com")


def destination(model: str, opts: dict) -> str:
    """local | our-cloud | third-party. Fails closed: anything unknown is third-party."""
    dest = opts.get("destination")
    if dest == "third-party":
        return dest
    if dest in ("local", "our-cloud"):
        # an override may name a host the inference cannot see (Docker), never contradict a known third party
        if "cloud" in model.lower() or _ollama_cloud(effective_base(model, opts)):
            raise RuntimeError(f"destination {dest!r} contradicts the model or api_base; fix the *_OPTIONS")
        return dest
    if model.startswith("bedrock/"):
        return "our-cloud"
    # "cloud" anywhere (name-cloud, name:cloud, any case): the local Ollama forwards those to ollama.com
    if model.startswith("ollama") and "cloud" not in model.lower():
        base = effective_base(model, opts)
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


def _schema_echo(text: str, schema: dict) -> bool:
    """True when a reply is the schema itself (or one key holding it), not data (#245). Never logs or returns content."""
    try:
        obj = json.loads(text or "")
    except ValueError:
        return False
    if not isinstance(obj, dict):
        return False
    fields = set(schema.get("required", []))  # not all properties: ReferenceCase has a field "title"

    def echo(o):
        return isinstance(o, dict) and len(SCHEMA_WORDS & o.keys()) >= 2 and not fields & o.keys()
    return echo(obj) or (len(obj) == 1 and echo(next(iter(obj.values()))))


def _example(schema: dict) -> object:
    """The schema's shape with a type label for every value, so none reads as a default answer (#245). Deterministic."""
    defs = schema.get("$defs", {})

    def build(n):
        if "$ref" in n:
            return build(defs[n["$ref"].rsplit("/", 1)[-1]])
        if "enum" in n:
            return "<one of: " + " | ".join(str(v) for v in n["enum"]) + ">"
        for k in ("anyOf", "oneOf"):
            if k in n:
                return build(next((b for b in n[k] if b.get("type") != "null"), n[k][0]))
        t = n.get("type")
        if t == "object" or "properties" in n:
            return {k: build(v) for k, v in n.get("properties", {}).items()}
        if t == "array":
            return [build(n.get("items", {}))]
        return {"string": "<string>", "integer": "<number>", "number": "<number>", "boolean": "<true or false>"}.get(t, "<string>")
    return build(schema)


def json_cue(schema: dict) -> str:
    return ("\n\nReply with one JSON object that follows this JSON Schema. The schema describes the format; do not repeat it."
            f"\nSchema: {json.dumps(schema)}"
            f"\nExample of the shape (each value shows its type, not an answer): {json.dumps(_example(schema), ensure_ascii=False)}")


def complete_json[M: BaseModel](alias: str, system: str, user: str, model_cls: type[M], *, data_class: str) -> M:
    """alias is an env var name (EXTRACT_MODEL | DRAFT_MODEL) holding the LiteLLM model id."""
    global ECHO_RETRIES
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
        system += json_cue(schema)
        extra["response_format"] = {"type": "json_object"}
    else:
        extra["response_format"] = {"type": "json_schema", "json_schema": {
            "name": model_cls.__name__, "schema": schema, "strict": True}}
    if ollama:
        for k in ("think", "num_ctx", "repeat_penalty", "api_base"):
            if k in opts:
                extra[k] = opts[k]
        # not OLLAMA_API_KEY: LiteLLM reads that one itself and sends it to every Ollama host
        if _ollama_cloud(effective_base(model, opts)) and os.environ.get("OLLAMA_CLOUD_KEY"):
            extra["api_key"] = os.environ["OLLAMA_CLOUD_KEY"]

    def too_long(more):  # Ollama drops the start of a prompt that overflows its context, without an error
        est = (len(system) + len(user) + sum(len(m["content"]) for m in more)) // 3  # characters per token; size only
        return ollama and alias == "EXTRACT_MODEL" and "num_ctx" in opts and est + MAX_TOKENS > opts["num_ctx"], est

    def _call(more=()):
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]},
                {"role": "user", "content": user},
                *more,
            ],
            temperature=0,
            max_tokens=MAX_TOKENS,
            **extra,
        )
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise ValueError(f"{alias} reply truncated at max_tokens={MAX_TOKENS}")
        return choice.message.content or ""

    over, est = too_long(())
    if over:
        raise ValueError(f"{alias} prompt (~{est} tokens) does not fit num_ctx={opts['num_ctx']}")
    text = _call()
    retried = ""
    for attempt in (0, 1):
        try:
            return model_cls.model_validate_json(text)
        except ValidationError as e:
            # errors without input values: the reply quotes the document, and job errors are stored
            detail = e.errors(include_input=False, include_url=False)
            err = ValueError(f"{alias} reply is not a valid {model_cls.__name__}: {detail}{retried}")
            if attempt or not _schema_echo(text, schema):
                raise err from None
            more = ({"role": "assistant", "content": text}, {"role": "user", "content": CORRECTION})
            if too_long(more)[0]:
                raise err from None
            ECHO_RETRIES += 1
            log.warning("schema echo: alias=%s model=%s schema=%s", alias, model, model_cls.__name__)
            text = _call(more)
            retried = " (after a schema-echo retry)"
