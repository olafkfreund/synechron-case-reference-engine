import importlib.util
import json
import logging
import os
import re
import uuid
from pathlib import Path

import litellm
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import db, extract, ingest as ing, llm, search as sr, worker
from app.main import User
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401
from tests.test_review import DOCS, R, make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


# --- the restricted database role ------------------------------------------------------------------

@pytest.fixture
def app_role():
    db.init()
    with db.connect() as c:
        c.execute("drop owned by refs_app") if c.execute("select 1 from pg_roles where rolname='refs_app'").fetchone() else None
        c.execute("drop role if exists refs_app")
    yield
    with db.connect() as c:
        if c.execute("select 1 from pg_roles where rolname='refs_app'").fetchone():
            c.execute("drop owned by refs_app")
            c.execute("drop role refs_app")


def as_app(password="pw-1"):
    d = conninfo_to_dict(os.environ["DATABASE_URL"])
    return psycopg.connect(make_conninfo(**{**d, "user": "refs_app", "password": password}))


def test_app_role_is_idempotent_and_cannot_change_the_schema(app_role):
    with db.connect() as c:
        db.apply_app_role(c, "pw-1")
    with db.connect() as c:
        db.apply_app_role(c, "pw-2'; drop table sources; --")  # twice, with a password that needs quoting
    with db.connect() as c:
        assert c.execute("select rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolbypassrls from pg_roles "
                         "where rolname='refs_app'").fetchone() == (False, False, False, True, False)
    with as_app("pw-2'; drop table sources; --") as a:
        name = uuid.uuid4().hex
        a.execute("insert into sources(kind, name) values ('s3', %s)", (name,))  # row access works
        assert a.execute("select count(*) from sources where name=%s", (name,)).fetchone()[0] == 1
        a.execute("delete from sources where name=%s", (name,))
        a.execute("insert into jobs(kind) values ('x')")  # sequences too
        a.execute("delete from jobs where kind='x'")
    for ddl in ("create table t(x int)", "drop table sources", "alter table sources add column z int",
                "truncate sources", "create role evil", "create schema s"):
        with as_app("pw-2'; drop table sources; --") as a:
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.DuplicateTable)) as e:
                a.execute(ddl)
            assert isinstance(e.value, psycopg.errors.InsufficientPrivilege), ddl
    with db.connect() as c:  # the schema is intact
        assert c.execute("select to_regclass('public.sources')").fetchone()[0]


def test_audit_log_tables_are_append_only_for_the_app(app_role):
    with db.connect() as c:
        db.apply_app_role(c, "pw-1")
    for t in ("source_class_changes", "source_acl_changes"):
        for stmt in (f"update {t} set changed_by='x'", f"delete from {t}"):
            with as_app("pw-1") as a:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    a.execute(stmt)


def test_tables_created_later_are_covered_by_default_privileges(app_role):
    with db.connect() as c:
        db.apply_app_role(c, "pw-1")
        c.execute("create table if not exists later_table(x int)")
    try:
        with as_app("pw-1") as a:
            a.execute("insert into later_table values (1)")
    finally:
        with db.connect() as c:
            c.execute("drop table later_table")


def test_init_applies_the_role_only_when_asked(app_role, monkeypatch):
    monkeypatch.delenv("DB_APP_ROLE_SECRET_ARN", raising=False)
    db.init()
    with db.connect() as c:
        assert not c.execute("select 1 from pg_roles where rolname='refs_app'").fetchone()
    monkeypatch.setenv("DB_APP_ROLE_SECRET_ARN", "arn:fake")
    monkeypatch.setattr(db, "_read_password", lambda arn: "from-secret")
    db.init()
    with as_app("from-secret") as a:
        a.execute("select 1 from sources limit 1")


def test_processes_do_not_run_ddl_unless_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "init", lambda: calls.append(1))
    monkeypatch.delenv("DB_INIT_ON_START", raising=False)
    db.init_if_requested()
    assert calls == []
    monkeypatch.setenv("DB_INIT_ON_START", "1")
    db.init_if_requested()
    assert calls == [1]
    src = {p: (ROOT / p).read_text() for p in ("app/main.py", "app/worker.py", "app/enqueue_crawls.py")}
    assert all("db.init()" not in s for s in src.values())  # only app.migrate calls init() directly


# --- audit and reminders ------------------------------------------------------------------------------

