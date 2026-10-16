import uuid

import pytest

from app import anonymise, db, search as sr
from app.main import User
from app.schema import Outcome, ReferenceCase, Sourced
from tests.test_auth import USER, client, env  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401

ME = User("u1", "U", frozenset([DOCS]), frozenset(["user"]))
S = lambda v, q="a long enough source quote": Sourced[str](value=v, source_quote=q)  # noqa: E731


def data(title="Faster onboarding", industry="Banking", region="United Kingdom", tech=("Kubernetes",), **kw):
    return ReferenceCase(
        title=S(title), industry=S(industry), region=S(region), tech_stack=[S(t) for t in tech],
        outcomes=[Outcome(metric="onboarding", value="12 to 3 days", source_quote="q")],
        summary="Onboarding fell from 12 days to 3 days.", **kw)


@pytest.fixture
def approved(make):  # noqa: F811
    def approved(case=None, status="approved", due="30 days", **kw):
        case = case or data()
        cid = make(status=status, due=due, data=case.model_dump_json(), **kw)
        with db.connect() as c:
            c.execute("update cases set search_text=%s, summary=%s where id=%s",
                      (case.search_text(), case.summary, cid))
        return cid
    return approved


@pytest.fixture
def reg(env):  # noqa: F811
    db.init()
    tag = uuid.uuid4().hex[:6]
    made = []

    def add(name, label, referenceable=False, suffix=""):
        with db.connect() as c:
            made.append(c.execute(
                "insert into clients(name, anonymised_label, referenceable) values (%s,%s,%s) returning id",
                (f"{name}{tag}{suffix}", label, referenceable)).fetchone()[0])
        return f"{name}{tag}{suffix}", made[-1]
    yield add
    with db.connect() as c:
        c.execute("update cases set client_id = null where client_id = any(%s)", (made,))
        c.execute("delete from clients where id = any(%s)", (made,))


def link(cid, client_id):
    with db.connect() as c:
        c.execute("update cases set client_id=%s where id=%s", (client_id, cid))


def ids(user=ME, text="onboarding", **filters):
    return [c["id"] for c in sr.search(user, text, filters)]


def test_or_semantics_long_bid_text(approved):
    cid = approved()
    bid = ("We need a partner to migrate our customer onboarding journey to cloud with microservices, "
           "strong security, regulator-ready audit trails, a fixed budget and a nine month timeline")
    assert ids(text=bid) == [cid]


def test_hidden_cases(approved):
    visible = approved()
    approved(acl=("g-other",))
    approved(due="-1 day")
    approved(status="extracted", due=None)
    approved(status="rejected", due=None)
    approved(deleted=True)
    assert ids() == [visible]


def test_filters(approved):
    uk = approved(data(industry="Retail banking", region="United Kingdom", tech=("Kubernetes", "Kafka")))
    de = approved(data(industry="Insurance", region="Germany", tech=("Terraform",)))
    assert set(ids()) == {uk, de}
    assert ids(industry="BANKING") == [uk]
    assert ids(region="germ") == [de]
    assert ids(tech="kafka") == [uk]
    assert ids(industry="banking", region="germany") == []
    assert ids(text="", industry="insur") == [de]  # filters alone, no text


def picks_reply(monkeypatch, *picks):
    monkeypatch.setattr(sr, "complete_json", lambda *a, **k: sr.Picks(picks=list(picks)))


def test_pick_valid_and_invented_number(approved, monkeypatch):
    a, b = approved(), approved(data(title="Other onboarding"))
    picks_reply(monkeypatch,
                sr.Pick(case_id=a, reason="fits", tailored="Cut onboarding from 12 to 3 days."),
                sr.Pick(case_id=b, reason="fits", tailored="Cut onboarding from 12 days to 1 day."))
    picks, notes = sr.pick("bid", sr.search(ME, "onboarding", {}), [])
    got = {p["id"]: p for p in picks}
    assert got[a]["tailored"] == "Cut onboarding from 12 to 3 days."
    assert got[b]["tailored"] == "Onboarding fell from 12 days to 3 days."  # summary fallback
    assert len(notes) == 1 and str(b) not in notes[0]  # no internal ids on a user page


