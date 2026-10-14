"""MCP phase 2 tools: output links, research, library and briefs (#249). Nothing calls a model or Brave."""
import json
import uuid

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app import brief as br, db, main, mcp_server, render
from tests.test_mcp_http import ORIGIN, env  # noqa: F401
from tests.test_mcp_tools import GROUPS, PREFACE_START, Session, parse  # noqa: F401
from tests.test_mcp_http import rpc
from tests.test_research import U, as_user, reg  # noqa: F401
from tests.test_research_claims import BASE, finish, sent
from tests.test_review import make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

NEW = {"generate_output", "start_research", "get_research", "save_library_item", "list_library", "get_library_item",
       "create_brief", "get_brief", "download_brief", "download_library_item"}
Q = "Entra supports passwordless sign in out of the box"


@pytest.fixture
def mcp(env):  # noqa: F811
    db.init()
    with TestClient(main.create_app()) as c:
        yield lambda **kw: Session(c, **{"groups": U, **kw})


def user_id():
    return f"u{uuid.uuid4().hex[:8]}"  # the reg fixture cleans rows of users named u%


def done_research(who, *quotes, **extra):
    loc = sent(as_user(who), None)
    finish(loc, [{**BASE, "quote": q, "url": f"https://v.example/{i}", "publisher": "v.example", **extra} for i, q in enumerate(quotes)])
    return int(loc.rsplit("/", 1)[1])


def count(sql, *a):
    with db.connect() as c:
        return c.execute(sql, a).fetchone()[0]


def test_new_tools_are_listed_with_schemas_and_hints(mcp, approved, reg):  # noqa: F811
    who = user_id()
    s = mcp(who=who)
    tools = {t["name"]: t for t in parse(rpc(s.c, "tools/list", "2025-06-18", tok=s.tok))["tools"]}
    assert NEW <= set(tools)
    for n in NEW:
        assert tools[n]["outputSchema"]
    for n in NEW - {"start_research", "create_brief", "save_library_item"}:
        assert tools[n]["annotations"]["readOnlyHint"], n
    for n in ("start_research", "create_brief"):
        a = tools[n]["annotations"]
        assert not a["readOnlyHint"] and not a["idempotentHint"] and a["openWorldHint"], n
    cid, rid = approved(), done_research(who, Q)
    s = mcp(who=who, groups=GROUPS)
    calls = [("generate_output", dict(case_ids=[cid], format="md")), ("start_research", dict(question="how does kyc work")),
             ("get_research", dict(research_id=rid)), ("save_library_item", dict(research_id=rid, n=[0], topic="Sign in")),
             ("list_library", {}), ("create_brief", dict(research_id=rid, n=[0]))]
    for name, args in calls:
        res = s.call(name, **args)
        assert not res.get("isError"), (name, res)
        jsonschema.validate(res["structuredContent"], tools[name]["outputSchema"])
        assert res["content"][0]["text"].startswith(PREFACE_START)
    lid = s.out("list_library")["items"][0]["id"]
    bid = count("select max(id) from briefs where created_by=%s", who)
    for name, args in (("get_library_item", dict(library_id=lid)), ("get_brief", dict(brief_id=bid))):
        res = s.call(name, **args)
        jsonschema.validate(res["structuredContent"], tools[name]["outputSchema"])


def test_start_research_matches_the_portal_preview_and_queues_one_job(mcp, reg):  # noqa: F811
    who = user_id()
    question = f"how did {reg} do it"
    portal = as_user(who).post("/research", data={"question": question})
    out = mcp(who=who).out("start_research", question=question)
    assert out["query"] in portal.text and reg.lower() not in out["query"].lower() and out["status"] == "queued"
    assert count("select count(*) from jobs where kind='research' and payload->>'research_id' = %s", str(out["research_id"])) == 1
    assert count("select count(*) from research where created_by=%s", who) == 1


def test_get_research_is_private_and_drops_protected_claims(mcp, reg):  # noqa: F811
    a, b = user_id(), user_id()
    rid = done_research(a, Q, "Another quote that is long enough", publisher=f"{reg}.example")
    with db.connect() as c:  # the first claim is clean, the second names the client in its publisher
        c.execute("update research set results = jsonb_set(results, '{claims,0,publisher}', '\"v.example\"') where id=%s", (rid,))
    mine = mcp(who=a).out("get_research", research_id=rid)
    assert mine["status"] == "done" and [c["quote"] for c in mine["claims"]] == [Q] and mine["omitted"] == 1
    assert reg not in json.dumps(mine) and mine["claims"][0]["url"] == "https://v.example/0"
    s = mcp(who=b)
    assert s.call("get_research", research_id=rid)["isError"]
    assert s.rows()[-1][1] == "not_found"


