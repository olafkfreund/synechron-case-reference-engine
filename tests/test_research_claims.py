import csv
import io
import json
import re
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
    claims, note = rs.extract_claims("passwordless configuration steps", PAGES)
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
    rid = make_row(f"rolling updates {uuid.uuid4().hex}")
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


def test_view_shows_our_approach_only_while_visible_and_applies_protection(approved, reg, echo, monkeypatch):
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
                     (f"Built for {name.replace('o', chr(246))}", cid))  # #166: replaced, not withheld
    t = c.get(loc).text
    assert "Built for a retailer" in t and "[withheld]" not in t and name not in t
    monkeypatch.setattr(rs.anonymise, "apply", lambda t, c: t)
    t = c.get(loc).text
    assert "[withheld]" in t and name not in t


def test_view_is_private_and_escapes_claims(reg):
    c = as_user("u1")
    loc = sent(c, None)
    evil = "<script>alert(1)</script>"
    finish(loc, [{"statement": evil, "quote": evil, "type": "vendor_claim", "url": "https://v.example/x",
                  "publisher": evil, "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 3}])
    t = c.get(loc).text
    assert "<script>alert" not in t and "&lt;script&gt;" in t and "vendor claim" not in t
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


BASE = {"statement": "S", "type": "vendor_claim", "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 3}


def done_row(c, *quotes, **extra):
    loc = sent(c, None)
    finish(loc, [{**BASE, "quote": q, "url": f"https://v.example/{i}", "publisher": "v.example", **extra} for i, q in enumerate(quotes)])
    return loc.rsplit("/", 1)[1]


def test_export_returns_only_the_picked_claims_and_is_private(reg):
    c = as_user("u1")
    rid = done_row(c, "first quote here", "second quote here", "third quote here")
    r = c.post(f"/research/{rid}/export", data={"format": "md", "n": [0, 2]})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    assert "first quote" in r.text and "third quote" in r.text and "second quote" not in r.text and "https://v.example/2" in r.text
    assert as_user("u2").post(f"/research/{rid}/export", data={"format": "md", "n": [0]}).status_code == 404


def test_export_drops_protected_claims_and_refuses_when_none_are_left(reg):
    c = as_user("u1")
    loc = sent(c, None)
    finish(loc, [{**BASE, "quote": "ok quote", "url": "https://v.example/a", "publisher": "v.example"},
                 {**BASE, "quote": "bad quote", "url": "https://w.example/b", "publisher": f"{reg}.example"}])
    rid = loc.rsplit("/", 1)[1]
    r = c.post(f"/research/{rid}/export", data={"format": "md", "n": [0, 1]})
    assert "ok quote" in r.text and "bad quote" not in r.text and reg.lower() not in r.text.lower()
    assert c.post(f"/research/{rid}/export", data={"format": "md", "n": [1]}).status_code == 400
    assert c.post(f"/research/{rid}/export", data={"format": "md"}).status_code == 400


def test_export_csv_neutralises_formulas(reg):
    c = as_user("u1")
    rid = done_row(c, "=HYPERLINK(\"http://x\")")
    r = c.post(f"/research/{rid}/export", data={"format": "csv", "n": [0]})
    assert r.headers["content-type"].startswith("text/csv") and "'=HYPERLINK" in r.text
    assert "\n=HYPERLINK" not in r.text and ',=HYPERLINK' not in r.text and '"=HYPERLINK' not in r.text


def test_export_copy_is_a_readonly_textarea(reg):
    c = as_user("u1")
    rid = done_row(c, "copy me quote")
    r = c.post(f"/research/{rid}/export", data={"format": "copy", "n": [0]})
    assert "<textarea readonly" in r.text and "copy me quote" in r.text and "attachment" not in r.headers.get("content-disposition", "")


def test_view_puts_actions_first_and_has_no_type_label(reg):
    c = as_user("u1")
    rid = done_row(c, "some quote text")
    t = c.get(f"/research/{rid}").text
    assert t.index("Use these sources") < t.index("What public sources say")
    assert "vendor claim" not in t and "AI reading:" in t and 'form="pick"' in t


def test_reviewer_view_has_attach_inside_the_pick_form(approved, reg, echo):
    cid = approved(case_with())
    c = as_user("u1", ["g-rev", DOCS])
    loc = sent(c, cid)
    finish(loc, [{**BASE, "quote": "q q q q", "url": "https://v.example/x", "publisher": "v.example"}])
    t = c.get(loc).text
    form = t[t.index('<form id="pick"'):]
    form = form[:form.index("</form>")]
    assert 'formaction="/sources/attach"' in form and 'name="research_id"' in form
    assert f'<select id="attach-to" name="target"' in form and f'<option value="{cid}:' in form and "selected" in form
    assert 'formaction="/sources/attach"' not in as_user("u1", ["g-user", DOCS]).get(loc).text  # a plain user cannot attach


def test_export_unknown_format_and_unfinished_research(reg):
    c = as_user("u1")
    rid = done_row(c, "a quote")
    assert c.post(f"/research/{rid}/export", data={"format": "xlsx", "n": [0]}).status_code == 400
    queued = sent(c, None).rsplit("/", 1)[1]  # still queued
    assert c.post(f"/research/{queued}/export", data={"format": "md", "n": [0]}).status_code == 404


def test_export_ignores_out_of_range_and_duplicate_indices(reg):
    c = as_user("u1")
    rid = done_row(c, "only quote")
    r = c.post(f"/research/{rid}/export", data={"format": "md", "n": [0, 0, 5, -1]})
    assert r.status_code == 200 and r.text.count("only quote") == 1
    assert c.post(f"/research/{rid}/export", data={"format": "md", "n": [5, -1]}).status_code == 400


@pytest.mark.parametrize("quote", [" =SUM(1)", "\n=SUM(1)", "\uff1dSUM(1)", "+1", "@x"])
def test_export_csv_prefixes_hidden_formulas(reg, quote):
    c = as_user("u1")
    rid = done_row(c, quote)
    r = c.post(f"/research/{rid}/export", data={"format": "csv", "n": [0]})
    cell = list(csv.reader(io.StringIO(r.text)))[1][0]
    assert cell == "'" + " ".join(quote.split())  # plain() collapses whitespace; the formula is still prefixed


def test_export_anonymises_or_drops_a_protected_title(reg):
    c = as_user("u1")
    loc = sent(c, None)
    finish(loc, [{**BASE, "quote": "fine quote", "url": "https://v.example/a", "publisher": "v.example", "title": f"{reg} case study"}])
    rid = loc.rsplit("/", 1)[1]
    r = c.post(f"/research/{rid}/export", data={"format": "csv", "n": [0]})
    assert reg.lower() not in r.text.lower()


def test_export_markdown_keeps_each_item_on_one_line(reg):
    c = as_user("u1")
    rid = done_row(c, "line one\n\n2. injected")
    r = c.post(f"/research/{rid}/export", data={"format": "md", "n": [0]})
    assert "line one 2. injected" in r.text and "\n2. injected" not in r.text


def test_search_page_has_research_button(approved, monkeypatch):
    from app import search as sr
    cid = approved()
    monkeypatch.setattr(sr, "complete_json", lambda *a, **k: sr.Picks(picks=[sr.Pick(case_id=cid, reason="r", tailored="Fine 12 days.")]))
    r = as_user("u1", ["g-user", DOCS]).post("/search", data={"bid_text": "onboarding"})
    assert f'action="/research/from-case"' in r.text and f'name="case_id" value="{cid}"' in r.text


def test_cache_hit_redoes_failed_or_missing_claims_without_refetching(web, monkeypatch):
    q = f"rolling updates cache {uuid.uuid4().hex}"
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
              "markdown": "x " * rs.page_chars() + "this sentence is past the cut"}]
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Claims(claims=[
        claim("Past the cut.", quote="this sentence is past the cut")]))
    assert rs.extract_claims("q", pages)[0] == []


def test_research_is_not_refused_by_the_extraction_guard(monkeypatch):
    """The research worst case (8 pages of PAGE_CHARS, schema in the prompt) must clear the overflow guard (#212 G1); its real overflow is tracked in #217."""
    from types import SimpleNamespace

    from app import llm
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/qwen3:14b")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 24576}))
    sent = []

    def completion(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"claims": []}'), finish_reason="stop")])
    monkeypatch.setattr(llm.litellm, "completion", completion)
    pages = [dict(PAGES[0], markdown="x" * rs.page_chars()) for _ in range(8)]
    claims, note = rs.extract_claims("q", pages)
    assert sent and note is None and claims == []


def test_a_markdown_quote_is_sourced_against_the_raw_page_and_stored_verbatim(monkeypatch):
    raw = "Entra **supports** [passwordless](https://x.io/a) sign in out of the box"
    page = [{**PAGES[0], "markdown": raw + "."}]
    reply(monkeypatch, claim(quote=raw))
    claims, _ = rs.extract_claims("passwordless", page)
    assert [c["quote"] for c in claims] == [raw]  # kept: sourced on the raw text, stored as written


def pick_fields(t):
    """The hidden fields inside form#pick, as a browser would post them (plus the first quote ticked)."""
    form = t[t.index('<form id="pick"'):]
    form = form[:form.index("</form>")]
    return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form)), form


