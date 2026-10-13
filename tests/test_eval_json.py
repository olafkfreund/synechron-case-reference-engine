import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from app import llm

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_json", ROOT / "scripts" / "eval_json.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

SECRET = "Zorbulon settlement"


def test_measure_counts_classes_only(monkeypatch):
    replies = iter([SimpleNamespace(claims=[1, 2]), ValueError(f"{SECRET} (after a schema-echo retry)"), KeyError(SECRET)])

    def call():
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r
    r = ev.measure(call, 3)
    assert (r["success"], r["echo"], r["other"], r["size"]) == (1, 1, 1, 2)
    assert SECRET not in json.dumps(r)


def test_main_prints_counts_and_never_text(monkeypatch, capsys):
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/m")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", "{}")  # main() rewrites it; pytest restores it
    sent = []

    def completion(**kw):
        sent.append(kw)
        msg = SimpleNamespace(content='{"title": "T", "takeaways": [], "themes": [{"heading": "H", "text": "t"}]}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    monkeypatch.setattr(llm.litellm, "completion", completion)
    monkeypatch.setattr(ev.db, "connect", lambda: FakeConn())
    monkeypatch.setattr(ev.anonymise, "load_clients", lambda c: [])
    assert ev.main(["--brief-id", "1", "--calls", "brief", "--n", "2", "--mode", "schema"]) == 0
    out = capsys.readouterr().out
    assert "success=2" in out and "mode=schema" in out and SECRET not in out
    assert sent[0]["response_format"]["type"] == "json_schema"  # --mode overrode the Ollama default
    assert sent[0]["messages"][1]["content"] != sent[1]["messages"][1]["content"]  # nonce differs


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        return SimpleNamespace(fetchone=lambda: ([{"quote": SECRET, "type": "x"}],))


def test_claims_echo_is_counted_as_echo(monkeypatch):
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/m")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", "{}")
    monkeypatch.setattr(ev.research, "complete_json", ev.research.complete_json)  # restored after the spy
    echo = json.dumps({"properties": {}, "type": "object", "required": ["claims"]})
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=echo), finish_reason="stop")]))

    class Conn(FakeConn):
        def execute(self, sql, args=()):
            return SimpleNamespace(fetchone=lambda: ("q", {"pages": [{"publisher": "p", "markdown": "m"}]}))
    monkeypatch.setattr(ev.db, "connect", lambda: Conn())
    r = ev.measure(ev.claims_call(1), 2)
    assert (r["echo"], r["other"], r["success"], r["retries"]) == (2, 0, 0, 2)
