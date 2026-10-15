import json

import boto3
import pytest
from moto import mock_aws

from app import conn_settings as cs, db
from app.schema import ReferenceCase


@pytest.fixture
def managed(monkeypatch):
    """Secrets Manager (moto) holding the six secrets, empty, and SETTINGS_SECRET_IDS pointing at them."""
    for k, v in dict(AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1").items():
        monkeypatch.setenv(k, v)
    db.init()
    with mock_aws():
        sm = boto3.client("secretsmanager")
        ids = {n: sm.create_secret(Name=f"t/{n}")["ARN"] for n in cs.SECRET_NAMES}
        monkeypatch.setenv("SETTINGS_SECRET_IDS", json.dumps(ids))
        cs.clear_cache()
        with db.connect() as c:
            c.execute("delete from connection_settings")
        yield sm, ids
    cs.clear_cache()
    with db.connect() as c:
        c.execute("delete from connection_settings")


@pytest.fixture
def clock(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(cs.time, "monotonic", lambda: t[0])
    return t


def test_unmanaged_reads_the_environment(monkeypatch):
    monkeypatch.delenv("SETTINGS_SECRET_IDS", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "env-key")
    assert cs.get("BRAVE_API_KEY") == "env-key" and not cs.editable()
    with pytest.raises(KeyError):
        cs.get("S3_BUCKET")  # Terraform-only names are not managed


def test_managed_secret_never_reads_the_environment(managed, monkeypatch):
    sm, ids = managed
    monkeypatch.setenv("BRAVE_API_KEY", "stale-env-key")
    assert cs.get("BRAVE_API_KEY") is None  # created, never set: not configured
    sm.put_secret_value(SecretId=ids["BRAVE_API_KEY"], SecretString=cs.CLEARED)
    cs.clear_cache()
    assert cs.get("BRAVE_API_KEY") is None  # cleared
    sm.put_secret_value(SecretId=ids["BRAVE_API_KEY"], SecretString="sm-key")
    cs.clear_cache()
    assert cs.get("BRAVE_API_KEY") == "sm-key"


def test_plain_row_beats_environment(managed, monkeypatch):
    monkeypatch.setenv("DRAFT_MODEL", "bedrock/from-env")
    assert cs.get("DRAFT_MODEL") == "bedrock/from-env"
    with db.connect() as c:
        c.execute("insert into connection_settings(name, value, changed_by) values ('DRAFT_MODEL','bedrock/from-row','a')")
    cs.clear_cache()
    assert cs.get("DRAFT_MODEL") == "bedrock/from-row"


def test_cache_holds_until_the_ttl(managed, clock):
    sm, ids = managed
    sm.put_secret_value(SecretId=ids["OPENAI_API_KEY"], SecretString="one")
    assert cs.get("OPENAI_API_KEY") == "one"
    sm.put_secret_value(SecretId=ids["OPENAI_API_KEY"], SecretString="two")
    clock[0] += cs.TTL - 1
    assert cs.get("OPENAI_API_KEY") == "one"
    clock[0] += 2
    assert cs.get("OPENAI_API_KEY") == "two"


def test_outage_serves_last_value_then_fails(managed, clock, monkeypatch):
    sm, ids = managed
    sm.put_secret_value(SecretId=ids["GEMINI_API_KEY"], SecretString="k")
    assert cs.get("GEMINI_API_KEY") == "k"

    def down():
        raise ConnectionError("endpoint unreachable")
    monkeypatch.setattr(cs, "_client", down)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    clock[0] += cs.STALE_LIMIT - 1
    assert cs.get("GEMINI_API_KEY") == "k"
    clock[0] += 2
    with pytest.raises(ConnectionError):
        cs.get("GEMINI_API_KEY")  # never the environment


def test_audit_table_is_append_only_for_the_app_role():
    with db.connect() as c:
        db.init()
        c.execute(db.ROLES.read_text())
        ok = c.execute("select has_table_privilege('refs_app','connection_setting_changes','insert'), "
                       "has_table_privilege('refs_app','connection_setting_changes','update'), "
                       "has_table_privilege('refs_app','connection_setting_changes','delete')").fetchone()
    assert ok == (True, False, False)


def test_callers_pick_up_a_change_after_the_ttl(managed, clock, monkeypatch):
    from app import crawl, llm, research
    sm, ids = managed
    sm.put_secret_value(SecretId=ids["BRAVE_API_KEY"], SecretString="old")
    seen = []
    monkeypatch.setattr(research, "_brave_attempts", lambda client, q, key: seen.append(key) or [])
    research.brave_search("q")
    sm.put_secret_value(SecretId=ids["BRAVE_API_KEY"], SecretString="new")
    research.brave_search("q")
    clock[0] += cs.TTL + 1
    research.brave_search("q")
    assert seen == ["old", "old", "new"]

    monkeypatch.setenv("GRAPH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "env-secret")  # managed: ignored
    with pytest.raises(RuntimeError, match="GRAPH_CLIENT_SECRET"):
        crawl.Graph("t").login()

    monkeypatch.setenv("EXTRACT_MODEL", "bedrock/env-model")
    with db.connect() as c:
        c.execute("insert into connection_settings(name, value, changed_by) values ('EXTRACT_MODEL','bedrock/row-model','a')")
    clock[0] += cs.TTL + 1
    calls = []
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: calls.append(kw) or (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        llm.complete_json("EXTRACT_MODEL", "s", "u", ReferenceCase, data_class="public")
    assert calls[0]["model"] == "bedrock/row-model"