def test_save_and_create_brief_give_the_portal_rows(mcp, reg):  # noqa: F811
    who = user_id()
    rid = done_research(who, Q, "Conditional access needs a policy configured first")
    s, portal = mcp(who=who), as_user(who)
    lid = s.out("save_library_item", research_id=rid, n=[1, 0], topic="Sign in")["library_id"]
    r = portal.post("/library/save", data={"research_id": rid, "n": [1, 0], "topic": "Sign in"}, follow_redirects=False)
    other = int(r.headers["location"].rsplit("/", 1)[1])
    row = "select topic, kind, query, body, created_by from library_items where id=%s"
    with db.connect() as c:
        assert c.execute(row, (lid,)).fetchone() == c.execute(row, (other,)).fetchone()
    bid = s.out("create_brief", research_id=rid, n=[0, 1], title="Brief")["brief_id"]
    portal.post(f"/research/{rid}/brief", data={"n": [0, 1], "title": "Brief"})
    with db.connect() as c:
        rows = c.execute("select title, quotes, status, created_by from briefs where research_id=%s order by id", (rid,)).fetchall()
    assert len(rows) == 2 and rows[0] == rows[1] and rows[0][2] == "queued"
    assert count("select count(*) from jobs where kind='brief' and payload->>'brief_id' = %s", str(bid)) == 1
    # a brief that is not done cannot be saved or downloaded; a finished one can, as the owner only
    assert s.call("download_brief", brief_id=bid, format="md")["isError"]
    assert s.call("save_library_item", brief_id=bid)["isError"] and s.call("save_library_item")["isError"]
    body = {"title": "T", "takeaways": ["Passwordless works [1]."], "themes": [{"heading": "H", "text": "It works [1]."}], "flags": []}
    with db.connect() as c:
        c.execute("update briefs set status='done', body=%s where id=%s", (json.dumps(body), bid))
    got = s.out("get_brief", brief_id=bid)
    assert got["status"] == "done" and got["brief"]["themes"][0]["text"] == "It works [1]." and not got["flagged"]
    assert mcp(who=user_id()).call("get_brief", brief_id=bid)["isError"]
    lib = s.out("save_library_item", brief_id=bid, topic="A brief")["library_id"]
    item = s.out("get_library_item", library_id=lib)
    assert item["kind"] == "brief" and item["brief"]["title"] == "T"
    assert [i["id"] for i in s.out("list_library", q="brief")["items"]][:1] == [lib]
    for url in (s.out("download_brief", brief_id=bid, format="md")["download_url"],
                s.out("download_library_item", library_id=lib, format="docx")["download_url"]):
        assert url.startswith(f"{ORIGIN}/mcp/download/")
    assert s.call("download_library_item", library_id=lib, format="csv")["isError"]


def test_a_withheld_brief_gives_the_portal_message(mcp, reg, monkeypatch):  # noqa: F811
    who = user_id()
    rid = done_research(who, Q)
    s = mcp(who=who)
    bid = s.out("create_brief", research_id=rid, n=[0])["brief_id"]
    with db.connect() as c:
        c.execute("update briefs set status='done', body=%s where id=%s", (json.dumps({"title": "T", "themes": [], "takeaways": []}), bid))
    monkeypatch.setattr(render, "brief_section", lambda *a: (_ for _ in ()).throw(render.Withheld(render.WITHHELD)))
    res = s.call("get_brief", brief_id=bid)
    assert res["isError"] and res["content"][0]["text"] == render.WITHHELD and s.rows()[-1][1] == "denied"


@pytest.mark.parametrize("tool,args", [("start_research", dict(question="kyc automation")), ("create_brief", dict(research_id=0, n=[0]))])
def test_daily_caps(mcp, reg, tool, args):  # noqa: F811
    who = user_id()
    with db.connect() as c:
        c.execute("insert into tool_calls(user_key, client_id, tool, object_ids, outcome, ms) "
                  "select %s, 'client-1', %s, '{}', 'ok', 1 from generate_series(1, 9)", (who, tool))
        c.execute("insert into tool_calls(user_key, client_id, tool, object_ids, outcome, ms, created_at) "
                  "values (%s, 'client-1', %s, '{}', 'ok', 1, now() - interval '25 hours')", (who, tool))  # outside the window
    if tool == "create_brief":
        args = dict(args, research_id=done_research(who, Q))
    s = mcp(who=who)
    assert not s.call(tool, **args).get("isError")  # the 10th
    res = s.call(tool, **args)
    assert res["isError"] and res["content"][0]["text"] == f"daily limit reached for {tool}"
    other = mcp(who=user_id()).call("start_research", question="kyc") if tool == "start_research" else {}
    assert not other.get("isError")  # the cap is per user


def test_nothing_leaks_through_the_new_tools(mcp, reg, make):  # noqa: F811
    marker = f"DOC-TEXT-{uuid.uuid4().hex}"
    case = data(title=f"{reg} onboarding")
    cid = make(status="approved", due="30 days", data=case.model_dump_json(), text=marker)
    with db.connect() as c:
        c.execute("update cases set search_text=%s, summary=%s where id=%s", (case.search_text(), case.summary, cid))
        c.execute("update documents set web_url='https://stored.example/WEB-URL', folder_url='https://stored.example/FOLDER-URL' "
                  "where id=(select document_id from cases where id=%s)", (cid,))
    who = user_id()
    rid = done_research(who, Q)
    s = mcp(who=who, groups=GROUPS)
    secret = f"SECRET-QUESTION-{uuid.uuid4().hex}"
    bid_text = f"BID-TEXT-{uuid.uuid4().hex} onboarding"
    lid = s.out("save_library_item", research_id=rid, n=[0], topic="Sign in")["library_id"]
    outs = [s.call("search_references", bid_text=bid_text),
            s.call("generate_output", case_ids=[cid], format="md", research_id=rid, library_ids=[lid]),
            s.call("generate_output", case_ids=[cid], format="md"), s.call("start_research", question=secret),
            s.call("get_research", research_id=rid), s.call("create_brief", research_id=rid, n=[0]),
            s.call("save_library_item", research_id=rid, n=[0], topic="x"), s.call("list_library")]
    blob = json.dumps(outs)
    assert reg not in blob and marker not in blob and "stored.example" not in blob and secret not in blob and bid_text not in blob
    assert not outs[1].get("isError"), outs[1]
    url = outs[1]["structuredContent"]["download_url"]  # generate_output
    with db.connect() as c:
        rows = repr(c.execute("select * from tool_calls where user_key=%s", (who,)).fetchall())
    assert secret not in rows and bid_text not in rows and url.rsplit("/", 1)[1] not in rows