def test_empty_metric_outcome_is_value_only(approved, monkeypatch):
    a = approved(data().model_copy(update={"outcomes": [Outcome(metric="", value="cut by half", source_quote="q")]}))
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="fits", tailored=""))
    top, _, _ = sr.results(ME, "onboarding", {})
    assert top[0]["outcomes"] == ["cut by half"]


def test_summary_numbers_do_not_license_tailored_numbers(approved, monkeypatch):
    cid = approved(data().model_copy(update={"summary": "Saved 99 hours."}))
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="r", tailored="We saved 99 hours."))
    picks, notes = sr.pick("bid", sr.search(ME, "onboarding", {}), [])
    assert picks[0]["tailored"] == "Saved 99 hours." and notes  # the summary, not the LLM text
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="r", tailored="Saved 99 hours in 2 weeks."))
    assert sr.pick("bid", sr.search(ME, "onboarding", {}), [])[1]


def test_comma_decimal_fact_no_longer_licenses_15(approved, monkeypatch):
    cid = approved(data().model_copy(update={"outcomes": [Outcome(metric="saved", value="1,5 days", source_quote="q")]}))
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="r", tailored="Saved 15 days."))
    picks, notes = sr.pick("bid", sr.search(ME, "onboarding", {}), [])
    assert picks[0]["tailored"] == "Onboarding fell from 12 days to 3 days." and notes  # summary fallback


def test_unknown_case_id_dropped(approved, monkeypatch):
    a = approved()
    picks_reply(monkeypatch, sr.Pick(case_id=999999, reason="x", tailored="x"),
                sr.Pick(case_id=a, reason="ok", tailored="Fine."))
    assert [p["id"] for p in sr.pick("bid", sr.search(ME, "onboarding", {}), [])[0]] == [a]


def test_client_name_removed_from_llm_text(approved, monkeypatch):
    a = approved()
    reg = [dict(name="Zorp", aliases=[], anonymised_label="a retailer", referenceable=False)]
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="Zorp wanted this", tailored="Zörp's onboarding, by ZORP."))
    p = sr.pick("bid", sr.search(ME, "onboarding", {}), reg)[0][0]
    assert p["reason"] == "a retailer wanted this"
    assert "orp" not in p["tailored"]  # #166: every spelling is replaced


def test_llm_failure_uses_rank_order(approved, monkeypatch):
    best = approved(data(title="Onboarding onboarding onboarding"))
    other = approved(data(title="Something else", industry="Banking"))
    with db.connect() as c:
        c.execute("update cases set search_text = search_text || ' onboarding' where id=%s", (other,))

    def boom(*a, **k):
        raise RuntimeError("secret")
    monkeypatch.setattr(sr, "complete_json", boom)
    picks, notes = sr.pick("onboarding", sr.search(ME, "onboarding", {}), [])
    assert [p["id"] for p in picks][0] == best and all(p["reason"] == "" for p in picks)
    assert notes == ["AI picks unavailable; showing the best text matches"]


def set_basis(cid, basis):
    with db.connect() as c:
        c.execute("update cases set basis=%s where id=%s", (basis, cid))


def test_engagement_ranks_after_delivered_on_tie_and_is_badged(approved, monkeypatch):
    eng = approved()  # lower id, identical text: would win the tie on id alone
    dlv = approved()
    set_basis(eng, "engagement")
    got = sr.search(ME, "onboarding", {})
    assert [c["id"] for c in got] == [dlv, eng] and [c["basis"] for c in got] == ["delivered", "engagement"]
    seen = []
    monkeypatch.setattr(sr, "complete_json", lambda m, s, user, *a, **k: seen.append((s, user)) or sr.Picks(picks=[]))
    sr.pick("onboarding", got, [])
    assert f"case_id {eng} (basis: engagement)" in seen[0][1] and f"case_id {dlv} (basis: delivered)" in seen[0][1]
    assert "never describe them as delivered" in seen[0][0]
    monkeypatch.setattr(sr, "complete_json", lambda *a, **k: sr.Picks(picks=[sr.Pick(case_id=dlv, reason="", tailored="x")]))
    r = client([USER, DOCS]).post("/search", data={"bid_text": "onboarding"})
    assert "Delivered case" in r.text and "Engagement (contracted scope)" in r.text