def test_plain_user_page_posts_what_save_export_and_brief_need(reg):
    c = as_user("u1")
    rid = done_row(c, "some quote text")
    t = c.get(f"/research/{rid}").text
    assert "Download all (industry context only)" in t and t.index("Use these sources") < t.index("Download all")
    fields, _ = pick_fields(t)
    assert fields.get("research_id") == str(rid)
    posted = {**fields, "n": [0], "topic": ""}
    r = c.post("/library/save", data=posted, follow_redirects=False)  # no topic typed: the search is the default
    assert r.status_code == 303
    with db.connect() as conn:
        assert conn.execute("select topic from library_items where id=%s", (int(r.headers["location"].rsplit("/", 1)[1]),)).fetchone()[0] == "kyc automation"
    assert c.post(f"/research/{rid}/export", data={**posted, "format": "md"}).status_code == 200
    assert c.post(f"/research/{rid}/brief", data={**posted, "title": ""}, follow_redirects=False).status_code == 303


def test_save_without_a_topic_uses_the_brief_title_or_a_placeholder_and_errors_are_pages(reg):
    c = as_user("u1")
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','t',%s,%s,'done') returning id", (
            json.dumps([{"quote": "q q q", "publisher": "v.example", "url": "https://v.example/0"}]),
            json.dumps({"title": "Access policy brief", "takeaways": ["Fine [1]."], "themes": [], "flags": []}))).fetchone()[0]
    r = c.post("/library/save", data={"brief_id": bid}, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as conn:
        assert conn.execute("select topic from library_items where id=%s", (int(r.headers["location"].rsplit("/", 1)[1]),)).fetchone()[0] == "access policy brief"
    rid = done_row(c, "some quote text")
    html = {"Accept": "text/html"}
    r = c.post("/library/save", data={"research_id": rid, "topic": "x"}, headers=html)  # nothing picked
    assert r.status_code == 400 and "text/html" in r.headers["content-type"] and "pick at least one" in r.text and f'href="/research/{rid}"' in r.text
    assert c.post("/library/save", data={"research_id": rid, "topic": "x"}).json()["detail"].startswith("pick at least one")  # API keeps JSON
    assert c.post("/library/save", data={"research_id": 999999999, "n": [0]}).status_code == 404


def test_save_falls_back_to_untitled_research_and_never_stores_an_email_as_the_name(reg):
    c = as_user("u1")
    with db.connect() as conn:
        bid = conn.execute("insert into briefs(created_by, title, quotes, body, status) values ('u1','',%s,%s,'done') returning id", (
            json.dumps([{"quote": "q q q", "publisher": "v.example", "url": "https://v.example/0"}]),
            json.dumps({"title": "", "takeaways": ["Fine [1]."], "themes": [], "flags": []}))).fetchone()[0]
    lid = int(c.post("/library/save", data={"brief_id": bid}, follow_redirects=False).headers["location"].rsplit("/", 1)[1])
    with db.connect() as conn:
        assert conn.execute("select topic, created_by_name from library_items where id=%s", (lid,)).fetchone() == ("Untitled research", "")  # name == sub: no real name


def test_page_chars_follows_num_ctx_for_ollama_only(monkeypatch):
    monkeypatch.delenv("DRAFT_MODEL_OPTIONS", raising=False)
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/qwen3:14b")
    assert rs.page_chars() == 6000  # no num_ctx
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 24576}))
    n = rs.page_chars()
    assert 4500 < n < 6000
    system = len(rs.CLAIMS_SYSTEM) + len(rs.json_cue(rs.llm_schema(rs.Claims), True))
    assert rs.MAX_RESULTS * n + system <= (24576 - rs.MAX_TOKENS - 2000) * 3  # 8 pages fit with no silent cut (#217)
    monkeypatch.setenv("DRAFT_MODEL", "bedrock/x")
    assert rs.page_chars() == 6000  # llm.py sends num_ctx to Ollama only


