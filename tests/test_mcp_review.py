"""The reviewer gate and the allow-listed view (#249 phase 4)."""
import asyncio
import json
import uuid

import pytest
from pydantic import BaseModel

from app import db, main, mcp_server as ms
from app.mcp_server import AccessToken
from app.review import load
from tests.test_mcp_http import env  # noqa: F401
from tests.test_review import DOC, DOCS, make  # noqa: F401

CLIENT = "client-1"


class Out(BaseModel):
    ok: bool = True


@pytest.fixture
def gate(env, monkeypatch):  # noqa: F811
    db.init()
    made = []

    def call(roles=("user", "reviewer"), scp="refs.access refs.review", approval="live"):
        who = f"t-{uuid.uuid4()}"
        user = main.User(who, who, frozenset([DOCS]), frozenset(roles))
        claims = dict(scp=scp, azp=CLIENT, oid=who, _user=user)
        monkeypatch.setattr(ms, "get_access_token", lambda: AccessToken(token="t", client_id=CLIENT, scopes=scp.split(), claims=claims))
        with db.connect() as c:
            c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))
            if approval:
                c.execute("insert into model_approvals(model, data_class, approved_by, expires_at, revoked_by) values "
                          "(%s, 'confidential', 'admin', now() + %s::interval, %s)",
                          (f"mcp-client:{CLIENT}", "1 day" if approval == "live" else "-1 day", "admin" if approval == "revoked" else None))
        ran = []
        res = asyncio.run(ms._run(lambda u: (ran.append(1), (Out(), []))[1], "gate_probe", review=True))
        with db.connect() as c:
            rows = c.execute("select tool, outcome from tool_calls where user_key = %s", (who,)).fetchall()
        return res, rows, ran

    yield call
    with db.connect() as c:
        c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))


def test_the_gate_passes_with_all_three(gate):
    res, rows, ran = gate()
    assert not res.is_error and rows == [("gate_probe", "ok")] and ran


@pytest.mark.parametrize("kw, text", [
    (dict(roles=("user",)), "reviewer role required"),
    (dict(scp="refs.access"), "refs.review scope"),
    (dict(approval=None), "not approved to handle confidential data"),
    (dict(approval="expired"), "not approved to handle confidential data"),
    (dict(approval="revoked"), "not approved to handle confidential data"),
])
def test_each_gate_refusal_is_audited_once_and_the_tool_never_runs(gate, kw, text):
    res, rows, ran = gate(**kw)
    assert res.is_error and text in res.content[0].text
    assert rows == [("gate_probe", "denied")] and not ran


def test_view_has_only_the_allow_list(make):  # noqa: F811
    cid = make(text=DOC + " See https://example.com/secret and external key k.")
    from app.schema import ReferenceCase
    user = main.User("r", "r", frozenset([DOCS]), frozenset({"user", "reviewer"}))
    with db.connect() as c:
        case, _ = load(c, cid, user, c.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()[0])
    view = ms.review_view(case, cid, "extracted", "Source doc", "vv")
    assert set(view) == {"id", "status", "basis", "document", "v", "fields", "prose", "notes", "completeness"}
    blob = json.dumps(view)
    assert "example.com" not in blob and "external key" not in blob and "http" not in blob
    f = {x["path"]: x for x in view["fields"]}
    assert f["title"]["state"] == "sourced" and f["industry"]["state"] == "unsourced" and f["summary"]["values"]["value"]
    assert view["notes"] == ["check the summary"] and view["prose"]["challenge"]["by_reviewer"] is True
    assert not any(x["path"].endswith(".new") for x in view["fields"])


# ---- the tools, over HTTP

import re  # noqa: E402

import jsonschema  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.test_auth import client as portal_client  # noqa: E402
from tests.test_mcp_http import rpc, token  # noqa: E402
from tests.test_mcp_tools import parse  # noqa: E402
from tests.test_review import Q_TITLE, REV, case_data, ver  # noqa: E402
from app.schema import Sourced  # noqa: E402

TECH = [Sourced[str](value="Kafka", source_quote=Q_TITLE), Sourced[str](value="Spark", source_quote=Q_TITLE)]


class Rev:
    def __init__(self, c, scp="refs.access refs.review", groups=("g-rev", DOCS)):
        self.c, self.who = c, f"t-{uuid.uuid4()}"
        self.tok = token(oid=self.who, groups=list(groups), scp=scp)

    def call(self, name, **args):
        return parse(rpc(self.c, "tools/call", "2025-06-18", {"name": name, "arguments": args}, tok=self.tok))

    def out(self, name, **args):
        res = self.call(name, **args)
        assert not res.get("isError"), res
        return res["structuredContent"]

    def audit(self):
        with db.connect() as conn:
            return conn.execute("select tool, outcome from tool_calls where user_key = %s order by id", (self.who,)).fetchall()


@pytest.fixture
def rev(env):  # noqa: F811
    from fastapi.testclient import TestClient as TC
    db.init()
    with db.connect() as c:
        c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))
        c.execute("insert into model_approvals(model, data_class, approved_by, expires_at) values (%s, 'confidential', 'admin', now() + interval '1 day')",
                  (f"mcp-client:{CLIENT}",))
    with TC(main.create_app()) as c:
        yield lambda **kw: Rev(c, **kw)
    with db.connect() as c:
        c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))


def schemas(r):
    tools = parse(rpc(r.c, "tools/list", "2025-06-18", tok=r.tok))["tools"]
    return {t["name"]: t["outputSchema"] for t in tools if t.get("outputSchema")}


