import json
import uuid

import pytest

from app import anonymise, db
from tests.test_auth import ADMIN, env  # noqa: F401
from tests.test_research import as_user, reg  # noqa: F401
from tests.test_research_claims import BASE, finish, sent
from tests.test_review import make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

TAG = f"qzx{uuid.uuid4().hex[:8]}"  # a distinctive word: library text must never reach case search
Q1 = f"Entra supports passwordless sign in out of the box {TAG}"
Q2 = "Conditional access needs a policy configured first"


@pytest.fixture
def lib(env):  # noqa: F811
    db.init()
    yield
    with db.connect() as c:
        c.execute("delete from library_items where created_by like 'u%%'")
        c.execute("delete from research where created_by like 'u%%'")
        c.execute("delete from briefs where created_by like 'u%%'")
        c.execute("delete from jobs where kind in ('research','brief')")


def research_with(c, *claims, pages=()):
    loc = sent(c, None)
    finish(loc, [{**BASE, "quote": q, "url": f"https://v.example/{i}", "publisher": "v.example", **extra}
                 for i, (q, extra) in enumerate(claims)], pages=pages)
    return loc.rsplit("/", 1)[1]


def save(c, rid, n=(0,), topic="Passwordless sign in"):
    r = c.post("/library/save", data={"research_id": rid, "n": list(n), "topic": topic}, follow_redirects=False)
    return r, int(r.headers["location"].rsplit("/", 1)[1]) if r.status_code == 303 else None


def test_save_copies_the_picked_quotes_scrubs_the_topic_and_every_user_sees_it(reg, lib):
    c = as_user("u1")
    rid = research_with(c, (Q1, {}), (Q2, {}))
    r, lid = save(c, rid, [1, 1, 9], topic=f"{reg} passwordless")
    assert r.status_code == 303
    with db.connect() as conn:
        topic, kind, body, query = conn.execute("select topic, kind, body, query from library_items where id=%s", (lid,)).fetchone()
    assert reg not in topic and kind == "quotes" and [q["quote"] for q in body["quotes"]] == [Q2] and query
    other = as_user("u2")
    assert "passwordless" in other.get("/library").text and Q2 in other.get(f"/library/{lid}").text
    assert TAG not in other.get(f"/library/{lid}").text  # the unpicked quote was not copied
    assert other.get("/library?q=conditional").text.count(f"/library/{lid}") == 1


def test_a_blocked_topic_quote_url_or_brief_is_refused(reg, lib, monkeypatch):
    c = as_user("u1")
    assert save(c, research_with(c, (Q1, {})), topic=reg)[0].status_code == 400                       # nothing left after the scrub
    for extra in ({"publisher": f"{reg}.example"}, {"url": f"https://v.example/{reg}"}):
        rid = research_with(c, (Q1, extra), (Q2, {}))               # one blocked, one clean: the strict branch is what refuses
        assert save(c, rid, [0, 1])[0].status_code == 400
        assert save(c, rid, [1])[0].status_code == 303               # the clean one alone is fine
    assert save(c, research_with(c, (Q1, {})), n=[])[0].status_code == 400                          # nothing picked
    assert c.post("/library/save", data={"topic": "x"}).status_code == 400                           # neither source
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','t',%s,%s,'done') returning id", (
            json.dumps([{"quote": Q2, "publisher": "v.example", "url": "https://v.example/0"}]),
            json.dumps({"title": "Access", "takeaways": [f"{reg} needs a policy [1]."], "themes": [], "flags": []}))).fetchone()[0]
    monkeypatch.setattr(anonymise, "apply", lambda t, c: t)  # the name would otherwise be replaced; blocked() must still refuse
    assert c.post("/library/save", data={"brief_id": bid, "topic": "Access"}).status_code == 400
    with db.connect() as conn:  # a name right after a line break must not hide in a joined string
        conn.execute("update briefs set body=%s where id=%s", (json.dumps({"title": "A", "takeaways": [f"Fine.\n{reg} needs a policy [1]."], "themes": [], "flags": []}), bid))
    assert c.post("/library/save", data={"brief_id": bid, "topic": "Access"}).status_code == 400
    with db.connect() as conn:
        conn.execute("update briefs set body=%s where id=%s", (json.dumps({"title": "A", "takeaways": ["Fine [1]."], "themes": [], "flags": [{"where": "x"}]}), bid))
    assert c.post("/library/save", data={"brief_id": bid, "topic": "Access"}).status_code == 409    # a flagged brief
    assert as_user("u2").post("/library/save", data={"brief_id": bid, "topic": "Access"}).status_code == 404


