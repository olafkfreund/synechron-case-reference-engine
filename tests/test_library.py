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
    assert badge not in c.get("/library").text and "today" in c.get("/library").text
    with db.connect() as conn:
        conn.execute("update library_items set checked_at = now() - interval '366 days', created_at = now() - interval '400 days' where id=%s", (lid,))
    t = c.get("/library").text
    assert badge in t and "400 days old" in t and badge in c.get(f"/library/{lid}").text
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


def test_a_markdown_quote_shows_as_plain_text_in_the_library(reg, lib):
    c = as_user("u1")
    rid = research_with(c, ("Entra **supports** [passwordless](https://x.io/a) sign in out of the box", {}))
    _, lid = save(c, rid)
    v = as_user("u2").get(f"/library/{lid}").text
    assert "Entra supports passwordless sign in out of the box" in v and "](" not in v and "**" not in v


# --- #235: suggestions, browse by topic, saved-by ---

def put(topic, quote, kind="quotes", by="u1", name="Ann", **extra):
    body = {"quotes": [{"quote": quote, "publisher": "v.example", "url": "https://v.example/0"}]}
    if kind == "brief":
        body["brief"] = {"title": topic, "takeaways": [f"{quote} [1]."], "themes": []}
    with db.connect() as conn:
        return conn.execute("insert into library_items(topic, kind, query, body, created_by, created_by_name) values (%s,%s,'q',%s,%s,%s) returning id",
                            (topic, kind, json.dumps(body), by, name)).fetchone()[0]


def rword(prefix):  # letters only: the keyword rule drops digits
    import random
    import string
    return prefix + "".join(random.choices(string.ascii_lowercase, k=8))


def test_suggest_needs_two_words_ignores_the_stoplist_and_caps_at_five(reg, lib, monkeypatch):
    import app.library as L
    calls = []
    monkeypatch.setattr("app.llm.complete_json", lambda *a, **k: calls.append(a))
    w1, w2, w3 = rword("zyxa"), rword("zyxb"), rword("zyxz")
    both = put("t", f"{w1} and {w2} together")
    one = put("t", f"{w1} alone")
    stop = put("t", "which would within these things")
    got = [i["id"] for i in L.suggest(f"we need {w1} and {w2} and {w3} which would within", {}, [])]
    assert both in got and one not in got and stop not in got      # whole words only count; stoplist words never match
    assert [i["id"] for i in L.suggest(w1, {}, [])].count(one) == 1  # one keyword is enough when there are 2 or fewer
    for _ in range(7):
        put("t", f"{w1} {w2} more")
    assert len(L.suggest(f"{w1} {w2}", {}, [])) == L.SUGGEST_MAX
    assert not calls                                               # no model was called
    sub = put("t", f"{w1}x {w2}x")                                 # a longer word does not contain the keyword
    assert sub not in [i["id"] for i in L.suggest(f"{w1} {w2} {w3}", {}, [])]


def test_suggest_never_sees_text_hidden_after_saving(reg, lib):
    import app.library as L
    word = rword("Wibble")
    w2 = rword("zyxc")
    bid = f"{word.lower()} {w2} {rword('zyxq')}"  # three keywords, so two must match
    lid = put("t", f"{word} and {w2} run it")
    assert lid in [i["id"] for i in L.suggest(bid, {}, [])]
    with db.connect() as conn:
        cid = conn.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (word,)).fetchone()[0]
        clients = anonymise.load_clients(conn)
    try:
        assert lid not in [i["id"] for i in L.suggest(bid, {}, clients)]
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))


def test_search_post_shows_the_card_the_get_does_not_and_the_bid_stays_out_of_logs(reg, lib, caplog, monkeypatch):
    import logging
    caplog.set_level(logging.DEBUG)
    w1, w2 = rword("zyxd"), rword("zyxe")
    lid = put(f"{w1} topic", f"{w2} is here")
    c = as_user("u1")
    r = c.post("/search", data={"bid_text": f"{w1} {w2}"}, follow_redirects=False)
    assert r.status_code == 200 and "From the library" in r.text and f"/library/{lid}" in r.text
    assert w1 not in caplog.text and w2 not in caplog.text            # the bid text is in no log line
    calls = []
    monkeypatch.setattr("app.library.suggest", lambda *a, **k: calls.append(a) or [])
    assert "From the library" not in c.get("/search").text and not calls  # browsing never calls suggest()