def test_audit_view_is_admin_only_paged_and_filtered(approved):
    cid = approved()
    u = uuid.uuid4().hex[:8]
    a, b = f"u{u}-0", f"u{u}-1"  # own users: other tests leave generations rows behind
    with db.connect() as c:
        for who, n in ((a, 55), (b, 3)):
            for _ in range(n):
                c.execute("insert into generations(user_id, format, case_ids) values (%s,'md',%s)", (who, [cid]))
    gen = lambda t: t.split("<h2>Generated outputs</h2>")[1].split("<h2>Model approvals</h2>")[0]  # noqa: E731
    try:
        assert client(R).get("/admin/audit").status_code == 403
        r = client([ADMIN]).get("/admin/audit")
        assert r.status_code == 200 and f'href="/review/{cid}"' in r.text
        t = client([ADMIN]).get(f"/admin/audit?user={a}").text
        assert gen(t).count("<tr>") == 51 and "older &raquo;" in t and "&laquo; newer" not in t
        assert f"p=2&amp;user={a}" in t
        p2 = client([ADMIN]).get(f"/admin/audit?p=2&user={a}").text
        assert gen(p2).count("<tr>") == 6 and "&laquo; newer" in p2 and "older &raquo;" not in p2
        only = client([ADMIN]).get(f"/admin/audit?user={b}").text
        assert gen(only).count("<tr>") == 4 and b in only and a not in only
    finally:
        with db.connect() as c:
            c.execute("delete from generations where user_id = any(%s)", ([a, b],))


def test_review_page_lists_approvals_expiring_within_30_days(approved):
    soon, later, expired = approved(due="10 days"), approved(due="90 days"), approved(due="-1 day")
    hidden = approved(due="10 days", acl=("g-other",))
    t = client(R).get("/review").text
    head, _, tail = t.partition("Due for re-review within 30 days")
    assert f'/review/{soon}"' in tail and f'/review/{later}"' not in t.split("Due for re-review")[1]
    assert f'/review/{expired}"' in head and f'/review/{hidden}"' not in t


# --- recall ---------------------------------------------------------------------------------------------