def test_age_and_the_recheck_badge(reg, lib):
    c = as_user("u1")
    _, lid = save(c, research_with(c, (Q2, {})))
    badge = '<span class="badge">re-check</span>'
    assert badge not in c.get("/library").text and "0 day(s) old" in c.get("/library").text
    with db.connect() as conn:
        conn.execute("update library_items set checked_at = now() - interval '366 days', created_at = now() - interval '400 days' where id=%s", (lid,))
    t = c.get("/library").text
    assert badge in t and "400 day(s) old" in t and badge in c.get(f"/library/{lid}").text
    c.post(f"/library/{lid}/checked")
    assert badge not in c.get("/library").text and badge not in c.get(f"/library/{lid}").text


def test_library_text_never_enters_case_search(reg, lib, approved):
    approved()
    c = as_user("u1")
    save(c, research_with(c, (Q1, {})))
    with db.connect() as conn:
        assert conn.execute("select count(*) from cases where search_text ilike %s or data::text ilike %s", (f"%{TAG}%", f"%{TAG}%")).fetchone()[0] == 0


def test_search_is_literal_visible_and_does_not_confirm_a_protected_name(reg, lib):
    c = as_user("u1")
    save(c, research_with(c, (Q2, {})), topic="access policy")
    assert "access policy" in c.get("/library?q=access").text and "access policy" in c.get("/library?q=policy+configured").text
    assert "access policy" not in c.get("/library?q=%25").text and "access policy" not in c.get("/library?q=_ccess").text
    assert "access policy" not in c.get(f"/library?q={reg}").text


def test_search_ignores_the_recheck_map_and_text_that_is_hidden_now(reg, lib):
    c = as_user("u1")
    _, lid = save(c, research_with(c, (Q2, {})), topic="access policy")
    c.post(f"/library/{lid}/recheck")  # body.rechecks now holds {"u1": <research id>}
    assert f"/library/{lid}" not in c.get("/library?q=u1").text and f"/library/{lid}" not in c.get("/library?q=rechecks").text
    assert f"/library/{lid}" in c.get("/library?q=conditional+access").text           # a normal word still finds it
    word = f"Wibble{uuid.uuid4().hex[:6]}"
    _, hid = save(c, research_with(c, (f"{word}Corp runs this in production daily", {})), topic="production notes")
    assert f"/library/{hid}" in c.get(f"/library?q={word}").text
    with db.connect() as conn:  # the client is protected after the save: apply() now replaces the name
        cid = conn.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (f"{word}Corp",)).fetchone()[0]
    try:
        assert f"/library/{hid}" not in c.get(f"/library?q={word}").text            # a part of the name finds nothing
        assert f"/library/{hid}" in c.get("/library?q=production").text
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))


def test_a_client_protected_after_saving_is_withheld_on_view_and_download(reg, lib, monkeypatch):
    c = as_user("u1")
    word = f"Wibble{uuid.uuid4().hex[:6]}"
    rid = research_with(c, (f"{word} runs this in production daily", {}), (Q2, {}))
    _, lid = save(c, rid, [0, 1], topic=f"About {word.lower()}x")
    with db.connect() as conn:
        cid = conn.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (word,)).fetchone()[0]
    try:
        monkeypatch.setattr(anonymise, "apply", lambda t, c: t)  # apply() misses: blocked() alone must still guard
        t = as_user("u2").get(f"/library/{lid}").text
        assert word not in t and "1 quote(s) not shown" in t and Q2 in t
        r = as_user("u2").post(f"/library/{lid}/export", data={"format": "csv"})
        assert word not in r.text and Q2 in r.text
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))


