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
    monkeypatch.delenv("DRAFT_MODEL_OPTIONS", raising=False)
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
