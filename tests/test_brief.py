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