def test_engagement_outcomes_not_shown_or_given_to_model(approved, monkeypatch):  # #139
    eng = approved()
    set_basis(eng, "engagement")
    seen = []

    def fake(m, s, user, *a, **k):
        seen.append(user)
        return sr.Picks(picks=[sr.Pick(case_id=eng, reason="", tailored="x")])
    monkeypatch.setattr(sr, "complete_json", fake)
    top, others, _ = sr.results(ME, "onboarding", {})
    assert [t["id"] for t in top] == [eng] and top[0]["outcomes"] == []
    assert "12 to 3 days" not in seen[0]


def test_page_for_plain_user_hides_quotes_and_orgs(approved, monkeypatch):
    c1 = approved(data(organisations=["Secret Org Ltd"]).model_copy(
        update={"title": S("Faster onboarding", "Hidden source quote text")}))
    approved(data(title="Second onboarding"))
    picks_reply(monkeypatch, sr.Pick(case_id=c1, reason="fits well", tailored="Cut onboarding to 3 days."))
    r = client([USER, DOCS]).post("/search", data={"bid_text": "onboarding", "region": "kingdom"})
    assert r.status_code == 200
    assert "fits well" in r.text and "Cut onboarding to 3 days." in r.text and "Other matches" in r.text
    assert "Hidden source quote text" not in r.text and "Secret Org" not in r.text


def test_search_requires_login(env):  # noqa: F811
    assert client().get("/search").status_code == 401
    r = client().get("/search", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_invented_number_in_reason_is_dropped(approved, monkeypatch):
    a = approved()
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="Cut costs by 40% for 2 million users", tailored="Faster onboarding."))
    p = sr.pick("bid", sr.search(ME, "onboarding", {}), [])[0][0]
    assert p["reason"] == ""


def test_all_picks_dropped_is_not_silent(approved, monkeypatch):
    approved()
    picks_reply(monkeypatch, sr.Pick(case_id=999999999, reason="r", tailored="t"))
    picks, notes = sr.pick("bid", sr.search(ME, "onboarding", {}), [])
    assert picks and "did not match" in notes[0] and "999999999" not in " ".join(notes)


def test_stop_words_with_filter_still_finds(approved):
    cid = approved()
    assert ids(text="the and of", region="kingdom") == [cid]


def test_search_form_is_post_so_bid_text_stays_out_of_urls(env):  # noqa: F811
    page = client([USER]).get("/search").text
    assert 'method="post"' in page


def test_client_display_rules(approved, reg, monkeypatch):
    ref_name, ref = reg("Globex", "a manufacturer", True)
    anon_name, anon = reg("Initech", "a software firm")
    a, b, c = approved(), approved(data(title="Second onboarding")), approved(data(title="Third onboarding"))
    link(a, ref)
    link(b, anon)
    want = {a: ref_name, b: "a software firm", c: "a client"}
    got = {x["id"]: x["label"] for x in sr.search(ME, "onboarding", {})}
    assert {k: got[k] for k in want} == want
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="fits", tailored="Cut onboarding from 12 to 3 days."))
    top, others, _ = sr.results(ME, "onboarding", {})
    shown = {x["id"]: x["label"] for x in top + others}
    assert {k: shown[k] for k in want} == want
    assert anon_name not in "".join(shown.values())


def test_referenceable_name_containing_protected_name_shows_its_label(approved, reg, monkeypatch):
    p, _ = reg("Zorp", "a retailer")
    n, ref = reg("Zorp", "a logistics firm", True, suffix=" Logistics")
    a = approved(data(title=f"Faster onboarding for {n}"))
    link(a, ref)
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="fits", tailored="Cut onboarding from 12 to 3 days."))
    top, others, _ = sr.results(ME, "onboarding", {})
    x = next(x for x in top + others if x["id"] == a)
    assert x["label"] == "a logistics firm"
    assert x["title"] == "Faster onboarding for a logistics firm"
    assert p not in x["title"] + x["label"]


