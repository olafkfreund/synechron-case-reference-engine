import pytest

from app import brief, db
from tests.test_auth import env  # noqa: F401
from tests.test_research import reg  # noqa: F401

Q = ["Entra supports passwordless sign in out of the box for 3 tenants", "Conditional access needs a policy configured first"]


def body(**kw):
    return {"title": "Passwordless sign in", "takeaways": ["Entra supports passwordless sign in [1]."],
            "themes": [{"heading": "Setup", "text": "A policy is needed first [2]."}], **kw}


def reasons(b):
    return [(f["where"], f["reason"]) for f in brief.check(b, Q)]


def test_a_clean_brief_has_no_flags():
    assert brief.check(body(), Q) == []
    assert brief.check(body(takeaways=["Entra supports passwordless sign in. [1]", "It works for 3 tenants [1]."]), Q) == []


def test_uncited_unknown_and_unsupported_sentences_are_flagged():
    assert ("takeaway 1", "has no citation") in reasons(body(takeaways=["Entra supports passwordless sign in."]))
    assert ("takeaway 1", "cites unknown quote [9]") in reasons(body(takeaways=["Entra supports passwordless sign in [9]."]))
    assert ("takeaway 1", "number 5 is in no quote") in reasons(body(takeaways=["It works for 5 tenants [1]."]))
    assert any("Okta" in r for _, r in reasons(body(takeaways=["Entra beats Okta here [1]."])))
    assert ("takeaway 1", "number 3 is in no quote") in reasons(body(takeaways=["It works for 3 tenants [2]."]))  # the CITED quote counts
    assert any(w == "title" for w, _ in reasons(body(title="Passwordless sign in with Okta")))


def test_a_price_blocks(env):
    assert any("price" in r for _, r in reasons(body(takeaways=["It costs $500 per user [1]."])))


def test_run_sends_only_the_quotes_and_stores_flags(env, reg, monkeypatch):
    db.init()
    seen = {}

    def fake(alias, system, user, cls, *, data_class):
        seen.update(alias=alias, user=user, data_class=data_class)
        return cls(title="passwordless sign in", takeaways=["Entra supports passwordless sign in [1]."], themes=[])
    monkeypatch.setattr(brief, "complete_json", fake)
    quotes = [{"quote": f"{reg} runs Entra passwordless sign in out of the box"}, {"quote": "second quote here"}]
    with db.connect() as c:
        bid = c.execute("insert into briefs(created_by, title, quotes) values ('u1','t',%s) returning id",
                        (__import__("json").dumps(quotes),)).fetchone()[0]
    brief.run(bid)
    with db.connect() as c:
        status, b = c.execute("select status, body from briefs where id=%s", (bid,)).fetchone()
        c.execute("delete from briefs where id=%s", (bid,))
    assert (seen["alias"], seen["data_class"]) == ("DRAFT_MODEL", "public")
    assert reg not in seen["user"] and seen["user"].startswith("<quotes>\n[1] a retailer") and "[2] second quote here" in seen["user"]
    assert status == "done" and b["flags"] == [] and b["title"] == "passwordless sign in"


def test_a_model_failure_is_recorded_as_the_class_only(env, monkeypatch):
    db.init()
    monkeypatch.setattr(brief, "complete_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret quote text")))
    with db.connect() as c:
        bid = c.execute("insert into briefs(created_by, title, quotes) values ('u1','t','[]') returning id").fetchone()[0]
    brief.run(bid)
    with db.connect() as c:
        row = c.execute("select status, error from briefs where id=%s", (bid,)).fetchone()
        c.execute("delete from briefs where id=%s", (bid,))
    assert row == ("failed", "RuntimeError")


# ---- routes ----

import json  # noqa: E402

from tests.test_research import as_user  # noqa: E402
from tests.test_research_claims import BASE, finish, sent  # noqa: E402

QUOTES = [{"quote": Q[0], "publisher": "v.example", "url": "https://v.example/0", "type": "vendor_claim"},
          {"quote": Q[1], "publisher": "w.example", "url": "https://w.example/1", "type": "vendor_claim"}]


