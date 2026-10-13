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
    with pytest.raises(RuntimeError, match="contradicts"):  # any ollama.com subdomain is the cloud too
        llm.destination(LOCAL, {"destination": "local", "api_base": "https://api.ollama.com"})


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
    assert kw["messages"][0]["content"][0]["text"] == "sys" + llm.json_cue(llm.llm_schema(ReferenceCase)) and "do not repeat" not in kw["messages"][0]["content"][0]["text"]
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


def test_prompt_that_overflows_num_ctx_is_refused_before_the_call(monkeypatch):
    with pytest.raises(ValueError, match=r"does not fit num_ctx=9000") as e:
        run(monkeypatch, "{}", model="ollama_chat/m", opts={"num_ctx": 9000}, dc="public")
    assert "usr" not in str(e.value)  # size only, never content
    calls = []
    monkeypatch.setattr(llm.litellm, "completion", fake("{}", calls))
    with pytest.raises(ValueError):
        llm.complete_json("EXTRACT_MODEL", "s" * 3000, "u" * 3000, ReferenceCase, data_class="public")
    assert calls == []


def test_overflow_guard_covers_extraction_only(monkeypatch):
    # research's worst case sits right at the context limit (#212 G1), so only extraction is guarded
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/m")  # an Ollama model too, so only the alias check exempts it
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 100}))
    monkeypatch.setattr(llm.litellm, "completion", fake('{"title": {}}', []))
    llm.complete_json("DRAFT_MODEL", "s" * 3000, "u" * 3000, ReferenceCase, data_class="public")


ECHO = json.dumps({"$defs": {}, "properties": {"title": {}}, "required": ["title"], "type": "object"})
GOOD = '{"title": {"value": "T", "source_quote": "q"}}'


def seq(replies, calls):
    it = iter(replies)

    def completion(**kw):
        calls.append(kw)
        msg = SimpleNamespace(content=next(it))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    return completion


def go(monkeypatch, replies, opts=None, dc="public"):
    calls = []
    monkeypatch.setenv("EXTRACT_MODEL", LOCAL)
    monkeypatch.delenv("EXTRACT_MODEL_OPTIONS", raising=False)
    if opts:
        monkeypatch.setenv("EXTRACT_MODEL_OPTIONS", json.dumps(opts))
    monkeypatch.setattr(llm.litellm, "completion", seq(replies, calls))
    return lambda: llm.complete_json("EXTRACT_MODEL", "sys", "usr", ReferenceCase, data_class=dc), calls


def test_echo_then_valid_retries_once_with_the_correction(monkeypatch):
    f, calls = go(monkeypatch, [ECHO, GOOD])
    before = llm.ECHO_RETRIES
    assert f().title.value == "T"
    assert len(calls) == 2 and llm.ECHO_RETRIES == before + 1
    assert calls[1]["messages"][:2] == calls[0]["messages"]  # same system block (cache_control) and user turn
    assert calls[1]["messages"][2:] == [{"role": "assistant", "content": ECHO}, {"role": "user", "content": llm.CORRECTION}]
    assert calls[1]["response_format"] == calls[0]["response_format"]


def test_echo_twice_raises_without_content(monkeypatch):
    echo = json.dumps({"properties": {}, "type": "SECRET client text"})
    f, calls = go(monkeypatch, [echo, echo])
    with pytest.raises(ValueError, match=r"after a schema-echo retry") as e:
        f()
    assert len(calls) == 2 and "SECRET" not in str(e.value)


def test_nested_echo_is_detected():
    schema = llm.llm_schema(ReferenceCase)
    assert llm._schema_echo(json.dumps({"ReferenceCase": json.loads(ECHO)}), schema)
    assert llm._schema_echo(ECHO, schema)
    for ok in ('{"title": {"value": "T"}}', '{"type": "x"}', "[1]", "nope", ""):
        assert not llm._schema_echo(ok, schema)


def test_other_invalid_replies_are_not_retried(monkeypatch):
    f, calls = go(monkeypatch, ['{"title": 5}', GOOD])
    with pytest.raises(ValueError, match="not a valid") as e:
        f()
    assert len(calls) == 1 and "retry" not in str(e.value)


def test_no_retry_when_it_would_overflow_num_ctx(monkeypatch):
    big = ECHO + " " * 30000
    f, calls = go(monkeypatch, [big, GOOD], opts={"num_ctx": 12000})
    with pytest.raises(ValueError, match="not a valid") as e:
        f()
    assert len(calls) == 1 and "retry" not in str(e.value)


def test_echo_log_line_has_no_content(monkeypatch, caplog):
    f, _ = go(monkeypatch, [ECHO, GOOD])
    with caplog.at_level("WARNING", logger="app.llm"):
        f()
    msg = caplog.text
    assert "schema echo" in msg and "EXTRACT_MODEL" in msg and "properties" not in msg and "usr" not in msg


def test_schema_mode_has_no_cue_and_no_retry_text_change(monkeypatch):
    _, kw = run(monkeypatch, GOOD)
    assert kw["messages"][0]["content"][0]["text"] == "sys"


def all_models():
    from app import brief, ingest, research, search, writeup
    from app.schema import References
    return [ReferenceCase, References, ingest.Triage, writeup.Writeup, research.Query, research.Claims,
            brief.Brief, search.Picks]