def test_pick_leaves_out_a_candidate_with_a_name_apply_missed(monkeypatch):  # #165
    monkeypatch.setattr(sr.anonymise, "apply", lambda t, c: t)  # since #166 apply() catches this spelling; force a miss
    reg = [dict(name="Zorp Bank", aliases=[], anonymised_label="a bank", referenceable=False)]
    leak = data().model_copy(update={"summary": "Ｚｏｒｐ Bank onboarding fell from 12 days to 3 days."})
    cands = [dict(id=1, basis="delivered", case=leak), dict(id=2, basis="delivered", case=data())]
    seen = []
    monkeypatch.setattr(sr, "complete_json", lambda m, s, user, *a, **k: seen.append(user) or sr.Picks(picks=[]))
    sr.pick("onboarding", cands, reg)
    assert "case_id 2 " in seen[0] and "case_id 1 " not in seen[0] and "Ｚｏｒｐ" not in seen[0]


# browse (#215)
GUEST = [USER, DOCS]


def no_model(monkeypatch):
    """Returns the list of model calls; pick() swallows exceptions, so a raising stub could not fail a test."""
    calls = []
    monkeypatch.setattr(sr, "complete_json", lambda *a, **k: calls.append(1))
    return calls


def stamp(cid, days):
    with db.connect() as c:
        c.execute("update cases set approved_at = now() - make_interval(days => %s) where id=%s", (days, cid))


def test_get_lists_visible_cases_newest_first_without_a_model(approved, monkeypatch):
    calls = no_model(monkeypatch)
    old, mid, new = (approved(data(title=t)) for t in ("Old case", "Mid case", "New case"))
    for cid, d in ((old, 30), (mid, 20), (new, 10)):
        stamp(cid, d)
    approved(data(title="Hidden case"), acl=("g-other",))
    text = client(GUEST).get("/search").text
    assert text.index("New case") < text.index("Mid case") < text.index("Old case")
    assert not calls
    assert "Hidden case" not in text and "Approved " in text and "Kubernetes" in text


def test_paging(approved):
    for i in range(21):
        stamp(approved(data(title=f"Case {i:02d}")), 100 - i)  # Case 20 is the newest
    c = client(GUEST)
    p1, p2 = c.get("/search").text, c.get("/search?page=2").text
    assert p1.count('class="section-card stack result"') == 20 and "Case 00" not in p1
    assert 'Next</a>' in p1 and 'Previous</a>' not in p1 and "page=2" in p1
    assert p2.count('class="section-card stack result"') == 1 and "Case 00" in p2
    assert 'Previous</a>' in p2 and 'Next</a>' not in p2


def test_kind_and_sort(approved):
    d = approved(data(title="Zeta delivered"))
    e = approved(data(title="Alpha engagement"))
    set_basis(e, "engagement")
    stamp(d, 1)
    stamp(e, 10)  # newest and title order disagree
    c = client(GUEST)
    t = c.get("/search").text
    assert t.index("Zeta delivered") < t.index("Alpha engagement")
    t = c.get("/search?kind=engagement").text
    assert "Alpha engagement" in t and "Zeta delivered" not in t
    t = c.get("/search?sort=title").text
    assert t.index("Alpha engagement") < t.index("Zeta delivered")
    assert d != e


def test_facets_count_and_hide_protected(approved, reg):
    name, _ = reg("Zorp", "a retailer")
    approved(data(industry="Banking"))
    approved(data(industry="Banking"))
    approved(data(industry="Insurance"))
    approved(data(industry=f"Banking for {name}"))
    f = sr.facets(ME)
    assert ("Banking", 2) in f["industry"] and ("Insurance", 1) in f["industry"]
    assert [v for v, _ in f["industry"]] == ["Banking", "Insurance"]  # the value naming a client is not offered
    assert ("Kubernetes", 4) in f["tech"]
    assert name not in client(GUEST).get("/search").text


def test_post_with_only_filters_calls_no_model(approved, monkeypatch):
    calls = no_model(monkeypatch)
    approved(data(title="Banking case"))
    r = client(GUEST).post("/search", data={"bid_text": "  ", "industry": "bank"})
    assert r.status_code == 200 and "Banking case" in r.text and not calls