def mk(statement, page, quote, rel=2, type="configuration"):
    return rs.Claim(statement=statement, page=page, quote=quote, type=type, relevance=rel)


def test_every_drop_reason_the_order_and_the_caps_in_one_stub(monkeypatch):
    letters = "abcdefgh"
    host = ["learn.microsoft.com", "b1.example", "b2.example", "b3.example"]
    wordy = " ".join(f"w{i}x" for i in range(81))
    pages = [dict(PAGES[0], url=f"https://{h}/p", publisher=h,
                  markdown=" ".join(f"Passwordless sign in item {i} for page {p}." for i in range(8))
                  + (f" {wordy}. Unrelated cats sleep on sofas daily." if p == 0 else ""))
             for p, h in enumerate(host)]
    q = lambda p, i: f"passwordless sign in item {i} for page {p}"  # noqa: E731
    st = lambda p, i: f"Claim {letters[p]}{letters[i]}."  # noqa: E731
    bad = [mk("Bad page.", 9, q(0, 0)), mk("Negative page.", -1, q(0, 0)), mk("Bad type.", 0, q(0, 0), type="marketing"),
           mk("  ", 0, q(0, 0)),
           mk("Too long passwordless.", 0, wordy), mk("Invented.", 0, "a sentence that is nowhere on this page"), mk("Short.", 0, "passwordless sign in"),
           mk("Tangential.", 0, q(0, 5), rel=1), mk("Zero clamps to one.", 0, q(0, 5), rel=0), mk("Negative clamps to one.", 0, q(0, 5), rel=-1),
           mk("Cats sleep.", 0, "Unrelated cats sleep on sofas daily", rel=3),   # no word of the query
           mk("Needs 7 items.", 0, q(0, 5)),                                      # 7 is not in the quote
           mk(st(1, 0), 1, q(1, 0))]                                              # duplicate of the kept one below
    good = [mk(st(0, i), 0, q(0, i)) for i in range(4)] + [mk(st(0, 4), 0, q(0, 4), rel=3)]      # five on the vendor page: cap 4
    for p in (1, 2, 3):
        good += [mk(st(p, i), p, q(p, i), rel=3 if i == 3 else 2) for i in range(4)]
    for b in bad[:-1]:  # alone, each is dropped for its own reason (in the crowd a cap could hide a missing check)
        reply(monkeypatch, b)
        assert rs.extract_claims("passwordless sign registry", pages)[0] == [], b.statement
    reply(monkeypatch, *bad, *good)
    out = rs.extract_claims("passwordless sign registry", pages)[0]
    got = [c["statement"] for c in out]
    assert len(got) == rs.MAX_CLAIMS == 15
    assert not set(got) & ({c.statement for c in bad[:11]} | {"Needs 7 items."})                    # every drop reason applied
    assert got[:4] == [st(0, 4), st(0, 0), st(0, 1), st(0, 2)]                                    # rank 1 first, relevance 3 before 2, page cap 4
    assert st(0, 3) not in got
    assert got[4:7] == [st(1, 3), st(2, 3), st(3, 3)]                                              # then every relevance 3, in a rank
    assert got[7:] == [st(p, i) for p in (1, 2) for i in range(3)] + [st(3, 0), st(3, 1)]         # relevance 2 in page order; the total cap cut the rest
    assert got.count(st(1, 0)) == 1 and [c["url"] for c in out].count(pages[3]["url"]) == 3          # the duplicate went; the last page lost one


