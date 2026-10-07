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


def run(monkeypatch, reply, alias="EXTRACT_MODEL", finish="stop"):
    calls = []
    monkeypatch.setenv("EXTRACT_MODEL", "m-small")
    monkeypatch.setenv("DRAFT_MODEL", "m-mid")
    monkeypatch.setattr(llm.litellm, "completion", fake(reply, calls, finish))
    return llm.complete_json(alias, "sys", "usr", ReferenceCase), calls[0]


def test_valid_reply_and_model_from_env(monkeypatch):
    case, kw = run(monkeypatch, '{"title": {"value": "T", "source_quote": "q"}}', "DRAFT_MODEL")
    assert case.title.value == "T"
    assert kw["model"] == "m-mid"


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
        llm.complete_json("EXTRACT_MODEL", "s", "u", ReferenceCase)


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