@pytest.fixture
def made(env):  # noqa: F811
    db.init()
    ids = []

    def add(data=None, status="done", by="u1"):
        with db.connect() as c:
            ids.append(c.execute("insert into briefs(created_by, title, quotes, body, status) values (%s,'t',%s,%s,%s) returning id",
                                 (by, json.dumps(QUOTES), json.dumps(data or {**body(), "flags": []}), status)).fetchone()[0])
        return ids[-1]
    yield add
    with db.connect() as c:
        c.execute("delete from briefs where id = any(%s)", (ids,))
        c.execute("delete from jobs where kind='brief'")



def test_start_copies_only_the_picked_quotes_and_queues_the_job(reg, made):
    c = as_user("u1")
    loc = sent(c, None)
    finish(loc, [{**BASE, "quote": f"{q['quote']}", "url": q["url"], "publisher": q["publisher"]} for q in QUOTES])
    rid = loc.rsplit("/", 1)[1]
    r = c.post(f"/research/{rid}/brief", data={"n": [1, 1, 9], "title": f"{reg} best practice"}, follow_redirects=False)
    assert r.status_code == 303
    bid = int(r.headers["location"].rsplit("/", 1)[1])
    with db.connect() as conn:
        title, quotes, status = conn.execute("select title, quotes, status from briefs where id=%s", (bid,)).fetchone()
        assert conn.execute("select count(*) from jobs where kind='brief' and payload->>'brief_id'=%s", (str(bid),)).fetchone()[0] == 1
        conn.execute("delete from briefs where id=%s", (bid,))
        conn.execute("delete from jobs where kind='brief'")
    assert [q["quote"] for q in quotes] == [Q[1]] and status == "queued" and reg not in title
    assert c.post(f"/research/{rid}/brief", data={"title": "x"}).status_code == 400            # nothing picked
    assert as_user("u2").post(f"/research/{rid}/brief", data={"n": [0]}).status_code == 404   # not their research
    assert c.post(f"/research/{rid}/brief", data={"n": list(range(31))}).status_code == 400    # too many


def test_only_the_owner_sees_edits_or_downloads_a_brief(made):
    bid = made()
    assert as_user("u1").get(f"/briefs/{bid}").status_code == 200
    other = as_user("u2")
    assert other.get(f"/briefs/{bid}").status_code == 404
    assert other.post(f"/briefs/{bid}", data={"title": "x"}).status_code == 404
    assert other.post(f"/briefs/{bid}/download", data={"format": "md"}).status_code == 404


def test_everything_rendered_is_escaped(made):
    evil = "<script>alert(1)</script>"
    bid = made({"title": evil, "takeaways": [evil], "themes": [{"heading": evil, "text": evil}],
                "flags": [{"where": evil, "sentence": evil, "reason": evil}]})
    t = as_user("u1").get(f"/briefs/{bid}").text
    assert "<script>alert" not in t and "&lt;script&gt;" in t


def test_an_edit_reruns_the_checks_and_a_flag_blocks_the_download(made):
    bid = made()
    c = as_user("u1")
    bad = {"title": "Passwordless sign in", "takeaways": "Entra supports passwordless sign in.", "heading": ["Setup"], "text": ["A policy is needed first [2]."]}
    assert c.post(f"/briefs/{bid}", data=bad, follow_redirects=False).status_code == 303
    with db.connect() as conn:
        flags = conn.execute("select body->'flags' from briefs where id=%s", (bid,)).fetchone()[0]
    assert [f["reason"] for f in flags] == ["has no citation"] and "has no citation" in c.get(f"/briefs/{bid}").text
    assert c.post(f"/briefs/{bid}/download", data={"format": "md"}).status_code == 409
    c.post(f"/briefs/{bid}", data={**bad, "takeaways": "Entra supports passwordless sign in [1]."})
    with db.connect() as conn:
        assert conn.execute("select body->'flags' from briefs where id=%s", (bid,)).fetchone()[0] == []
    assert c.post(f"/briefs/{bid}/download", data={"format": "md"}).status_code not in (404, 409)


def test_a_queued_brief_cannot_be_edited_or_downloaded(made):
    bid = made({}, status="queued")
    c = as_user("u1")
    assert c.post(f"/briefs/{bid}", data={"title": "x"}).status_code == 409
    assert c.post(f"/briefs/{bid}/download", data={"format": "md"}).status_code == 409
    assert "refresh" in c.get(f"/briefs/{bid}").text
