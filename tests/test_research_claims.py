import json
import uuid

import pytest

from app import db, research as rs
from app.schema import Sourced
from tests.test_auth import env  # noqa: F401
from tests.test_research import KEY, Web, as_user, make_row, reg, row, web  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

DOC0 = "Azure Entra supports passwordless sign in out of the box for all tenants. Setup needs 3 configuration steps."
PAGES = [
    {"url": "https://learn.microsoft.com/entra", "publisher": "learn.microsoft.com", "retrieved_at": "2026-10-01T00:00:00+00:00", "markdown": DOC0},
    {"url": "https://random.blog/post", "publisher": "random.blog", "retrieved_at": "2026-10-01T00:00:00+00:00",
     "markdown": "Many banks run KYC checks nightly as a batch process in practice."},
]


def claim(statement="Entra supports passwordless sign in.", page=0, quote="supports passwordless sign in out of the box",
          type="out_of_the_box"):
    return rs.Claim(statement=statement, page=page, quote=quote, type=type)


def reply(monkeypatch, *claims):
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Claims(claims=list(claims)))


def test_only_verified_claims_survive(monkeypatch):
    reply(monkeypatch,
          claim(),                                                              # good
          claim("Made up.", quote="a sentence that is nowhere on this page"),   # fabricated quote
          claim("Short.", quote="passwordless sign in"),                        # real but only 3 words
          claim("Needs 5 steps.", quote="Setup needs 3 configuration steps"),   # number not in the quote
          claim("Bad page.", page=7), claim("Negative page.", page=-1),
          claim("Bad type.", type="marketing"), claim("  ", quote="supports passwordless sign in out of the box"),
          claim("Needs 3 steps.", quote="Setup needs 3 configuration steps", type="configuration"))
    claims, note = rs.extract_claims("passwordless", PAGES)
    assert [c["statement"] for c in claims] == ["Entra supports passwordless sign in.", "Needs 3 steps."] and note is None
    c = claims[0]
    assert (c["url"], c["publisher"], c["retrieved_at"], c["rank"], c["type"]) == (
        "https://learn.microsoft.com/entra", "learn.microsoft.com", "2026-10-01T00:00:00+00:00", 1, "out_of_the_box")


def test_source_rank_and_ordering(monkeypatch):
    ranks = {"learn.microsoft.com": 1, "docs.aws.amazon.com": 1, "developer.example.com": 3, "docs.attacker.com": 3,
             "www.nist.gov": 1, "service.gov.uk": 1, "ietf.org": 1, "gartner.com": 2, "www.forrester.com": 2,
             "random.blog": 3, "evil-aws.amazon.com.attacker.com": 3, "notnist.gov.example.com": 3, "amazon.com": 3}
    assert {h: rs.source_rank(h) for h in ranks} == ranks
    pages = [dict(PAGES[1], markdown="Many banks run checks nightly in a batch process."), dict(PAGES[0]),
             dict(PAGES[0], publisher="www.gartner.com", url="https://www.gartner.com/x")]
    reply(monkeypatch,
          claim("Blog.", page=0, quote="banks run checks nightly in", type="out_of_the_box"),
          claim("Vendor marketing.", page=1, quote="supports passwordless sign in out of the box", type="vendor_claim"),
          claim("Vendor config.", page=1, quote="Setup needs 3 configuration steps", type="configuration"),
          claim("Vendor ootb.", page=1, quote="supports passwordless sign in out of the box", type="out_of_the_box"),
          claim("Analyst.", page=2, quote="supports passwordless sign in out of the box", type="industry_practice"))
    assert [c["statement"] for c in rs.extract_claims("q", pages)[0]] == [
        "Vendor ootb.", "Vendor config.", "Vendor marketing.", "Analyst.", "Blog."]