def load_recall():
    spec = importlib.util.spec_from_file_location("recall", ROOT / "scripts" / "recall.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recall_script_scores_hits_and_misses(approved, tmp_path, capsys):
    recall = load_recall()
    for name, case in (("Core banking onboarding", data(title="Core banking onboarding", industry="Retail banking")),
                       ("Claims automation", data(title="Claims automation", industry="Insurance"))):
        cid = approved(case)  # the document identifiers are what recall.yaml names
        with db.connect() as c:
            c.execute("update documents set title=%s, external_id=%s where id=(select document_id from cases where id=%s)",
                      (name, f"id-{uuid.uuid4().hex[:6]}", cid))
    f = tmp_path / "q.yaml"
    f.write_text("""
queries:
  - {id: hit-title, bid_text: "automate customer onboarding", expected: ["Core banking onboarding"]}
  - {id: hit-filtered, bid_text: "onboarding", filters: {industry: banking}, expected: ["nope", "Core banking onboarding"]}
  - {id: sample-skipped, sample: true, bid_text: "x", expected: ["whatever"]}
""")
    args = ["--file", str(f), "--groups", DOCS]
    assert recall.main(args) == 0
    out = capsys.readouterr().out
    assert "queries: 2  top-3: 100%  top-20: 100%" in out and "1 sample entry skipped" in out
    assert recall.main(args + ["--samples"]) == 1  # the sample misses: 2 of 3 = 67% < 90%
    out = capsys.readouterr().out
    assert "top-3: 67%" in out and "MISS sample-skipped" in out and "not in top 20" in out
    assert recall.main(args + ["--samples", "--min", "0.6"]) == 0
    assert recall.main(["--file", str(f), "--groups", "g-nobody"]) == 1  # ACL: nothing visible, nothing found
    only_samples = tmp_path / "s.yaml"
    only_samples.write_text("queries:\n  - {id: s, sample: true, bid_text: x, expected: [y]}\n")
    assert recall.main(["--file", str(only_samples), "--groups", DOCS]) == 2


def test_shipped_recall_file_parses_and_is_all_samples():
    import yaml
    qs = yaml.safe_load((ROOT / "tests" / "recall.yaml").read_text())["queries"]
    assert len(qs) == 3 and all(q["sample"] and q["bid_text"] and q["expected"] for q in qs)


# --- no document text in logs or output ---------------------------------------------------------------

SECRET_TEXT = "ZYGOMORPHIC-7731 reconciliation runs nightly across the ledger"


def test_document_text_never_reaches_logs_or_stdout(approved, monkeypatch, caplog, capsys, tmp_path):
    import boto3
    from moto import mock_aws
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "orig")
    monkeypatch.setenv("EXTRACT_MODEL", "bedrock/m")
    monkeypatch.setenv("DRAFT_MODEL", "bedrock/m")
    monkeypatch.setattr(ing, "to_markdown", lambda data, name: data.decode())

    def completion(**kw):  # the real llm.complete_json runs; only the network call is replaced
        schema = kw["response_format"]["json_schema"]["name"]
        body = {"Triage": '{"kind": "case", "describes_delivered_work": true}',
                "Extraction": json.dumps({"items": [
                    {"field": "title", "value": "Ledger reconciliation", "quote": SECRET_TEXT}],
                    "summary": "Nightly reconciliation."}),
                "Picks": '{"picks": []}'}[schema]
        from types import SimpleNamespace as N
        return N(choices=[N(finish_reason="stop", message=N(content=body))])
    monkeypatch.setattr(llm.litellm, "completion", completion)
    caplog.set_level(logging.DEBUG)
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="orig")
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,acl_groups) values ('s3',%s,%s) returning id", (uuid.uuid4().hex, [DOCS])).fetchone()[0]
        try:
            assert ing.ingest(sid, "k/doc.docx", "doc.docx", SECRET_TEXT.encode()) == "new"
            with db.connect() as c:
                did = c.execute("select id from documents where source_id=%s", (sid,)).fetchone()[0]
            with db.connect() as c:  # ingest queued an extract job; add one that fails, so a status line is printed
                c.execute("insert into jobs(kind, payload) values ('extract', jsonb_build_object('document_id', 999999999::bigint))")
            while worker.run_one():
                pass
            with db.connect() as c:
                c.execute("update cases set status='approved', review_due=now() + interval '60 days' where document_id=%s", (did,))
            user = User("u1", "U", frozenset([DOCS]), frozenset(["user"]))
            found = sr.search(user, "ledger reconciliation nightly", {})
            assert found
            sr.pick("ledger", found, [])
            r = client(R + ["g-user"]).post("/generate", data={"case_ids": [found[0]["id"]], "format": "md"})
            assert r.status_code == 200
        finally:
            with db.connect() as c:
                c.execute("delete from jobs where kind='extract' and (payload->>'document_id')::bigint = 999999999")
                c.execute("delete from sources where id=%s", (sid,))
    out = capsys.readouterr()
    assert SECRET_TEXT not in caplog.text and "ZYGOMORPHIC" not in caplog.text
    assert "ZYGOMORPHIC" not in out.out + out.err
    assert "job" in out.out  # the worker's status line was captured, so the check could have failed
    assert litellm.turn_off_message_logging is True
    assert "LITELLM_LOG=WARNING" in (ROOT / "Dockerfile").read_text()


def test_print_calls_are_the_known_content_free_ones():
    allowed = {  # file -> the only f-string/status lines that may print
        "app/main.py": ['print(f"login refused for {claims[\'sub\']}: groups overage", flush=True)',
                        'print(f"login sub={claims[\'sub\']!r} name={request.session[\'user\'][\'name\']!r}", flush=True)'],  # #47: finds a sub
        "app/enqueue_crawls.py": ['print(f"queued {queued} crawl job(s), {retried} extract retry(ies)", flush=True)'],
        "app/worker.py": ['print(f"job {job_id} {kind} {status}: {type(e).__name__}", flush=True)'],
        "app/revoke_sessions.py": ['print("usage: python -m app.revoke_sessions <sub> [<sub> ...]", file=sys.stderr)',
                                   'print(f"revoked sessions of {sub}", flush=True)'],
        "app/migrate.py": ['print("schema applied", flush=True)'],
    }
    found = {}
    for path in (ROOT / "app").rglob("*.py"):
        for line in path.read_text().splitlines():
            if re.search(r"\bprint\(", line):
                found.setdefault(str(path.relative_to(ROOT)), []).append(line.strip())
    assert found == allowed


def test_every_response_refuses_framing(env):
    """Clickjacking: no page may be framed, refusals and static files included (#146)."""
    rs = [client().get("/healthz"), client().get("/static/portal.css"),
          client().get("/review", headers={"Accept": "text/html"}, follow_redirects=False),
          client([REV]).get("/review"),
          client([REV], origin="http://evil.example").post("/review/1/approve")]
    assert [r.status_code for r in rs] == [200, 200, 303, 200, 403]
    for r in rs:
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