def test_off_list_filter_is_shown_selected_and_escaped(approved):
    approved()
    t = client(GUEST).get("/search?industry=<b>x").text
    assert '<option value="&lt;b&gt;x" selected>&lt;b&gt;x (filtered)</option>' in t
    assert "<b>x" not in t
    assert "No approved cases match these filters" in t


def test_bid_search_applies_kind(approved, monkeypatch):
    d = approved(data(title="Delivered onboarding"))
    e = approved(data(title="Engagement onboarding"))
    set_basis(e, "engagement")
    picks_reply(monkeypatch)
    top, others, _ = sr.results(ME, "onboarding", {}, "engagement")
    assert {x["id"] for x in top + others} == {e}
    assert {x["id"] for x in sr.results(ME, "onboarding", {})[0] + sr.results(ME, "onboarding", {})[1]} >= {d, e}
    r = client(GUEST).post("/search", data={"bid_text": "onboarding", "kind": "engagement"})
    assert "Engagement onboarding" in r.text and "Delivered onboarding" not in r.text


def test_rows_show_complete_or_partial_badge(approved, monkeypatch):
    from app.schema import Diagram, Period, Prose
    no_model(monkeypatch)
    approved(data(title="Thin case"))
    full = data(title="Full case", tech=("a", "b", "c"), capabilities=[S("x"), S("y"), S("z")], period=Period(start="2024"),
                diagrams=[Diagram(id=1, caption="c", status="ready")],
                prose={f: Prose(text=" ".join(["word"] * 80), by_reviewer=True)
                       for f in ("challenge", "solution", "implementation", "configuration")})
    approved(full)
    text = client(GUEST).get("/search").text
    assert text.count("Partial · 1/9") == 1 and text.count(">Complete<") == 1


def test_bid_results_top_and_others_carry_the_score(approved, monkeypatch):
    a, b = approved(), approved(data(title="Other onboarding"))
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="fits", tailored="Cut onboarding."))
    top, others, _ = sr.results(ME, "onboarding", {})
    assert [x["id"] for x in top] == [a] and b in [x["id"] for x in others]
    assert all(x["score"] == (1, 9) for x in top + others)
    text = client(GUEST).post("/search", data={"bid_text": "onboarding"}).text
    assert text.count("Partial · 1/9") >= 2


def test_placeholder_metric_outcome_is_value_only(approved, monkeypatch):
    a = approved(data().model_copy(update={"outcomes": [Outcome(metric="metric", value="cut by half", source_quote="q")]}))
    picks_reply(monkeypatch, sr.Pick(case_id=a, reason="fits", tailored=""))
    top, _, _ = sr.results(ME, "onboarding", {})
    assert top[0]["outcomes"] == ["cut by half"]


def test_search_card_has_case_study_buttons_for_one_case(approved, monkeypatch):
    a = approved(data())
    monkeypatch.setattr(sr, "complete_json", lambda *a_, **k: sr.Picks(picks=[sr.Pick(case_id=a, reason="", tailored="x")]))
    t = client([USER, DOCS]).post("/search", data={"bid_text": "onboarding"}).text
    assert 'value="case_study_docx"' in t and 'value="case_study_pdf"' in t


def test_download_all_row_has_no_case_study_buttons(approved, monkeypatch):
    a, b = approved(data()), approved(data(title="Other onboarding"))
    monkeypatch.setattr(sr, "complete_json", lambda *a_, **k: sr.Picks(picks=[sr.Pick(case_id=a, reason="", tailored="x"),
                                                                                sr.Pick(case_id=b, reason="", tailored="y")]))
    t = client([USER, DOCS]).post("/search", data={"bid_text": "onboarding"}).text
    all_row = t[t.index("Download all"):]
    assert 'value="case_study' not in all_row and t.count('value="case_study_docx"') == 2


REVIEW = User("r1", "R", frozenset([DOCS]), frozenset(["user", "reviewer"]))


def set_links(cid, web, folder, title="Quarterly report"):
    with db.connect() as c:
        c.execute("update documents set web_url=%s, folder_url=%s, title=%s where id=(select document_id from cases where id=%s)",
                  (web, folder, title, cid))


def entries(user, cid):
    return sr.source_docs([cid], user, anonymise.load_clients()).get(cid, [])


