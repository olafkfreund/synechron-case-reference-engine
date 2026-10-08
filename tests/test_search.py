import pytest

from app import db, search as sr
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


def test_summary_numbers_do_not_license_tailored_numbers(approved, monkeypatch):
    cid = approved(data().model_copy(update={"summary": "Saved 99 hours."}))
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="r", tailored="We saved 99 hours."))
    picks, notes = sr.pick("bid", sr.search(ME, "onboarding", {}), [])
    assert picks[0]["tailored"] == "Saved 99 hours." and notes  # the summary, not the LLM text
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="r", tailored="Saved 99 hours in 2 weeks."))
    assert sr.pick("bid", sr.search(ME, "onboarding", {}), [])[1]


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
    assert "orp" not in p["tailored"]  # accented evasion survives apply(), blocked() catches it -> summary


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