def test_a_bad_relevance_does_not_reject_the_reply():
    r = rs.Claims.model_validate_json(json.dumps({"claims": [
        {"statement": "a", "page": 0, "quote": "q", "type": "t", "relevance": "high"},
        {"statement": "b", "page": 0, "quote": "q", "type": "t", "relevance": 2.5},
        {"statement": "c", "page": 0, "quote": "q", "type": "t", "relevance": None},
        {"statement": "d", "page": 0, "quote": "q", "type": "t", "relevance": 9},
        {"statement": "e", "page": 0, "quote": "q", "type": "t", "relevance": "-3"},
        {"statement": "f", "page": 0, "quote": "q", "type": "t"}]}))
    assert [c.relevance for c in r.claims] == [2, 2, 2, 3, 1, 2]


def test_a_budget_under_500_characters_fails_loudly_not_silently(monkeypatch):
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/qwen3:14b")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 11000}))
    with pytest.raises(rs.ResearchError, match="num_ctx too small"):
        rs.page_chars()
    with pytest.raises(rs.ResearchError):
        rs.extract_claims("q", PAGES)


MD_PAGE = """# Configure the registry

Some text.

```python
mlflow.set_registry_uri("databricks")
```

## Unrelated

```
rm -rf /
```

## Deploying the registry

1. Create a model
2. Register the model
3. Promote the version

- not numbered
"""