def test_schemas_and_the_queue_matches_the_portal(rev, make):  # noqa: F811
    a, b = make(), make(data=case_data(tech_stack=TECH))
    r = rev()
    sch = schemas(r)
    q = r.out("list_review_queue")
    jsonschema.validate(q, sch["list_review_queue"])
    portal = portal_client([REV, DOCS]).get("/review").text
    ids = list(dict.fromkeys(int(i) for i in re.findall(r'href="/review/(\d+)"', portal)))
    assert [x["id"] for x in q["rows"]] == ids[:20] and {a, b} <= set(ids)
    got = r.out("get_review_case", case_id=b)
    jsonschema.validate(got, sch["get_review_case"])
    assert got["v"] == ver(b) and got["status"] == "extracted"
    assert [r.out("edit_case", case_id=a, v=ver(a), field="summary", value="Edited.")["v"]] == [ver(a)]
    jsonschema.validate(r.out("edit_case", case_id=b, v=ver(b), field="summary", value="Again."), sch["edit_case"])
    assert [t for t, _ in r.audit()] == ["list_review_queue", "get_review_case", "edit_case", "edit_case"]


def test_the_view_leaks_nothing(rev, make):  # noqa: F811
    cid = make(text=DOC + " Secret-text https://leak.example/x", data=case_data(tech_stack=TECH))
    other = make(data=case_data(title=Sourced[str](value="Other-case-title", source_quote=Q_TITLE)))
    with db.connect() as c:
        c.execute("update documents set external_id = 'EXT-KEY-1' where id = (select document_id from cases where id = %s)", (cid,))
        c.execute("insert into jobs(kind, payload) values ('noop', '{}')")
        c.execute("insert into uploads(user_key, user_name, source_id, s3_key, bytes, job_id) select 'uk', 'uploader-zed', d.source_id, 'EXT-KEY-1', 1, (select max(id) from jobs) "
                  "from cases c join documents d on d.id = c.document_id where c.id = %s", (cid,))
        c.execute("update cases set public_sources = %s::jsonb where id = %s", (json.dumps([{"url": "https://pub.example/p", "statement": "pub-claim"}]), cid))
    raw = json.dumps(rev().out("get_review_case", case_id=cid))
    for needle in ("Secret-text", "leak.example", "EXT-KEY-1", "uploader-zed", "pub.example", "pub-claim", "Other-case-title", "http"):
        assert needle not in raw, needle
    with db.connect() as c:
        c.execute("delete from uploads where user_key = 'uk'")
        c.execute("delete from jobs where kind = 'noop'")


def test_edits(rev, make):  # noqa: F811
    r = rev()
    cid = make(text=DOC + " We run Redpanda and Flink.", data=case_data(tech_stack=TECH))
    # a list save, then a list remove, each with the version the last one returned
    v = r.out("edit_case", case_id=cid, v=ver(cid), field="tech_stack.0", value="Redpanda", quote="We run Redpanda and Flink.")
    assert v["state"] == "sourced" and v["v"] == ver(cid)
    v = r.out("edit_case", case_id=cid, v=v["v"], field="tech_stack.new", value="Flink", quote="a passage the document does not hold")
    assert v["state"] == "unsourced"  # a quote the document does not hold
    v = r.out("edit_case", case_id=cid, v=v["v"], field="tech_stack.2", action="remove")
    assert v["state"] is None
    with db.connect() as c:
        assert [t["value"] for t in c.execute("select data->'tech_stack' from cases where id=%s", (cid,)).fetchone()[0]] == ["Redpanda", "Spark"]
    # a prose edit returns the checker's flags
    p = r.out("edit_case", case_id=cid, v=v["v"], field="prose.challenge", value="They paid $5 million for it.")
    assert p["flags"]
    # a stale version is a 409, and the case is untouched
    before = ver(cid)
    res = r.call("edit_case", case_id=cid, v="stale", field="summary", value="X.")
    assert res["isError"] and res["content"][0]["text"] == "case changed; fetch it again with get_review_case and retry with the new v" and ver(cid) == before
    # a case this reviewer cannot see is a 404
    hidden = make(acl=("g-elsewhere",))
    res = r.call("get_review_case", case_id=hidden)
    assert res["isError"] and "no such case" in res["content"][0]["text"]
    res = r.call("edit_case", case_id=hidden, v=ver(hidden), field="summary", value="X.")
    assert res["isError"] and "no such case" in res["content"][0]["text"]
    assert [o for _, o in r.audit()].count("not_found") == 2 and "denied" in [o for _, o in r.audit()]


def test_edit_withdraws_an_approval(rev, make):  # noqa: F811
    cid = make("approved", "10 days")
    rev().out("edit_case", case_id=cid, v=ver(cid), field="summary", value="Edited.")
    with db.connect() as c:
        assert c.execute("select status from cases where id=%s", (cid,)).fetchone()[0] == "extracted"


def test_a_missing_scope_blocks_every_tool(rev, make):  # noqa: F811
    cid = make()
    r = rev(scp="refs.access")
    for name, args in (("list_review_queue", {}), ("get_review_case", dict(case_id=cid)),
                       ("edit_case", dict(case_id=cid, v=ver(cid), field="summary", value="X."))):
        assert r.call(name, **args)["isError"]
    assert [o for _, o in r.audit()] == ["denied"] * 3