def test_llm_failure_keeps_pages_and_job_is_done(web, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(rs, "complete_json", boom)
    claims, note = rs.extract_claims("q", PAGES)
    assert claims == [] and "could not be extracted" in note
    web.results = ["https://docs.example/g"]
    web.html("docs.example", "/robots.txt", "")
    web.html("docs.example", "/g", "<p>guide</p>")
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    status, _, results = row(rid)
    assert status == "done" and len(results["pages"]) == 1 and results["claims"] == [] and "could not" in results["note"]
    with db.connect() as c:
        c.execute("delete from research where id=%s", (rid,))


def test_job_stores_claims_with_rank(web, monkeypatch):
    web.results = ["https://docs.example/g"]
    web.html("docs.example", "/robots.txt", "")
    web.html("docs.example", "/g", "Kubernetes supports rolling updates without downtime out of the box.")
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Claims(claims=[
        claim("Kubernetes rolls updates.", quote="supports rolling updates without downtime")]))
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    c = row(rid)[2]["claims"][0]
    assert c["rank"] == 3 and c["publisher"] == "docs.example" and c["url"] == "https://docs.example/g" and c["retrieved_at"]
    with db.connect() as conn:
        conn.execute("delete from research where id=%s", (rid,))


# --- research this case -----------------------------------------------------------------------------

def case_with(**kw):
    return data(title="Faster onboarding", tech=("Kubernetes",), **kw).model_copy(update={
        "capabilities": [Sourced[str](value="KYC automation", source_quote="q")],
        "engagement_type": Sourced[str](value="Cloud migration", source_quote="q"),
        "challenge": Sourced[str](value="Secret challenge text", source_quote="q"),
        "solution": Sourced[str](value="An event driven KYC workflow", source_quote="q")})


@pytest.fixture
def echo(monkeypatch):
    """The 'rewrite' returns its input, so the preview shows exactly the seeded question."""
    monkeypatch.setattr(rs, "complete_json", lambda alias, system, user, cls, **kw: cls(query=user))


def test_from_case_seed_has_no_title_or_challenge(approved, reg, echo):
    cid = approved(case_with())
    r = as_user("u1", ["g-user", DOCS]).post("/research/from-case", data={"case_id": cid})
    assert r.status_code == 200
    t = r.text.lower()
    assert "kyc automation" in t and "kubernetes" in t and "cloud migration" in t
    assert "onboarding" not in t and "secret" not in t and "retail" not in t
    assert f'name="case_id" value="{cid}"' in r.text


def test_from_case_needs_a_visible_case(approved, reg, echo):
    c = as_user("u1", ["g-user", DOCS])
    for hidden in (approved(case_with(), acl=("g-other",)), approved(case_with(), due="-1 day"),
                   approved(case_with(), status="extracted", due=None), 999999999):
        assert c.post("/research/from-case", data={"case_id": hidden}).status_code == 404
        assert c.post("/research/send", data={"query": "kyc", "case_id": hidden}).status_code == 404
    assert as_user("u1", ["g-user"]).post("/research/from-case", data={"case_id": approved(case_with())}).status_code == 404


