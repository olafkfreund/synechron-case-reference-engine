import json
import os
import sys

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


# ---- writes, through the Settings routes ----

from tests.test_auth import ADMIN, REV, client, env  # noqa: E402,F401

SENTINEL = "SECRET-SENTINEL-288"
GUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def admins(env, managed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MODEL_CHOICES", "bedrock/m1,bedrock/m2")
    monkeypatch.setenv("EXTRACT_MODEL", "bedrock/m1")
    with db.connect() as c:
        c.execute("delete from connection_setting_changes")
    return client([ADMIN], sub="admin-a"), client([ADMIN], sub="admin-b")


def post(c, path, **form):
    name, _, action = path.partition("/")
    if action in ("approve", "reject", "withdraw") and "proposal" not in form:  # the proposal on screen
        with db.connect() as conn:
            form["proposal"] = (cs.proposal(conn, name) or (0,))[0]
    return c.post("/admin/settings/connections/" + path, data=form, follow_redirects=False)


def audit_text():
    with db.connect() as c:
        return " ".join(str(r) for r in c.execute("select * from connection_setting_changes").fetchall())


def test_one_admin_save_takes_effect_and_never_echoes(admins):
    a, _ = admins
    assert post(a, "brave", BRAVE_API_KEY=SENTINEL).status_code == 303
    assert cs.get("BRAVE_API_KEY") == SENTINEL
    html = a.get("/admin/settings").text
    assert 'badge ok">Configured' in html.split("<h3>Brave search</h3>")[1].split("conn-card")[0] and SENTINEL not in html
    assert "applied" in audit_text() and SENTINEL not in audit_text()
    assert post(a, "brave", BRAVE_API_KEY="").status_code == 303  # blank: unchanged
    assert cs.get("BRAVE_API_KEY") == SENTINEL


def test_validation_names_the_field_never_the_value(admins):
    a, _ = admins
    for path, form, field in (("sharepoint", {"GRAPH_CLIENT_ID": "not-a-guid-" + SENTINEL, "GRAPH_CLIENT_SECRET": "x"}, "GRAPH_CLIENT_ID"),
                              ("confluence", {"CONFLUENCE_EMAIL": SENTINEL, "CONFLUENCE_TOKEN": "x"}, "CONFLUENCE_EMAIL"),
                              ("brave", {"BRAVE_API_KEY": SENTINEL + "\nx"}, "BRAVE_API_KEY"),
                              ("brave", {"BRAVE_API_KEY": cs.CLEARED}, "BRAVE_API_KEY"),
                              ("brave", {"BRAVE_API_KEY": "k" * 4097}, "BRAVE_API_KEY"),
                              ("models", {"EXTRACT_MODEL": "openai/" + SENTINEL}, "EXTRACT_MODEL")):
        r = post(a, path, **form)
        assert r.status_code == 400 and field in r.text and SENTINEL not in r.text, (path, field)
    assert audit_text() == ""


def test_new_id_needs_its_secret(admins):
    a, _ = admins
    r = post(a, "sharepoint", GRAPH_CLIENT_ID=GUID)
    assert r.status_code == 400 and "GRAPH_CLIENT_SECRET" in r.text
    assert post(a, "sharepoint", GRAPH_CLIENT_ID=GUID, GRAPH_CLIENT_SECRET=SENTINEL).status_code == 303
    assert (cs.get("GRAPH_CLIENT_ID"), cs.get("GRAPH_CLIENT_SECRET")) == (GUID, SENTINEL)
    assert post(a, "GRAPH_CLIENT_SECRET/restore").status_code == 400  # half a pair is never restored


def test_two_admin_key_waits_for_a_second_admin(admins):
    a, b = admins
    assert post(a, "openai", OPENAI_API_KEY=SENTINEL).status_code == 303
    assert cs.get("OPENAI_API_KEY") is None  # never set before: the proposal must not become current
    html = b.get("/admin/settings").text
    assert "Waiting for approval: proposed by admin-a" in html and SENTINEL not in html
    assert post(a, "OPENAI_API_KEY/approve").status_code == 403
    assert post(a, "openai", OPENAI_API_KEY="other").status_code == 409  # one open proposal
    assert post(b, "OPENAI_API_KEY/approve").status_code == 303
    assert cs.get("OPENAI_API_KEY") == SENTINEL
    assert SENTINEL not in audit_text()


def test_model_reject_withdraw_and_restore(admins):
    a, b = admins
    assert post(a, "models", EXTRACT_MODEL="bedrock/m2").status_code == 303
    assert post(b, "EXTRACT_MODEL/withdraw").status_code == 403
    assert post(b, "EXTRACT_MODEL/reject").status_code == 303
    assert cs.get("EXTRACT_MODEL") == "bedrock/m1"
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    assert post(a, "EXTRACT_MODEL/withdraw").status_code == 303
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    post(b, "EXTRACT_MODEL/approve")
    assert cs.get("EXTRACT_MODEL") == "bedrock/m2"
    assert post(b, "EXTRACT_MODEL/restore").status_code == 303  # proposes m1 again
    assert cs.get("EXTRACT_MODEL") == "bedrock/m2"
    assert post(a, "EXTRACT_MODEL/approve").status_code == 303
    assert cs.get("EXTRACT_MODEL") == "bedrock/m1"


def test_restore_and_clear_a_secret(admins):
    a, _ = admins
    post(a, "brave", BRAVE_API_KEY="k1")
    post(a, "brave", BRAVE_API_KEY="k2")
    assert post(a, "BRAVE_API_KEY/restore").status_code == 303
    assert cs.get("BRAVE_API_KEY") == "k1"
    assert post(a, "BRAVE_API_KEY/clear").status_code == 303
    assert cs.get("BRAVE_API_KEY") is None
    assert post(a, "EXTRACT_MODEL/clear").status_code == 400


def test_writes_are_admin_only(admins):
    rev = client([REV], sub="rev")
    assert post(rev, "brave", BRAVE_API_KEY=SENTINEL).status_code == 403
    assert post(rev, "BRAVE_API_KEY/clear").status_code == 403
    assert cs.get("BRAVE_API_KEY") is None


def test_changes_show_on_audit_without_secrets(admins):
    a, _ = admins
    post(a, "brave", BRAVE_API_KEY=SENTINEL)
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    html = a.get("/admin/audit").text
    assert "Connection changes" in html and "BRAVE_API_KEY" in html and "bedrock/m2" in html and SENTINEL not in html


def test_a_decision_applies_only_to_the_proposal_seen(admins):
    a, b = admins
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    with db.connect() as conn:
        seen = cs.proposal(conn, "EXTRACT_MODEL")[0]
    post(a, "EXTRACT_MODEL/withdraw")
    post(a, "models", EXTRACT_MODEL="bedrock/m2")  # a new proposal replaces the one b saw
    assert post(b, "EXTRACT_MODEL/approve", proposal=seen).status_code == 409
    assert cs.get("EXTRACT_MODEL") == "bedrock/m1"


def test_a_failed_commit_leaves_the_old_secret_in_force(admins, monkeypatch):
    import psycopg
    a, _ = admins
    post(a, "brave", BRAVE_API_KEY="k1")

    real = psycopg.Connection.commit

    def fail(self):  # only the settings change's commit: the login check commits too
        if sys._getframe(1).f_code.co_name == "_commit_and_activate":
            raise psycopg.OperationalError("connection lost")
        return real(self)
    with monkeypatch.context() as m, pytest.raises(psycopg.OperationalError):
        m.setattr(psycopg.Connection, "commit", fail)
        post(a, "brave", BRAVE_API_KEY="k2")
    cs.clear_cache()
    assert cs.get("BRAVE_API_KEY") == "k1"
    assert audit_text().count("applied") == 1
    stages = cs._stages(json.loads(os.environ["SETTINGS_SECRET_IDS"])["BRAVE_API_KEY"])
    assert len(stages) >= 2  # k2 was written: the failure came after Secrets Manager, not before


def test_secret_proposal_reject_and_two_admin_restore(admins):
    a, b = admins
    post(a, "gemini", GEMINI_API_KEY="g1")
    post(b, "GEMINI_API_KEY/approve")
    post(a, "gemini", GEMINI_API_KEY="g2")
    assert post(b, "GEMINI_API_KEY/reject").status_code == 303
    assert cs.get("GEMINI_API_KEY") == "g1"
    post(a, "gemini", GEMINI_API_KEY="g2")
    post(b, "GEMINI_API_KEY/approve")
    assert post(a, "GEMINI_API_KEY/restore").status_code == 303  # proposes g1
    assert cs.get("GEMINI_API_KEY") == "g2"
    assert post(b, "GEMINI_API_KEY/approve").status_code == 303
    assert cs.get("GEMINI_API_KEY") == "g1"
    assert post(a, "GEMINI_API_KEY/clear").status_code == 303  # proposes "not configured"
    assert post(b, "GEMINI_API_KEY/approve").status_code == 303
    assert cs.get("GEMINI_API_KEY") is None


def test_decisions_are_admin_only(admins):
    a, _ = admins
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    rev = client([REV], sub="rev")
    for action in ("approve", "reject", "withdraw", "restore"):
        assert post(rev, "EXTRACT_MODEL/" + action).status_code == 403


def test_restore_checks_the_model_and_clear_needs_a_value(admins, monkeypatch):
    a, b = admins
    post(a, "models", EXTRACT_MODEL="bedrock/m2")
    post(b, "EXTRACT_MODEL/approve")
    monkeypatch.setenv("MODEL_CHOICES", "bedrock/m2")  # Terraform dropped m1 since
    assert post(a, "EXTRACT_MODEL/restore").status_code == 400
    assert post(a, "BRAVE_API_KEY/clear").status_code == 409  # never set