def test_source_docs_titles_follow_the_referenceable_rule(approved, reg):
    _, ref = reg("Globex", "a manufacturer", True)
    _, anon = reg("Initech", "a software firm")
    a, b, u = approved(), approved(data(title="Second onboarding")), approved(data(title="Third onboarding"))
    link(a, ref)
    link(b, anon)
    for cid in (a, b, u):
        set_links(cid, "https://h.example/a", "https://h.example/")
    assert [e["label"] for e in entries(ME, a)] == ["Quarterly report"]  # referenceable and linked
    for cid in (b, u):  # protected, or not linked to a client: count text, links kept
        (e,) = entries(ME, cid)
        assert e["label"].startswith("1 document in ") and [(d["web"], d["folder"]) for d in e["docs"]] == [(True, True)]
    assert [e["label"] for e in entries(REVIEW, b)] == ["Quarterly report"]


def test_source_docs_never_lists_withdrawn_or_invisible_documents(approved):
    gone, other = approved(deleted=True), approved(acl=("g-other",))
    ok = approved()
    assert sr.source_docs([gone, other, ok], ME, []).keys() == {ok}


def test_merged_case_lists_every_member(approved):
    a, b = approved(), approved()
    with db.connect() as c:
        m = c.execute("insert into cases(document_id, member_count, status) values (null, 2, 'approved') returning id").fetchone()[0]
        c.execute("update cases set merged_into=%s where id in (%s,%s)", (m, a, b))
    try:
        assert sum(len(e["docs"]) for e in entries(REVIEW, m)) == 2
    finally:
        with db.connect() as c:
            c.execute("update cases set merged_into=null where id in (%s,%s)", (a, b))
            c.execute("delete from cases where id=%s", (m,))


def card_html(approved, web, folder, monkeypatch):
    no_model(monkeypatch)
    cid = approved()
    set_links(cid, web, folder)
    return client(GUEST).get("/search").text


URL = 'https://h.example/Secret-Client/a"><script>x</script>'


def doc_of(cid):
    with db.connect() as c:
        return c.execute("select document_id from cases where id=%s", (cid,)).fetchone()[0]


def test_card_links_go_through_the_redirect_and_the_page_holds_no_url(approved, monkeypatch):
    no_model(monkeypatch)
    cid = approved()
    set_links(cid, URL, "https://h.example/Secret-Client/")
    t = client(GUEST).get("/search").text
    d = doc_of(cid)
    assert "Source documents · 1" in t and f'href="/go/{d}"' in t and f'href="/go/{d}/folder"' in t
    assert 'rel="noopener noreferrer"' in t and 'target="_blank"' in t
    assert "h.example" not in t and "Secret-Client" not in t and "<script>x" not in t  # the page never holds the URL
    assert "Quarterly report" not in t  # the title is hidden here


def test_source_documents_are_a_list_with_open_buttons_or_the_stored_line(approved, monkeypatch):
    both = card_html(approved, URL, "https://h.example/f/", monkeypatch)
    doc = both[both.index('<ul class="doc-list">'):both.index("</ul>", both.index('<ul class="doc-list">'))]
    assert doc.count("<li>") == 1 and ">Open</a>" in doc and ">Open folder</a>" in doc and "Stored in the engine" not in doc


def test_a_stored_only_document_says_so_in_the_list(approved, monkeypatch):
    stored = card_html(approved, None, None, monkeypatch)
    assert "Stored in the engine. Ask a reviewer for a copy." in stored[stored.index('<ul class="doc-list">'):] and ">Open</a>" not in stored


def go(d, folder="", user=GUEST):
    return client(user).get(f"/go/{d}{folder}", follow_redirects=False)


def test_go_redirects_a_visible_document_without_caching_or_referrer(approved):
    cid = approved()
    set_links(cid, URL, "https://h.example/f/")
    r = go(doc_of(cid))
    assert r.status_code == 303 and r.headers["location"].startswith("https://h.example/Secret-Client/a")
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert go(doc_of(cid), "/folder").headers["location"] == "https://h.example/f/"