def test_a_brief_item_is_re_anonymised_on_view_and_download(reg, lib):
    c = as_user("u1")
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','t',%s,%s,'done') returning id", (
            json.dumps([{"quote": Q2, "publisher": "v.example", "url": "https://v.example/0", "title": "Docs"}]),
            json.dumps({"title": "Access policy", "takeaways": ["A policy is needed first [1]."], "themes": [], "flags": []}))).fetchone()[0]
    r = c.post("/library/save", data={"brief_id": bid, "topic": "Access"}, follow_redirects=False)
    lid = int(r.headers["location"].rsplit("/", 1)[1])
    v = as_user("u2").get(f"/library/{lid}").text
    assert "A policy is needed first [1]." in v and "not Synechron delivery evidence" in v
    d = as_user("u2").post(f"/library/{lid}/download", data={"format": "md"})
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"] and "A policy is needed first" in d.text
    assert as_user("u2").post(f"/library/{lid}/export", data={"format": "md"}).status_code == 400
    with db.connect() as conn:
        assert conn.execute("select count(*) from generations where user_id='u2' and format like 'library-%%'").fetchone()[0] >= 1


def test_only_the_creator_or_an_admin_deletes(reg, lib):
    c = as_user("u1")
    _, lid = save(c, research_with(c, (Q2, {})))
    assert as_user("u2").post(f"/library/{lid}/delete").status_code == 404
    assert as_user("u2").get(f"/library/{lid}").status_code == 200
    assert as_user("u3", [ADMIN]).post(f"/library/{lid}/delete", follow_redirects=False).status_code == 303
    assert c.get(f"/library/{lid}").status_code == 404
    _, again = save(c, research_with(c, (Q2, {})))
    assert c.post(f"/library/{again}/delete", follow_redirects=False).status_code == 303


def test_recheck_reports_found_and_missing_quotes_and_changes_nothing(reg, lib):
    c = as_user("u1")
    rid = research_with(c, (Q1, {}), (Q2, {}))
    _, lid = save(c, rid, [0, 1])
    other = c
    assert as_user("u2").post(f"/library/{lid}/recheck", follow_redirects=False).status_code == 404
    assert as_user("u2").post(f"/library/{lid}/checked").status_code == 404
    with db.connect() as conn:
        assert conn.execute("select count(*) from research where created_by='u2'").fetchone()[0] == 0
    assert "Search again" not in as_user("u2").get(f"/library/{lid}").text and "Mark re-checked" not in as_user("u2").get(f"/library/{lid}").text
    assert as_user("u3", [ADMIN]).post(f"/library/{lid}/checked", follow_redirects=False).status_code == 303
    assert other.post(f"/library/{lid}/recheck", follow_redirects=False).status_code == 303
    t = other.get(f"/library/{lid}").text
    assert "Running the search again" in t
    with db.connect() as conn:
        again = int(conn.execute("select body->'rechecks'->>'u1' from library_items where id=%s", (lid,)).fetchone()[0])
        assert conn.execute("select count(*) from jobs where kind='research' and payload->>'research_id'=%s", (str(again),)).fetchone()[0] == 1
        conn.execute("update research set status='done', results=%s where id=%s", (json.dumps({"pages": [
            {"url": "https://v.example/0", "markdown": f"Page text. {Q1.upper()} and more."},  # found, in any case
            {"url": "https://v.example/1", "markdown": "The page changed."}], "claims": []}), again))
        before = conn.execute("select body->'quotes', checked_at from library_items where id=%s", (lid,)).fetchone()
    t = other.get(f"/library/{lid}").text
    assert "1 of 2 saved quote(s) still appear" in t and "still on the page" in t and "not found again" in t
    assert as_user("u3", [ADMIN]).get(f"/library/{lid}").text.count("still on the page") == 0  # an admin has not run it
    with db.connect() as conn:
        assert conn.execute("select body->'quotes', checked_at from library_items where id=%s", (lid,)).fetchone() == before


