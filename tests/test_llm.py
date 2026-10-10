import json
from types import SimpleNamespace

import pytest

from app import llm
from app.schema import ReferenceCase


def fake(reply, calls, finish="stop"):
    def completion(**kw):
        calls.append(kw)
        msg = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])
    return completion


def run(monkeypatch, reply, alias="EXTRACT_MODEL", finish="stop", model="bedrock/m", opts=None, dc="confidential"):
    calls = []
    monkeypatch.setenv("EXTRACT_MODEL", model)
    monkeypatch.setenv("DRAFT_MODEL", "bedrock/m-mid")
    monkeypatch.delenv("EXTRACT_MODEL_OPTIONS", raising=False)
    if opts is not None:
        monkeypatch.setenv(f"{alias}_OPTIONS", json.dumps(opts))
    monkeypatch.setattr(llm.litellm, "completion", fake(reply, calls, finish))
    return llm.complete_json(alias, "sys", "usr", ReferenceCase, data_class=dc), calls[0]


def test_valid_reply_and_model_from_env(monkeypatch):
    case, kw = run(monkeypatch, '{"title": {"value": "T", "source_quote": "q"}}', "DRAFT_MODEL")
    assert case.title.value == "T"
    assert kw["model"] == "bedrock/m-mid"


def test_cache_control_and_strict_schema(monkeypatch):
    _, kw = run(monkeypatch, '{"title": {}}')
    assert kw["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    js = kw["response_format"]["json_schema"]
    assert js["strict"] is True
    assert kw["temperature"] == 0 and kw["max_tokens"] == llm.MAX_TOKENS
    assert "unsourced" not in str(js["schema"])


def test_missing_env_raises(monkeypatch):
    monkeypatch.delenv("EXTRACT_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="EXTRACT_MODEL"):
        llm.complete_json("EXTRACT_MODEL", "s", "u", ReferenceCase, data_class="public")


def test_invalid_reply_raises(monkeypatch):
    with pytest.raises(ValueError, match="not a valid ReferenceCase"):
        run(monkeypatch, '{"title": 5, "bogus": 1}')


def test_invalid_reply_error_omits_document_text(monkeypatch):
    with pytest.raises(ValueError) as e:
        run(monkeypatch, '{"title": {"value": "SECRET client text", "source_quote": 5}}')
    assert "SECRET" not in str(e.value) and e.value.__cause__ is None


def test_truncated_and_empty_replies_raise(monkeypatch):
    with pytest.raises(ValueError, match="truncated"):
        run(monkeypatch, '{"title": {"va', finish="length")
    with pytest.raises(ValueError, match="not a valid"):
        run(monkeypatch, None)


LOCAL = "ollama_chat/qwen3:14b"


def test_destination_inference_and_override():
    d = llm.destination
    assert d("bedrock/x", {}) == "our-cloud"
    assert d(LOCAL, {}) == "local"
    assert d(LOCAL, {"api_base": "http://127.0.0.1:11434"}) == "local"
    assert d(LOCAL, {"api_base": "http://host.docker.internal:11434"}) == "third-party"
    assert d("ollama_chat/gpt-oss:120b-cloud", {}) == "third-party"
    assert d(LOCAL, {"api_base": "https://ollama.com"}) == "third-party"
    assert d("openai/gpt", {}) == "third-party"
    assert d("openai/gpt", {"destination": "local"}) == "local"
    assert d("bedrock/x", {"destination": "bogus"}) == "our-cloud"
    assert d("ollama_chat/gemma4:cloud", {"destination": "third-party"}) == "third-party"
    for model, opts in (("ollama_chat/gemma4:cloud", {"destination": "local"}),
                        (LOCAL, {"destination": "our-cloud", "api_base": "https://ollama.com"})):
        with pytest.raises(RuntimeError, match="contradicts"):
            d(model, opts)


@pytest.mark.parametrize("dc", ["confidential", "sanitised", "public"])
@pytest.mark.parametrize("dest", ["local", "our-cloud", "third-party"])
@pytest.mark.parametrize("approval", ["valid", "expired", "other-model", "other-class", "none"])
def test_policy_matrix(dc, dest, approval):
    from app import db
    db.init()
    with db.connect() as c:
        try:
            if approval != "none":
                c.execute(
                    "insert into model_approvals(model,data_class,approved_by,expires_at) values (%s,%s,'a',"
                    "now() + case when %s='expired' then interval '-1 day' else interval '1 day' end)",
                    ("other" if approval == "other-model" else "m1",
                     "public" if approval == "other-class" else dc, approval))
            c.commit()
            want = dc != "confidential" or dest != "third-party" or approval == "valid"
            assert llm.allowed(dest, dc, "m1") is want
        finally:
            c.execute("delete from model_approvals where approved_by='a'")
            c.commit()


def test_override_checks_env_base(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "https://ollama.com")
    for dest in ("local", "our-cloud"):
        with pytest.raises(RuntimeError, match="contradicts"):
            llm.destination(LOCAL, {"destination": dest})
    assert llm.destination(LOCAL, {}) == "third-party"
    assert llm.destination(LOCAL, {"destination": "local", "api_base": "http://host.docker.internal:11434"}) == "local"


def test_policy_error_has_no_content_and_blocks_before_request(monkeypatch):
    from app import db
    db.init()
    calls = []
    monkeypatch.setenv("EXTRACT_MODEL", "openai/gpt")
    monkeypatch.setattr(llm.litellm, "completion", fake("{}", calls))
    with pytest.raises(llm.PolicyError) as e:
        llm.complete_json("EXTRACT_MODEL", "s", "SECRET text", ReferenceCase, data_class="confidential")
    assert "SECRET" not in str(e.value) and not calls


def test_json_mode_request_shape(monkeypatch):
    _, kw = run(monkeypatch, '{"title": {}}', model=LOCAL, opts={"think": False, "num_ctx": 24576, "repeat_penalty": 1.05})
    assert kw["response_format"] == {"type": "json_object"}
    assert "Reply with JSON matching this schema" in kw["messages"][0]["content"][0]["text"]
    assert kw["think"] is False and kw["num_ctx"] == 24576 and kw["repeat_penalty"] == 1.05 and "api_key" not in kw


def test_schema_mode_default_for_bedrock_and_ollama_only_options(monkeypatch):
    _, kw = run(monkeypatch, '{"title": {}}', opts={"think": False, "num_ctx": 1, "repeat_penalty": 1.05})
    assert kw["response_format"]["type"] == "json_schema"
    assert "think" not in kw and "num_ctx" not in kw and "repeat_penalty" not in kw


def test_ollama_cloud_key_only_for_ollama_com(monkeypatch):
    monkeypatch.setenv("OLLAMA_CLOUD_KEY", "k")
    _, kw = run(monkeypatch, '{"title": {}}', model=LOCAL, dc="public",
                opts={"api_base": "https://ollama.com", "mode": "schema"})
    assert kw["api_key"] == "k" and kw["api_base"] == "https://ollama.com"
    assert kw["response_format"]["type"] == "json_schema"
    _, kw = run(monkeypatch, '{"title": {}}', model=LOCAL)
    assert "api_key" not in kw and "api_base" not in kw


def test_bad_options_value(monkeypatch):
    for bad in ("{nope", "[1]"):
        monkeypatch.setenv("EXTRACT_MODEL", "bedrock/m")
        monkeypatch.setenv("EXTRACT_MODEL_OPTIONS", bad)
        with pytest.raises(RuntimeError, match="EXTRACT_MODEL_OPTIONS"):
            llm.complete_json("EXTRACT_MODEL", "s", "u", ReferenceCase, data_class="public")


@pytest.mark.parametrize("model", ["ollama_chat/gemma4:cloud", "ollama_chat/gpt-oss:120B-CLOUD", "ollama/x-Cloud"])
def test_cloud_tags_are_never_local(model):
    assert llm.destination(model, {}) == "third-party"


@pytest.mark.parametrize("data_class", ["Confidential", "confidental", "", None, "secret"])
def test_unknown_data_class_fails_closed(data_class):
    assert llm.allowed("third-party", data_class, "m") is False
    assert llm.allowed("local", data_class, "m") is True  # local stays fine


def test_db_outage_blocks_the_call(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "ollama_chat/gemma4:cloud")
    def down():
        raise llm.db.psycopg.OperationalError("db down")
    monkeypatch.setattr(llm.db, "connect", down)
    sent = []
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: sent.append(kw))
    with pytest.raises(Exception):
        llm.complete_json("EXTRACT_MODEL", "s", "SECRET", ReferenceCase, data_class="confidential")
    assert sent == []