def test_go_is_404_for_everything_else(approved):
    def linked(**kw):
        cid = approved(**kw)
        set_links(cid, "https://h.example/a", "https://h.example/f/")
        return doc_of(cid)
    assert go(linked(acl=("g-other",))).status_code == 404  # outside the user's groups
    assert go(linked(deleted=True)).status_code == 404  # withdrawn
    assert go(linked(status="extracted")).status_code == 404  # a plain user, case not approved
    nolink = approved()
    set_links(nolink, None, None)
    assert go(doc_of(nolink)).status_code == 404 and go(doc_of(nolink), "/folder").status_code == 404
    assert go(10**9).status_code == 404


def test_card_for_an_upload_says_stored_in_the_engine(approved, monkeypatch):
    assert "Stored in the engine. Ask a reviewer for a copy." in card_html(approved, None, None, monkeypatch)


def test_capability_is_never_in_default_results_and_has_its_own_filter(approved, monkeypatch):  # #213
    d = approved(data(title="Delivered onboarding"))
    e = approved(data(title="Engagement onboarding"))
    k = approved(data(title="Capability onboarding"))
    set_basis(e, "engagement")
    set_basis(k, "capability")
    # the default set is exactly what it was before the third basis: delivered and engagement only
    assert sorted(ids()) == sorted([d, e])
    assert sorted(c["id"] for c in sr.search(ME, "", {})) == sorted([d, e])
    assert [c["id"] for c in sr.search(ME, "onboarding", {}, "capability")] == [k]
    assert sr.search(ME, "onboarding", {}, "capability", count=True) == 1
    picks_reply(monkeypatch)
    top, others, _ = sr.results(ME, "onboarding", {})
    assert k not in {x["id"] for x in top + others}
    c = client(GUEST)
    page = c.get("/search").text
    assert "Capability onboarding" not in page and "capability document matches" in page and "Show them" in page
    page = c.post("/search", data={"bid_text": "onboarding"}).text
    assert "Capability onboarding" not in page and 'name="kind" value="capability"' in page and 'value="onboarding"' in page
    page = c.get("/search?kind=capability").text
    assert "Capability onboarding" in page and "Delivered onboarding" not in page
    assert "Capability document" in page and "not delivery evidence" in page and "Show them" not in page
    assert "documents match" not in c.post("/search", data={"bid_text": "zzzz nothing", "kind": ""}).text


def test_capability_pick_text_with_result_language_is_replaced_and_prompt_says_offering(approved, monkeypatch):  # #213
    k = approved(data(title="Capability onboarding", basis="capability"))
    set_basis(k, "capability")
    seen = []
    monkeypatch.setattr(sr, "complete_json", lambda m, s, user, *a, **kw: seen.append(s) or sr.Picks(
        picks=[sr.Pick(case_id=k, reason="It reduced onboarding time.", tailored="We delivered onboarding.")]))
    top, _, _ = sr.results(ME, "onboarding", {}, "capability")
    assert top[0]["reason"] == "" and top[0]["tailored"] == "Onboarding fell from 12 days to 3 days."
    assert "Capability documents describe an offering" in seen[0] and top[0]["outcomes"] == []


def test_facets_follow_the_kind_like_the_results(approved):  # #213
    cid = approved(data(industry="Zzcap-only industry", basis="capability"))
    set_basis(cid, "capability")
    d = approved(data(industry="Zzdelivered industry"))
    assert "Zzcap-only industry" not in [v for v, _ in sr.facets(ME)["industry"]]
    assert "Zzdelivered industry" in [v for v, _ in sr.facets(ME)["industry"]]
    cap = [v for v, _ in sr.facets(ME, "capability")["industry"]]
    assert "Zzcap-only industry" in cap and "Zzdelivered industry" not in cap


def test_capability_cards_offer_no_case_study_or_slide_formats(approved):  # #213
    k = approved(data(title="Capability onboarding", basis="capability"))
    set_basis(k, "capability")
    d = approved(data(title="Delivered onboarding"))
    page = client(GUEST).get("/search?kind=capability").text
    assert 'value="docx"' in page and 'value="md"' in page
    assert 'value="pptx"' not in page and "case_study" not in page and 'value="pdf_pptx"' not in page
    assert 'value="case_study_docx"' in client(GUEST).get("/search?kind=delivered").text