def test_recheck_of_a_brief_reports_its_sources(reg, lib):
    c = as_user("u1")
    rid = research_with(c, (Q1, {}))
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(research_id, created_by, title, quotes, body, status) values (%s,'u1','t',%s,%s,'done') returning id", (
            rid, json.dumps([{"quote": Q1, "publisher": "v.example", "url": "https://v.example/0"},
                             {"quote": Q2, "publisher": "w.example", "url": "https://w.example/1"}]),
            json.dumps({"title": "Entra", "takeaways": ["Entra supports passwordless sign in [1][2]."], "themes": [], "flags": []}))).fetchone()[0]
    lid = int(c.post("/library/save", data={"brief_id": bid, "topic": "Entra"}, follow_redirects=False).headers["location"].rsplit("/", 1)[1])
    c.post(f"/library/{lid}/recheck")
    with db.connect() as conn:
        again = int(conn.execute("select body->'rechecks'->>'u1' from library_items where id=%s", (lid,)).fetchone()[0])
        conn.execute("update research set status='done', results=%s where id=%s", (json.dumps({"pages": [
            {"url": "https://v.example/0", "markdown": Q1}, {"url": "https://w.example/1", "markdown": "changed"}], "claims": []}), again))
    t = c.get(f"/library/{lid}").text
    assert "1 of 2 saved quote(s) still appear" in t and t.count("still on the page") == 1 and t.count("not found again") == 1


def protect_later(word):
    with db.connect() as conn:
        return conn.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (word,)).fetchone()[0]


def test_list_withholds_a_topic_naming_a_client_protected_later(reg, lib, monkeypatch):
    c = as_user("u1")
    word = f"wibble{uuid.uuid4().hex[:6]}"
    _, lid = save(c, research_with(c, (Q2, {})), topic=f"About {word}")
    assert f"about {word}" in c.get("/library").text
    cid = protect_later(word)
    try:
        monkeypatch.setattr(anonymise, "apply", lambda t, c: t)  # apply() misses: blocked() alone must still guard
        t = as_user("u2").get("/library").text
        assert word not in t and "[withheld]" in t and f"/library/{lid}" in t
        assert word not in as_user("u2").get(f"/library/{lid}").text
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))


def test_a_brief_item_protected_after_saving_is_withheld(reg, lib, monkeypatch):
    c = as_user("u1")
    word = f"Wibble{uuid.uuid4().hex[:6]}"
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','t',%s,%s,'done') returning id", (
            json.dumps([{"quote": Q2, "publisher": "v.example", "url": "https://v.example/0"}]),
            json.dumps({"title": "Access policy", "takeaways": [f"{word} needs a policy first [1].", "A policy is needed first [1]."],
                        "themes": [], "flags": []}))).fetchone()[0]
    r = c.post("/library/save", data={"brief_id": bid, "topic": "Access"}, follow_redirects=False)
    assert r.status_code == 303
    lid = int(r.headers["location"].rsplit("/", 1)[1])
    cid = protect_later(word)
    try:
        monkeypatch.setattr(anonymise, "apply", lambda t, c: t)
        v = as_user("u2").get(f"/library/{lid}")
        assert word not in v.text and "1 statement omitted" in v.text and "A policy is needed first [1]." in v.text
        d = as_user("u2").post(f"/library/{lid}/download", data={"format": "md"})
        assert d.status_code == 200 and word not in d.text and "1 statement omitted" in d.text
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))