def sent(c, cid):
    r = c.post("/research/send", data={"query": "kyc automation", "case_id": cid}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def finish(loc, claims, pages=()):
    rid = int(loc.rsplit("/", 1)[1])
    with db.connect() as conn:
        conn.execute("update research set status='done', results=%s where id=%s",
                     (json.dumps({"pages": list(pages), "skipped": [], "claims": claims}), rid))


def test_view_shows_our_approach_only_while_visible_and_applies_protection(approved, reg, echo):
    name = reg
    cid = approved(case_with().model_copy(update={"solution": Sourced[str](value=f"Built for {name} on Kafka", source_quote="q")}))
    c = as_user("u1", ["g-user", DOCS])
    loc = sent(c, cid)
    with db.connect() as conn:
        assert conn.execute("select case_id from research where id=%s", (int(loc.rsplit("/", 1)[1]),)).fetchone()[0] == cid
    finish(loc, [{"statement": "S", "quote": "q q q q", "type": "configuration", "url": "https://v.example/x",
                  "publisher": "v.example", "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 3}])
    t = c.get(loc).text
    assert "Our approach" in t and "Built for a retailer on Kafka" in t and "Kubernetes" in t and name not in t
    with db.connect() as conn:  # the case becomes invisible: the viewer loses the column, the research stays
        conn.execute("update documents set acl_groups='{g-other}' where id=(select document_id from cases where id=%s)", (cid,))
    assert "Our approach" not in c.get(loc).text and c.get(loc).status_code == 200
    with db.connect() as conn:
        conn.execute("update documents set acl_groups=%s where id=(select document_id from cases where id=%s)", ([DOCS], cid))
    with db.connect() as conn:
        conn.execute("update cases set data = jsonb_set(data, '{solution,value}', to_jsonb(%s::text)) where id=%s",
                     (f"Built for {name.replace('o', chr(246))}", cid))  # accented evasion survives apply()
    t = c.get(loc).text
    assert "[withheld]" in t and name not in t


def test_view_is_private_and_escapes_claims(reg):
    c = as_user("u1")
    loc = sent(c, None)
    evil = "<script>alert(1)</script>"
    finish(loc, [{"statement": evil, "quote": evil, "type": "vendor_claim", "url": "https://v.example/x",
                  "publisher": evil, "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 3}])
    t = c.get(loc).text
    assert "<script>alert" not in t and "&lt;script&gt;" in t and "vendor claim" in t
    assert as_user("u2").get(loc).status_code == 404


def test_view_hides_protected_names_in_claims_and_sources(reg):
    c = as_user("u1")
    loc = sent(c, None)
    base = {"statement": "S", "type": "vendor_claim", "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 3}
    finish(loc, [
        {**base, "quote": f"{reg} runs this nightly in production", "url": "https://v.example/a", "publisher": "v.example"},
        {**base, "quote": "dropped publisher claim", "url": "https://w.example/b", "publisher": f"{reg}.example"},
        {**base, "quote": "clean claim quote", "url": "https://v.example/c", "publisher": "v.example"},
        {**base, "quote": "url only claim", "url": f"https://v.example/{reg}", "publisher": "v.example"}],
        pages=[{"url": f"https://{reg}.example/x", "publisher": "w.example", "retrieved_at": "2026-10-01T00:00:00+00:00"},
               {"url": "https://v.example/c", "publisher": "v.example", "retrieved_at": "2026-10-01T00:00:00+00:00"}])
    t = c.get(loc).text
    assert reg.lower() not in t.lower()
    assert "2 statement(s) not shown" in t and "1 source(s) skipped." in t
    assert "clean claim quote" in t and "runs this nightly in production" in t


def test_search_page_has_research_button(approved, monkeypatch):
    from app import search as sr
    cid = approved()
    monkeypatch.setattr(sr, "complete_json", lambda *a, **k: sr.Picks(picks=[sr.Pick(case_id=cid, reason="r", tailored="Fine 12 days.")]))
    r = as_user("u1", ["g-user", DOCS]).post("/search", data={"bid_text": "onboarding"})
    assert f'action="/research/from-case"' in r.text and f'name="case_id" value="{cid}"' in r.text


def test_cache_hit_redoes_failed_or_missing_claims_without_refetching(web, monkeypatch):
    q = f"research cache {uuid.uuid4().hex}"
    page = {"url": "https://x.example/p", "publisher": "x.example", "title": "T", "retrieved_at": "2026-10-01T00:00:00+00:00",
            "markdown": "Kubernetes supports rolling updates without downtime out of the box."}
    with db.connect() as conn:  # an earlier row: extraction failed (or a pre-claims row)
        old = conn.execute("insert into research(query, status, results, created_by) values (%s,'done',%s,'u1') returning id",
                           (q, json.dumps({"pages": [page], "skipped": [], "claims": [], "note": "failed"}),)).fetchone()[0]
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Claims(claims=[
        claim("Rolling updates.", quote="supports rolling updates without downtime")]))
    monkeypatch.setattr(rs, "brave_search", lambda *a: pytest.fail("cache hit must not search"))
    rid = make_row(q)
    try:
        rs.run(rid)
        results = row(rid)[2]
        assert len(results["claims"]) == 1 and "note" not in results
    finally:
        with db.connect() as conn:
            conn.execute("delete from research where id = any(%s)", ([old, rid],))


def test_quote_must_be_within_what_the_model_saw(monkeypatch):
    pages = [{"url": "https://x.example/p", "publisher": "x.example", "retrieved_at": "t",
              "markdown": "x " * rs.PAGE_CHARS + "this sentence is past the cut"}]
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Claims(claims=[
        claim("Past the cut.", quote="this sentence is past the cut")]))
    assert rs.extract_claims("q", pages)[0] == []