def test_chips_filters_and_saved_by(reg, lib):
    a = put("Cloud Migration", "alpha one", name="Ann")
    b = put("cloud migration", "beta two", kind="brief", name="")
    other = put("Kafka", "gamma three")
    with db.connect() as conn:
        conn.execute("update library_items set checked_at = now() - interval '400 days' where id=%s", (other,))
    c = as_user("u1")
    t = c.get("/library").text
    assert "cloud migration (2)" in t and "Kafka (1)" in t and "saved by Ann" in t and "saved by a colleague" in t
    t = c.get("/library?topic=cloud+migration").text
    assert f"/library/{a}" in t and f"/library/{b}" in t and f"/library/{other}" not in t
    assert f"/library/{b}" not in c.get("/library?kind=quotes").text
    t = c.get("/library?due=1").text
    assert f"/library/{other}" in t and f"/library/{a}" not in t
    assert "saved by Ann" in c.get(f"/library/{a}").text and "saved by a colleague" in c.get(f"/library/{b}").text
    assert f"/library/{a}" in c.get("/library?kind=nonsense").text   # an unknown kind means all
    word = rword("Wibble")
    hid = put(f"{word} platform", "delta four")
    with db.connect() as conn:
        cid = conn.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (word,)).fetchone()[0]
    try:
        t = c.get("/library").text
        assert word not in t and "[withheld] (" not in t and f"/library/{hid}" in t
    finally:
        with db.connect() as conn:
            conn.execute("delete from clients where id=%s", (cid,))


def test_suggest_is_fast_on_300_items(reg, lib):
    import random
    import time

    import app.library as L
    rnd = random.Random(7)
    pool = [rword("w")[:6] + "xyz" for _ in range(3000)]  # a vocabulary of 3000 words
    for i in range(300):
        put(f"topic {i}", " ".join(rnd.choices(pool, k=60)), kind="brief" if i % 2 else "quotes")
    bid = " ".join(rnd.choices(pool, k=150))
    t0 = time.perf_counter()
    L.suggest(bid, {"industry": "Banking"}, [])
    took = time.perf_counter() - t0
    print(f"suggest() with 300 saved items: {took * 1000:.0f} ms")
    assert took < 0.5  # the 150-candidate cap measured about 63 ms; this only catches a collapse


def test_a_topic_chip_link_can_be_followed(reg, lib):
    import html
    import re
    a = put("Cloud Migration", "alpha one")
    other = put("Kafka", "gamma three")
    c = as_user("u1")
    hrefs = [html.unescape(h) for h in re.findall(r'<a class="badge[^"]*" href="(/library\?[^"]*)"', c.get("/library").text)]
    chip = next(h for h in hrefs if "Cloud" in h)
    r = c.get(chip)
    assert r.status_code == 200 and f"/library/{a}" in r.text and f"/library/{other}" not in r.text


def test_a_library_brief_keeps_its_diagram(lib):
    from tests.test_brief import QUOTES, body
    d = {"components": [{"id": "c1", "name": "Entra", "role": "x", "zone": "core", "kind": "service", "cite": "source 1"}]}
    with db.connect() as c:
        bid = c.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','t',%s,%s,'done') returning id",
                        (json.dumps(QUOTES), json.dumps({**body(), "flags": [], "diagram": d}))).fetchone()[0]
    assert as_user("u1").post("/library/save", data={"brief_id": bid}, follow_redirects=False).status_code == 303
    with db.connect() as c:
        assert c.execute("select body->'brief'->'diagram'->'components'->0->>'name' from library_items where created_by='u1'").fetchone()[0] == "Entra"