def test_notes_from_takes_fences_and_step_runs_that_share_a_query_word():
    notes = rs.notes_from(MD_PAGE, "mlflow model registry")
    assert notes == ['mlflow.set_registry_uri("databricks")', "1. Create a model\n2. Register the model\n3. Promote the version"]
    assert all(rs.quote_in(MD_PAGE, n) for n in notes)  # a substring of the page by construction
    assert rs.notes_from(MD_PAGE, "kubernetes pods") == []
    assert rs.notes_from("1. a\n2. b\n", "registry") == []  # fewer than three steps
    assert rs.notes_from("```\n" + "x" * 1600 + " registry\n```", "registry") == []  # over 1,500 characters
    many = "# registry\n" + "".join(f"```\ncode {i}\n```\n" for i in range(5))
    assert len(rs.notes_from(many, "registry")) == rs.NOTES_PER_PAGE


def test_with_notes_adds_technical_note_claims_capped_per_research():
    pages = [dict(PAGES[0], url=f"https://v{i}.example/", publisher=f"v{i}.example", markdown=MD_PAGE) for i in range(6)]
    out = rs.with_notes("mlflow model registry", pages, [{"statement": "S", "type": "configuration"}])
    notes = [c for c in out if c["type"] == "technical_note"]
    assert out[0]["statement"] == "S" and len(notes) == rs.MAX_NOTES
    assert notes[0]["statement"] == "" and notes[0]["url"] == pages[0]["url"] and notes[0]["rank"] == 3


def test_a_cache_hit_recomputes_notes_from_the_stored_pages(web, monkeypatch):
    q = f"mlflow model registry {uuid.uuid4().hex}"
    page = {"url": "https://x.example/p", "publisher": "x.example", "title": "T", "retrieved_at": "2026-10-01T00:00:00+00:00", "markdown": MD_PAGE}
    stale = {**BASE, "statement": "", "quote": "stale note", "type": "technical_note", "url": page["url"], "publisher": "x.example"}
    kept = {**BASE, "quote": "Some text about the registry here", "url": page["url"], "publisher": "x.example"}
    with db.connect() as conn:  # claims, no "note": the old code left it as it was
        old = conn.execute("insert into research(query, status, results, created_by) values (%s,'done',%s,'u1') returning id",
                           (q, json.dumps({"pages": [page], "skipped": [], "claims": [kept, stale]}))).fetchone()[0]
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: pytest.fail("a cache hit with claims must not call the model"))
    monkeypatch.setattr(rs, "brave_search", lambda *a: pytest.fail("cache hit must not search"))
    rid = make_row(q)
    try:
        rs.run(rid)
        claims = row(rid)[2]["claims"]
        assert claims[0] == kept and [c["quote"] for c in claims if c["type"] == "technical_note"] == rs.notes_from(MD_PAGE, q)
        assert "stale note" not in [c["quote"] for c in claims]
    finally:
        with db.connect() as conn:
            conn.execute("delete from research where id = any(%s)", ([old, rid],))


def test_a_too_small_budget_is_recorded_on_the_row(web, monkeypatch):
    q = f"mlflow {uuid.uuid4().hex}"
    page = {"url": "https://x.example/p", "publisher": "x.example", "title": "T", "retrieved_at": "2026-10-01T00:00:00+00:00", "markdown": MD_PAGE}
    with db.connect() as conn:
        old = conn.execute("insert into research(query, status, results, created_by) values (%s,'done',%s,'u1') returning id",
                           (q, json.dumps({"pages": [page], "skipped": [], "claims": [], "note": "failed"}))).fetchone()[0]
    monkeypatch.setenv("DRAFT_MODEL", "ollama_chat/qwen3:14b")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", json.dumps({"num_ctx": 11000}))
    rid = make_row(q)
    try:
        rs.run(rid)
        status, error = row(rid)[0], row(rid)[1]
        assert status == "failed" and "num_ctx too small" in error
    finally:
        with db.connect() as conn:
            conn.execute("delete from research where id = any(%s)", ([old, rid],))


def test_fences_close_on_enough_backticks_and_inline_backticks_open_nothing():
    page = ("# registry\n\n````\n```\ninner\n```\n````\n\n```x``` and more text\n\nplain words\n\n```\nreal\n```\n")
    notes = rs.notes_from(page, "registry")
    assert notes == ["```\ninner\n```", "real"] and all(rs.quote_in(page, n) for n in notes)
    assert rs.notes_from("# registry\n\n```\nnever closed\n", "registry") == []
    assert rs.notes_from("# registry\n\n```\n   \n```\n", "registry") == []  # a blank block is no note


def test_a_comment_inside_code_is_not_the_nearest_heading():
    page = "## Cats\n\n```\n# registry setup\n```\n\n```\nls -la\n```\n"
    assert rs.notes_from(page, "registry") == ["# registry setup"]  # the second block sits under "## Cats"