def test_no_model_field_is_named_like_a_schema_keyword():
    for m in all_models():
        assert not (llm.SCHEMA_WORDS - {"title", "type"}) & set(llm.llm_schema(m)["properties"]), m.__name__


def test_example_is_deterministic_and_has_the_schema_keys():
    for m in all_models():
        s = llm.llm_schema(m)
        ex = llm._example(s)
        assert ex == llm._example(s) and set(ex) == set(s["properties"]), m.__name__


def test_page_chars_still_fits_with_the_longer_cue(monkeypatch):
    from app import research
    monkeypatch.setenv("DRAFT_MODEL", LOCAL)
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 24576}))
    assert research.page_chars() > 500


def test_example_values_are_type_labels_never_literal_answers():
    def leaves(o):
        if isinstance(o, dict):
            return [x for v in o.values() for x in leaves(v)]
        return [x for v in o for x in leaves(v)] if isinstance(o, list) else [o]
    for m in all_models():
        s = llm.llm_schema(m)
        vals = leaves(llm._example(s))
        assert all(isinstance(v, str) and v.startswith("<") and v.endswith(">") for v in vals), m.__name__
    from app.ingest import Triage
    text = json.dumps(llm._example(llm.llm_schema(Triage)))
    assert text.count("true") == text.count("false") == text.count("<true or false>")  # no bare boolean literal
    assert ": true" not in text and ": false" not in text and ": 0" not in text


def test_default_cue_is_the_old_one_and_the_hint_adds_the_example():
    schema = llm.llm_schema(ReferenceCase)
    assert llm.json_cue(schema) == f"\n\nReply with JSON matching this schema: {json.dumps(schema)}"
    hint = llm.json_cue(schema, True)
    assert "do not repeat it" in hint and "not an answer" in hint and "<string>" in hint


def test_shape_hint_reaches_the_prompt_and_the_retry_works_with_both(monkeypatch):
    for hint in (False, True):
        f, calls = go(monkeypatch, [ECHO, GOOD])
        monkeypatch.setattr(llm.litellm, "completion", seq([ECHO, GOOD], calls))
        llm.complete_json("EXTRACT_MODEL", "sys", "usr", ReferenceCase, data_class="public", shape_hint=hint)
        sysmsg = calls[0]["messages"][0]["content"][0]["text"]
        assert ("do not repeat it" in sysmsg) is hint and len(calls) == 2


def test_only_claims_and_briefs_ask_for_the_hint(monkeypatch):
    import inspect
    from app import brief, research
    assert "shape_hint=True" in inspect.getsource(research.extract_claims)
    assert "shape_hint=True" in inspect.getsource(brief.run)
    import pathlib
    for f in pathlib.Path(llm.__file__).parent.glob("*.py"):
        if f.name not in ("research.py", "brief.py", "llm.py"):
            assert "shape_hint" not in f.read_text(), f.name


def test_real_schema_echoes_are_detected_bare_and_wrapped():
    from app import brief, research
    for m in (brief.Brief, research.Claims, ReferenceCase):
        schema = llm.llm_schema(m)
        assert llm._schema_echo(json.dumps(schema), schema), m.__name__
        assert llm._schema_echo(json.dumps({m.__name__: schema}), schema), m.__name__
    b = llm.llm_schema(brief.Brief)
    data = {"title": "T", "takeaways": [], "themes": []}
    assert not llm._schema_echo(json.dumps(data), b)
    assert not llm._schema_echo(json.dumps({**data, "type": "x"}), b)


def test_draft_model_retry_is_guarded_by_num_ctx(monkeypatch):
    calls = []
    monkeypatch.setenv("DRAFT_MODEL", LOCAL)
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 12000}))
    monkeypatch.setattr(llm.litellm, "completion", seq([ECHO + " " * 30000, GOOD], calls))
    with pytest.raises(ValueError, match="not a valid") as e:
        llm.complete_json("DRAFT_MODEL", "s", "u", ReferenceCase, data_class="public")
    assert len(calls) == 1 and "retry" not in str(e.value)


def test_truncated_retry_keeps_the_marker(monkeypatch):
    calls = []
    monkeypatch.setenv("EXTRACT_MODEL", LOCAL)
    monkeypatch.delenv("EXTRACT_MODEL_OPTIONS", raising=False)
    replies = iter([(ECHO, "stop"), ('{"ti', "length")])

    def completion(**kw):
        text, fin = next(replies)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=fin)])
    monkeypatch.setattr(llm.litellm, "completion", completion)
    with pytest.raises(ValueError, match=r"truncated.*after a schema-echo retry"):
        llm.complete_json("EXTRACT_MODEL", "s", "u", ReferenceCase, data_class="public")


def test_example_stops_at_recursive_refs():
    from pydantic import BaseModel

    class Node(BaseModel):
        name: str
        kids: list["Node"]
    ex = llm._example(llm.llm_schema(Node))
    assert ex == {"name": "<string>", "kids": ["<object>"]}
    assert llm._example({"type": "object", "properties": {"a": {"$ref": "#"}}}) == {"a": {"a": "<object>"}}
