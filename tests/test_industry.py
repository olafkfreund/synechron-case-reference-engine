import html
import json
import re
import zipfile
from io import BytesIO

import pytest

from app import db, render
from tests.test_auth import env  # noqa: F401
from tests.test_render import doc_text, pdf_text, slides  # noqa: F401
from tests.test_research import as_user, reg  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

G = ["g-user", DOCS]
Q1 = "Entra supports passwordless sign in out of the box"
Q2 = "Tenants need a conditional access policy configured first"


def claim(quote=Q1, type="out_of_the_box", publisher="learn.microsoft.com", url="https://learn.microsoft.com/entra"):
    return {"statement": "AI summary that must never appear", "quote": quote, "type": type, "url": url,
            "publisher": publisher, "retrieved_at": "2026-10-01T00:00:00+00:00", "rank": 1}

OLD = ("Supported out of the box", "Available through configuration", "Common industry practice", "Vendor statement")


@pytest.fixture
def rows(env):  # noqa: F811
    db.init()
    made = []

    def add(claims, by="u1", status="done", case_id=None, pages=()):
        with db.connect() as c:
            made.append(c.execute("insert into research(query, created_by, status, results, case_id) values ('q',%s,%s,%s,%s) returning id",
                                  (by, status, json.dumps({"pages": list(pages), "skipped": [], "claims": claims}), case_id)).fetchone()[0])
        return made[-1]
    yield add
    with db.connect() as c:
        c.execute("delete from research where id = any(%s)", (made,))


def gen(rid=None, ids=(), fmt="md", ack=False, user="u1", **kw):
    form = {"format": fmt, "case_ids": list(ids), **({"research_id": rid} if rid else {}), **({"industry_context_ack": "on"} if ack else {})}
    return as_user(user, G).post("/generate", data=form, **kw)


def audit():
    with db.connect() as c:
        return c.execute("select case_ids, industry_context_ack, format from generations order by id desc limit 1").fetchone()


def test_no_case_needs_research_and_the_acknowledgement(rows):
    rid = rows([claim()])
    assert gen().status_code == 400                       # nothing at all
    assert gen(rid).status_code == 400                    # research, but no acknowledgement
    r = gen(rid, ack=True)
    assert r.status_code == 200 and audit() == ([], True, "md")


def test_with_a_case_the_ack_is_not_needed_and_stored_false(rows, approved):
    rid, cid = rows([claim()]), approved()
    assert gen(rid, [cid]).status_code == 200 and audit()[:2] == ([cid], False)
    assert gen(rid, [cid], ack=True).status_code == 200 and audit()[:2] == ([cid], False)
    assert gen(None, [cid]).status_code == 200 and "Industry context" not in gen(None, [cid]).text  # a case alone: no section
    assert gen(rid, [cid, cid, cid, 1, 2, 3][:0] or [cid]).status_code == 200


def test_research_must_be_yours_and_finished(rows):
    assert gen(rows([claim()], by="u2"), ack=True).status_code == 404
    assert gen(rows([claim()], status="running"), ack=True).status_code == 404
    assert gen(999999999, ack=True).status_code == 404
    assert gen(rows([]), ack=True).status_code == 400     # done, but no usable statements


def test_markdown_section_wording(rows, approved):
    rid = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x"),
                claim("Analysts say this is common practice for banks", "industry_practice"),
                claim("The vendor describes this as best in class", "vendor_claim")])
    t = gen(rid, [approved()], ack=True).text
    sec = t[t.index("# Industry context (public sources)"):]
    assert render.INDUSTRY_DISCLAIMER.replace("_", "\\_") in sec
    assert not [p for p in OLD if p in sec]
    plain = sec.replace("\\", "")
    assert "docs.vendor.io states:" in sec and "## learn.microsoft.com" in plain  # no title: the host heads the group
    assert f"> “{Q1}” [1]" in plain and f"> “{Q2}” [2]" in plain
    body, sources = plain.split("## Sources")
    assert "1. learn.microsoft.com, learn.microsoft.com, <https://learn.microsoft.com/entra>, retrieved 2026-10-01" in sources
    assert "https://" not in body  # URLs leave the body
    assert "AI summary" not in t
    assert not re.search(r"\b(we|our|ours|delivered)\b", sec.replace(Q1, ""), re.I), sec  # fixed wording only


def test_every_fixed_string_avoids_first_person_and_delivery_wording():
    fixed = [render.INDUSTRY_HEADING, render.APPROACH_HEADING, render.INDUSTRY_DISCLAIMER, "states", "retrieved"]
    assert not [s for s in fixed if re.search(r"\b(we|our|ours|us|delivered)\b", s, re.I)]
    assert render.COMPANY in render.INDUSTRY_DISCLAIMER


def test_docx_and_pdf_have_the_quote_and_source(rows, approved):
    rid, cid = rows([claim()]), approved()
    t = doc_text(gen(rid, [cid], "docx").content)
    for want in (render.INDUSTRY_HEADING, render.INDUSTRY_DISCLAIMER, "learn.microsoft.com states:", f"{Q1}” [1]", "Sources",
                 "[1] learn.microsoft.com, learn.microsoft.com, https://learn.microsoft.com/entra, retrieved 2026-10-01", "Faster onboarding"):
        assert want in t, want
    text = " ".join(pdf_text(gen(rid, [cid], "pdf_docx").content)[0].split())  # the PDF wraps lines
    assert render.INDUSTRY_HEADING in text and Q1 in text and "learn.microsoft.com" in text
    assert "{{" not in t and "{%" not in t
    assert t.count("https://learn.microsoft.com/entra") == 1  # the URL only in the source list, not in the body
    assert not [p for p in OLD if p in t]


def test_pptx_industry_slide_keeps_the_quote_next_to_the_phrase(rows, approved):
    rid, cid = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x")]), approved()
    prs, out = slides(gen(rid, [cid], "pptx").content)
    assert len(out) == 2 and prs.slides[1].slide_layout.name == render.INDUSTRY_LAYOUT
    ind = out[1]
    assert ind["Title"] == [render.INDUSTRY_HEADING] and ind["Summary"] == [render.INDUSTRY_DISCLAIMER]
    assert len(ind["Statements"]) == 2
    for line, phrase, quote in zip(ind["Statements"], ("learn.microsoft.com states:", "docs.vendor.io states:"), (Q1, Q2)):
        assert line.startswith(phrase) and quote[:40] in line and line.count(phrase.split()[0]) == 1  # source once, quote next to it
    assert len(slides(gen(rid, [], "pptx", ack=True).content)[1]) == 1  # industry only
    assert gen(rid, [cid], "pdf_pptx").content.startswith(b"%PDF")


def test_a_claim_naming_a_protected_client_is_replaced_or_dropped_and_counted(rows, approved, reg, monkeypatch):
    name = reg
    evasion = name.replace("o", "ö")
    kept = gen(rows([claim(f"{evasion} runs this in production daily")]), ack=True)  # #166: replaced, not dropped
    assert kept.status_code == 200 and "a retailer runs this in production daily" in kept.text and "omitted" not in kept.text
    real = render.anonymise.apply
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t if "ö" in t else real(t, c))  # misses only the accent: blocked() must still guard
    rid = rows([claim(f"{name} runs passwordless sign in out of the box"), claim(f"{evasion} runs this in production daily"),
                claim(Q2, "configuration")])
    r = gen(rid, [approved()], ack=True)
    assert r.status_code == 200 and "Faster onboarding" in r.text                 # the case output is still produced
    t = r.text
    assert name not in t and evasion not in t and "a retailer runs passwordless" in t and Q2 in t
    assert "1 public statement omitted: it named a protected client" in t
    only = gen(rows([claim(f"{evasion} runs this in production daily")]), ack=True)
    assert only.status_code == 400                                                  # nothing usable is left


def test_pptx_industry_slide_shows_the_omitted_note(rows, approved, reg, monkeypatch):
    rid = rows([claim(), claim(f"{reg.replace('o', 'ö')} runs this in production daily", url="https://learn.microsoft.com/other")])  # one slide line per page
    kept = slides(gen(rid, [approved()], "pptx").content)[1][1]["Statements"]  # #166: replaced, not dropped
    assert len(kept) == 2 and "omitted" not in "".join(kept)
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # apply() misses: blocked() alone must still guard
    _, out = slides(gen(rid, [approved()], "pptx").content)
    st = out[1]["Statements"]
    assert st[0].startswith("learn.microsoft.com states:") and st[-1] == "1 public statement omitted: it named a protected client"
    many = rows([claim(f"{Q1} {i}", url=f"https://learn.microsoft.com/p{i}") for i in range(render.SLIDE_ITEMS + 1)] + [claim(f"{reg.replace('o', 'ö')} runs this daily")])
    st = slides(gen(many, [approved()], "pptx").content)[1][1]["Statements"]
    assert len(st) == render.SLIDE_ITEMS + 1 and st[-1].startswith("1 public statement omitted")  # the cap never cuts the note


def test_script_in_a_quote_is_escaped_everywhere(rows, approved):
    evil = "<script>alert(1)</script> & more"
    rid, cid = rows([claim(evil)]), approved()
    md = gen(rid, [cid]).text
    assert "<script>" not in md and "\\<script\\>" in md
    d = gen(rid, [cid], "docx").content
    xml = zipfile.ZipFile(BytesIO(d)).read("word/document.xml").decode()
    assert "<script>" not in xml and "&lt;script&gt;" in xml and evil in doc_text(d)
    p = gen(rid, [cid], "pptx").content
    slide_xml = zipfile.ZipFile(BytesIO(p)).read("ppt/slides/slide2.xml").decode()
    assert "<script>" not in slide_xml and "&lt;script&gt;" in slide_xml  # text, never markup


def test_preview_includes_industry_and_keeps_the_ack(rows, approved):
    rid, cid = rows([claim()]), approved()

    def prev(ids=(), ack=False):  # format is posted too, as the research form does
        form = {"format": "docx", "case_ids": list(ids), "research_id": rid, **({"industry_context_ack": "on"} if ack else {})}
        return as_user("u1", G).post("/preview", data=form)
    r = prev([cid])
    assert r.status_code == 200 and Q1 in r.text and "learn.microsoft.com states:" in r.text
    assert f"&ldquo;{Q1}&rdquo; [1]" in r.text and r.text.count("https://learn.microsoft.com/entra") == 2  # only in the source list (href and text)
    assert f'name="research_id" value="{rid}"' in r.text
    assert prev().status_code == 400
    esc = as_user("u1", G).post("/preview", data={"case_ids": [cid], "research_id": rows([claim("<b>x</b> in the quote")])})
    assert "&lt;b&gt;x&lt;/b&gt; in the quote" in esc.text and "<b>x</b>" not in esc.text
    r = prev(ack=True)
    assert r.status_code == 200 and 'name="industry_context_ack"' in r.text


def test_research_page_offers_the_download(rows, approved):
    rid = rows([claim()])
    t = as_user("u1", G).get(f"/research/{rid}").text
    assert 'action="/generate"' in t and f'name="research_id" value="{rid}"' in t and 'name="industry_context_ack"' in t
    assert 'name="case_ids"' not in t  # no originating case
    assert 'action="/generate"' not in as_user("u1", G).get(f"/research/{rows([])}").text


def test_fallback_to_case_layout_without_industry_layout(rows, approved, tmp_path, monkeypatch):
    from pptx import Presentation
    prs = Presentation(render.MASTER)
    lay = next(l for l in prs.slide_layouts if l.name == render.INDUSTRY_LAYOUT)
    lay.name = "Something else"  # a marketing master without the optional layout
    alt = tmp_path / "m.pptx"
    prs.save(alt)
    monkeypatch.setattr(render, "MASTER", alt)
    rid, cid = rows([claim()]), approved()
    _, out = slides(gen(rid, [cid], "pptx").content)
    ind = out[1]
    assert ind["Outcomes"][0].startswith("learn.microsoft.com states:") and Q1[:40] in ind["Outcomes"][0]
    assert ind["Challenge"] == [""] and ind["Solution"] == [""]
    assert ind["Technology"] == ["Sources: [1] learn.microsoft.com"]


def test_fallback_layout_shows_the_omitted_note(rows, approved, reg, tmp_path, monkeypatch):
    from pptx import Presentation
    prs = Presentation(render.MASTER)
    next(l for l in prs.slide_layouts if l.name == render.INDUSTRY_LAYOUT).name = "Something else"
    alt = tmp_path / "m.pptx"
    prs.save(alt)
    monkeypatch.setattr(render, "MASTER", alt)
    rid = rows([claim(), claim(f"{reg.replace('o', 'ö')} runs this in production daily")])
    assert "omitted" not in slides(gen(rid, [approved()], "pptx").content)[1][1]["Outcomes"][-1]  # #166: replaced, not dropped
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # apply() misses: blocked() alone must still guard
    _, out = slides(gen(rid, [approved()], "pptx").content)
    assert out[1]["Outcomes"][-1] == "1 public statement omitted: it named a protected client"


def test_citation_is_never_rewritten_and_client_site_is_dropped(rows, approved, reg):
    site = reg.lower() + ".com"  # the protected client's own website is the source
    rid, cid = rows([claim(publisher=site, url=f"https://{site}/p"), claim(Q2, "configuration")]), approved()
    md = gen(rid, [cid]).text
    assert site not in md and "a retailer.com" not in md and "1 public statement omitted" in md and Q2 in md


def test_audit_records_the_research_used(rows, approved):
    rid, cid = rows([claim()]), approved()
    gen(rid, [cid], "md")
    with db.connect() as c:
        assert c.execute("select research_id from generations order by id desc limit 1").fetchone()[0] == rid


def test_research_text_never_reaches_cases(rows, approved):
    rid, cid = rows([claim()]), approved()
    gen(rid, [cid], "docx")
    with db.connect() as c:
        data, search_text = c.execute("select data::text, search_text from cases where id=%s", (cid,)).fetchone()
    assert Q1[:30] not in data and Q1[:30] not in search_text
    rid2 = rows([claim()], case_id=cid)
    assert attach(cid, rid2, [0]).status_code == 303
    with db.connect() as c:
        data, search_text = c.execute("select data::text, search_text from cases where id=%s", (cid,)).fetchone()
    assert Q1[:30] not in data and Q1[:30] not in search_text


# ---- public sources attached to a case (#199) ----

RV = ["g-rev", DOCS]


def token(cid):
    with db.connect() as c:
        return c.execute("select md5(public_sources::text) from cases where id=%s", (cid,)).fetchone()[0]


def stored(cid):
    with db.connect() as c:
        return c.execute("select public_sources from cases where id=%s", (cid,)).fetchone()[0]


def attach(cid, rid, n, s=None, user="u1", groups=RV, **extra):
    return as_user(user, groups).post(f"/review/{cid}/sources", follow_redirects=False,
                                      data={"research_id": rid, "n": n, "s": s or token(cid), **extra})


def test_reviewer_attaches_chosen_claims_to_the_case(rows, approved):
    cid = approved()
    rid = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x")], case_id=cid)
    r = attach(cid, rid, [1])
    assert r.status_code == 303 and r.headers["location"] == f"/review/{cid}"
    (item,) = stored(cid)
    assert item["quote"] == Q2 and item["research_id"] == rid and item["attached_by"] == "u1" and item["attached_at"]
    assert "statement" not in item
    page = as_user("u1", RV).get(f"/review/{cid}").text
    assert "Public sources (not delivery evidence)" in page and Q2 in page and Q1 not in page
    assert f'<option value="{cid}:{token(cid)}" selected>' in as_user("u1", RV).get(f"/research/{rid}").text  # the origin case leads
    assert 'formaction="/sources/attach"' not in as_user("u1", G).get(f"/research/{rid}").text  # a plain user cannot attach


def test_attach_refuses_research_not_yours_or_not_done(rows, approved):
    cid = approved()
    for rid in (rows([claim()], by="u2", case_id=cid), rows([claim()], status="running", case_id=cid), 999999999):
        assert attach(cid, rid, [0]).status_code == 404
    assert attach(cid, rows([claim()], case_id=cid), [0], groups=G).status_code == 403
    assert stored(cid) == []


def test_research_from_another_case_or_none_attaches_to_any_case_the_reviewer_may_review(rows, approved):
    cid, other = approved(), approved()
    assert attach(cid, rows([claim()], case_id=other), [0]).status_code == 303
    assert attach(cid, rows([Q2 and claim(Q2, "configuration")]), [0]).status_code == 303
    assert [c["quote"] for c in stored(cid)] == [Q1, Q2] and stored(other) == []


def test_a_case_the_reviewer_cannot_review_gets_404_and_a_stale_version_409(rows, approved):
    hidden, cid = approved(acl=("g-other",)), approved()
    rid = rows([claim()])
    assert attach(hidden, rid, [0], s=token(hidden)).status_code == 404
    assert attach(cid, rid, [0], s="stale").status_code == 409
    assert stored(cid) == [] and stored(hidden) == []


def test_the_picker_posts_case_and_version_together(rows, approved):
    cid = approved()
    rid = rows([claim()])
    c = as_user("u1", RV)
    assert c.post("/sources/attach", data={"target": f"{cid}:{token(cid)}", "research_id": rid, "n": [0]}, follow_redirects=False).status_code == 303
    assert [x["research_id"] for x in stored(cid)] == [rid]
    for bad in ("", "x:y", f"{cid}:stale", f"{cid}"):
        assert c.post("/sources/attach", data={"target": bad, "research_id": rid, "n": [0]}).status_code in (400, 409, 422)
    assert c.post("/sources/attach", data={"target": f"{cid}:{token(cid)}", "n": [0]}).status_code == 400            # neither source
    both = {"target": f"{cid}:{token(cid)}", "research_id": rid, "library_id": 1, "n": [0]}
    assert c.post("/sources/attach", data=both).status_code == 400                                                  # both sources
    assert as_user("u1", G).post("/sources/attach", data={"target": f"{cid}:{token(cid)}", "research_id": rid, "n": [0]}).status_code == 403


def test_a_library_item_attaches_any_users_item_and_records_library_id(approved, env):
    cid = approved()
    quotes = [{"quote": Q1, "publisher": "learn.microsoft.com", "url": "https://learn.microsoft.com/entra", "retrieved_at": "2026-10-01T00:00:00+00:00", "type": "out_of_the_box"},
              {"quote": Q2, "publisher": "docs.vendor.io", "url": "https://docs.vendor.io/x", "retrieved_at": "2026-10-01T00:00:00+00:00", "type": "configuration"}]
    with db.connect() as c:
        lid = c.execute("insert into library_items(topic, kind, body, created_by) values ('t','quotes',%s,'u9') returning id",
                        (json.dumps({"quotes": quotes}),)).fetchone()[0]
    try:
        r = as_user("u1", RV).post(f"/review/{cid}/sources", data={"library_id": lid, "n": [1, 1, 5, -1], "s": token(cid), "quote": "forged"}, follow_redirects=False)
        assert r.status_code == 303
        (item,) = stored(cid)
        assert item["quote"] == Q2 and item["library_id"] == lid and "research_id" not in item and item["attached_by"] == "u1"
        assert as_user("u1", RV).post(f"/review/{cid}/sources", data={"library_id": 999999999, "n": [0], "s": token(cid)}).status_code == 404
    finally:
        with db.connect() as c:
            c.execute("delete from library_items where id=%s", (lid,))


def test_attach_copies_from_the_row_not_the_form(rows, approved):
    cid = approved()
    rid = rows([claim()], case_id=cid)
    attach(cid, rid, [0, 7, -1], quote="forged", url="https://evil.example/x", publisher="forged")
    (item,) = stored(cid)
    assert item["quote"] == Q1 and item["url"] == "https://learn.microsoft.com/entra" and item["publisher"] == "learn.microsoft.com"


def test_attach_skips_a_blocked_claim_and_stores_the_source_text_verbatim(rows, approved, reg):
    cid = approved()
    evasion = f"{reg.replace('o', 'ö')} runs this daily"
    rid = rows([claim(evasion), claim(publisher=reg.lower() + ".com", url=f"https://{reg.lower()}.com/p"),
                claim(Q2, "configuration")], case_id=cid)
    attach(cid, rid, [0, 1, 2])
    got = stored(cid)
    assert [c["quote"] for c in got] == [evasion, Q2] and reg not in json.dumps(got)  # stored as the source says; anonymised at render
    assert "a retailer runs this daily" in gen(None, [cid]).text


def test_attach_dedupes_and_caps_at_20(rows, approved):
    cid = approved()
    rid = rows([claim(f"{Q1} {i}") for i in range(21)], case_id=cid)
    assert attach(cid, rid, [0, 0, 1]).status_code == 303 and attach(cid, rid, [1, 2]).status_code == 303
    assert len(stored(cid)) == 3
    assert attach(cid, rid, list(range(3, 20))).status_code == 303 and len(stored(cid)) == 20
    before = stored(cid)
    assert attach(cid, rid, [20]).status_code == 400 and stored(cid) == before


def test_attach_and_remove_need_the_current_list(rows, approved):
    cid = approved()
    rid = rows([claim(), claim(Q2, "configuration")], case_id=cid)
    stale = token(cid)
    assert attach(cid, rid, [0], s=stale).status_code == 303
    assert attach(cid, rid, [1], s=stale).status_code == 409
    rm = lambda i, s: as_user("u1", RV).post(f"/review/{cid}/sources/remove", data={"i": i, "s": s}, follow_redirects=False)  # noqa: E731
    assert rm(0, stale).status_code == 409 and len(stored(cid)) == 1
    assert rm(5, token(cid)).status_code == 400
    assert rm(0, token(cid)).status_code == 303 and stored(cid) == []


def test_attached_sources_appear_in_every_format(rows, approved):
    cid = approved()
    attach(cid, rows([claim()], case_id=cid), [0])
    md = gen(None, [cid]).text
    assert "# Best practice and public sources" in md and "Industry context" not in md and Q1 in md
    assert render.INDUSTRY_DISCLAIMER.replace("_", "\\_") in md
    t = doc_text(gen(None, [cid], "docx").content)
    assert render.SOURCES_HEADING in t and Q1 in t and "learn.microsoft.com" in t
    _, out = slides(gen(None, [cid], "pptx").content)
    assert out[1]["Title"] == [render.SOURCES_HEADING] and Q1[:40] in out[1]["Statements"][0]
    assert audit()[0] == [cid]
    with db.connect() as c:
        assert c.execute("select research_id from generations order by id desc limit 1").fetchone()[0] is None


def test_attached_and_download_research_are_merged_without_duplicates(rows, approved):
    cid = approved()
    attach(cid, rows([claim()], case_id=cid), [0])
    rid = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x")])
    md = gen(rid, [cid]).text.replace("\\", "")
    assert md.count(Q1) == 1 and Q2 in md and md.index(Q1) < md.index(Q2) and "# Best practice and public sources" in md
    with db.connect() as c:
        assert c.execute("select research_id from generations order by id desc limit 1").fetchone()[0] == rid


def test_research_only_output_keeps_the_industry_heading(rows, approved):
    cid = approved()
    md = gen(rows([claim()]), [cid]).text
    assert "# Industry context (public sources)" in md and "Best practice" not in md


def test_client_protected_after_attach_is_dropped_at_render(rows, approved, reg):
    cid = approved()
    attach(cid, rows([claim(Q2, "configuration")], case_id=cid), [0])
    with db.connect() as c:  # the client joined the registry after the attach
        c.execute("update cases set public_sources = jsonb_build_array(public_sources->0 || jsonb_build_object('quote', %s::text), "
                  "public_sources->0)", (f"{reg} runs this daily",))
    md = gen(None, [cid]).text
    assert reg not in md and "a retailer runs this daily" in md and Q2 in md


def test_attached_sources_survive_reextraction_and_research_deletion(rows, approved):
    cid = approved()
    rid = rows([claim()], case_id=cid)
    attach(cid, rid, [0])
    with db.connect() as c:
        c.execute("delete from research where id=%s", (rid,))
        c.execute("update cases set data = data || '{\"summary\": \"re-extracted\"}'::jsonb where id=%s", (cid,))
    assert [c["quote"] for c in stored(cid)] == [Q1] and Q1 in gen(None, [cid]).text


def test_attach_does_not_reopen_an_approved_case(rows, approved):
    cid = approved()
    with db.connect() as c:
        before = c.execute("select status, review_due, approved_by, data::text, search_text from cases where id=%s", (cid,)).fetchone()
    attach(cid, rows([claim()], case_id=cid), [0])
    as_user("u1", RV).post(f"/review/{cid}/sources/remove", data={"i": 0, "s": token(cid)})
    attach(cid, rows([claim()], case_id=cid), [0])
    with db.connect() as c:
        assert c.execute("select status, review_due, approved_by, data::text, search_text from cases where id=%s", (cid,)).fetchone() == before


def _protect_attached_site(cid, reg):
    with db.connect() as c:  # the source's site now belongs to a protected client: blocked() drops it at render
        c.execute("update cases set public_sources = jsonb_build_array(public_sources->0 || jsonb_build_object('url', %s::text))",
                  (f"https://{reg.lower()}.com/p",))


def test_heading_stays_industry_when_every_attached_claim_is_dropped_at_render(rows, approved, reg):
    cid = approved()
    attach(cid, rows([claim(Q2, "configuration")], case_id=cid), [0])
    _protect_attached_site(cid, reg)
    md = gen(rows([claim()]), [cid]).text
    assert "# Industry context (public sources)" in md and "Best practice" not in md and Q1 in md and Q2 not in md


def test_review_page_lists_a_hidden_source_for_removal_without_showing_it(rows, approved, reg):
    cid = approved()
    attach(cid, rows([claim(Q2, "configuration")], case_id=cid), [0])
    _protect_attached_site(cid, reg)
    page = as_user("u1", RV).get(f"/review/{cid}").text
    assert "Withheld: names a protected client" in page and Q2 not in page and reg.lower() not in page
    assert 'name="i" value="0"' in page


# ---- grouped layout (#214): groups, citations, sources, titles ----

def section(claims, pages=()):
    return render.industry_section(claims, render.anonymise.load_clients(), pages=pages)


def test_section_groups_by_page_and_numbers_sources(env):
    db.init()
    a = {**claim(), "title": "Entra overview"}
    b = {**claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x"), "title": "Vendor docs"}
    s = section([a, b, {**claim("Second quote from the first page"), "title": "Entra overview"}])
    assert [g["title"] for g in s["groups"]] == ["Entra overview", "Vendor docs"]
    assert [q["n"] for q in s["groups"][0]["quotes"]] == [1, 1] and s["groups"][1]["quotes"][0]["n"] == 2
    assert s["groups"][0]["lead"] == "learn.microsoft.com states:"
    assert [(x["n"], x["url"]) for x in s["sources"]] == [(1, "https://learn.microsoft.com/entra"), (2, "https://docs.vendor.io/x")]
    assert not any("http" in x["text"] for x in s["statements"][:-1]) and "https://docs.vendor.io/x" in s["statements"][-1]["text"]


def test_section_title_falls_back_to_the_page_then_the_host(env):
    db.init()
    pages = [{"url": "https://learn.microsoft.com/entra", "title": "From the page"}]
    assert section([claim()], pages)["groups"][0]["title"] == "From the page"
    assert section([claim()])["groups"][0]["title"] == "learn.microsoft.com"


def test_a_title_naming_a_protected_client_never_reaches_the_section(reg, monkeypatch):
    name = reg
    s = section([{**claim(), "title": f"{name} case study"}])
    assert name not in json.dumps(s) and s["groups"][0]["title"] == "a retailer case study"
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # apply() misses it: blocked() must still drop the claim
    s = section([{**claim(), "title": f"{name} case study"}, claim(Q2, url="https://x.example/y")])
    assert name not in json.dumps(s) and "omitted" in s["note"] and len(s["groups"]) == 1
    assert s["groups"][0]["quotes"][0]["n"] == 1 and s["sources"][0]["n"] == 1  # numbering counts only what is shown


def test_two_quotes_from_one_page_share_a_citation_in_markdown(rows, approved):
    t = gen(rows([claim(), claim(Q2, "configuration")]), [approved()], ack=True).text.replace("\\", "")
    assert f"> “{Q1}” [1]" in t and f"> “{Q2}” [1]" in t and t.count("<https://learn.microsoft.com/entra>") == 1


def test_markdown_autolink_only_for_a_plain_url(rows):
    evil = "https://x/a>[c](javascript:alert(1))"
    t = gen(rows([claim(url=evil)]), ack=True).text
    assert "<https://x/a>" not in t and not re.search(r"(?<!\\)\]\(javascript", t) and not re.search(r"(?<!\\)<https?://x", t)
    assert "<https://learn.microsoft.com/entra>" in gen(rows([claim()]), ack=True).text  # a plain URL still links


def test_group_title_comes_from_the_stored_page_when_the_claim_has_none(rows):
    pages = [{"url": "https://learn.microsoft.com/entra", "title": "Passwordless overview", "publisher": "learn.microsoft.com"}]
    t = gen(rows([claim()], pages=pages), ack=True).text
    assert "## Passwordless overview" in t and "1. Passwordless overview, learn.microsoft.com" in t


def test_all_claims_omitted_leaves_the_note_and_no_sources_heading(reg, monkeypatch):
    s = section([claim(publisher=f"{reg.lower()}.example", url=f"https://{reg.lower()}.example/x")])
    assert s["sources"] == [] and s["groups"] == [] and "omitted" in s["note"]
    assert "Sources" not in render.to_markdown([], s) and "omitted" in render.to_markdown([], s)
    assert "Sources" not in doc_text(render.to_docx([], s)) and "omitted" in doc_text(render.to_docx([], s))


def test_a_malformed_url_does_not_break_the_section(env):
    db.init()
    s = section([claim(url="http://[x")])
    assert s["groups"][0]["title"] == "learn.microsoft.com"


# ---- the case study's "Approach and industry practice" passage (#214) ----

def test_case_study_puts_the_attached_sources_after_our_sections_under_the_disclaimer(rows, approved):
    cid = approved()
    attach(cid, rows([claim(), claim(Q2, "configuration")], case_id=cid), [0, 1])
    t = doc_text(gen(None, [cid], "case_study_docx").content)
    study, footer, head, disc = (t.index(x) for x in ("Faster onboarding", "Based on 1 source document", render.APPROACH_HEADING, render.INDUSTRY_DISCLAIMER))
    assert study < footer < head < disc                                              # delivery first, then the passage and its disclaimer
    after = t[head:]
    assert f"{Q1}” [1]" in after and f"{Q2}” [1]" in after and "Sources" in after and "[1] learn.microsoft.com" in after
    assert render.SOURCES_HEADING not in t and "Challenge" not in after              # never inside the delivery sections
    assert render.APPROACH_HEADING not in doc_text(gen(None, [cid], "docx").content)  # the other documents keep their heading
    pdf = " ".join(pdf_text(gen(None, [cid], "case_study_pdf").content)[0].split())
    assert render.APPROACH_HEADING in pdf and Q1 in pdf


def test_case_study_without_attached_sources_has_no_passage(rows, approved):
    cid = approved()
    t = doc_text(gen(None, [cid], "case_study_docx").content)
    assert render.APPROACH_HEADING not in t and render.INDUSTRY_DISCLAIMER not in t
    rid = rows([claim()])
    t = doc_text(gen(rid, [cid], "case_study_docx").content)                          # research alone is industry context, not the approach
    assert render.INDUSTRY_HEADING in t and render.APPROACH_HEADING not in t


def test_case_study_passage_is_anonymised_and_dropped_when_blocked(rows, approved, reg, monkeypatch):
    cid = approved()
    attach(cid, rows([claim(f"{reg} runs this daily in production"), claim(Q2, "configuration")], case_id=cid), [0, 1])
    t = doc_text(gen(None, [cid], "case_study_docx").content)
    assert reg not in t and "a retailer runs this daily in production" in t
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)                   # apply() misses: blocked() alone must still guard
    t = doc_text(gen(None, [cid], "case_study_docx").content)
    assert reg not in t and Q2 in t and "1 public statement omitted" in t


def test_preview_says_a_case_study_retitles_the_attached_sources(rows, approved):
    cid = approved()
    def note(c):
        r = as_user("u1", G).post("/preview", data={"case_ids": [c]})
        return html.unescape(re.search(r"Case study downloads also end with:[^<]*", r.text)[0])
    assert "Approach and industry practice" not in note(cid)
    attach(cid, rows([claim()], case_id=cid), [0])
    assert note(cid).endswith(', and title the public sources section “Approach and industry practice”')


# ---- the case picker and library attach (#214 phase 5 review) ----

LIB_QUOTES = [{"quote": Q1, "publisher": "learn.microsoft.com", "url": "https://learn.microsoft.com/entra", "retrieved_at": "2026-10-01T00:00:00+00:00", "type": "out_of_the_box"},
              {"quote": Q2, "publisher": "docs.vendor.io", "url": "https://docs.vendor.io/x", "retrieved_at": "2026-10-01T00:00:00+00:00", "type": "configuration"}]


@pytest.fixture
def lib_item(env):
    made = []

    def add(kind="quotes", quotes=LIB_QUOTES, brief=None):
        with db.connect() as c:
            made.append(c.execute("insert into library_items(topic, kind, body, created_by) values ('t',%s,%s,'u9') returning id",
                                  (kind, json.dumps({"quotes": quotes, **({"brief": brief} if brief else {})}))).fetchone()[0])
        return made[-1]
    yield add
    with db.connect() as c:
        c.execute("delete from library_items where id = any(%s)", (made,))


def test_the_picker_lists_only_cases_the_reviewer_may_review(rows, approved, lib_item):
    hidden, shown = approved(acl=("g-other",)), approved()
    rid, lid = rows([claim()]), lib_item()
    c = as_user("u1", RV)
    for url in (f"/research/{rid}", f"/library/{lid}"):
        t = c.get(url).text
        assert f'value="{shown}:' in t and f'value="{hidden}:' not in t, url
        assert '<option value="" selected>Choose a case' in t, url
    assert 'name="target"' not in as_user("u1", G).get(f"/research/{rid}").text


def test_the_picker_defaults_to_choose_a_case_unless_the_research_started_from_a_listed_case(rows, approved):
    cid = approved()
    t = as_user("u1", RV).get(f"/research/{rows([claim()])}").text
    assert '<option value="" selected>Choose a case' in t
    t = as_user("u1", RV).get(f"/research/{rows([claim()], case_id=cid)}").text
    assert '<option value="">Choose a case' in t and f'<option value="{cid}:{token(cid)}" selected>' in t


def test_a_library_brief_item_form_attaches_exactly_its_sources(approved, lib_item):
    cid = approved()
    brief = {"title": "Entra", "takeaways": ["Entra supports passwordless sign in [1][2]."], "themes": []}
    lid = lib_item("brief", brief=brief)
    c = as_user("u1", RV)
    t = c.get(f"/library/{lid}").text
    form = t[t.index('<form id="attach"'):]
    form = form[:form.index("</form>")]
    target = re.search(rf'<option value="({cid}:[0-9a-f]+)"', t)[1]
    ns = [int(x) for x in re.findall(r'name="n" value="(\d+)" form="attach"', t)]
    assert ns == [0, 1] and 'name="library_id"' in form
    r = c.post("/sources/attach", data={"target": target, "library_id": lid, "n": ns}, follow_redirects=False)
    assert r.status_code == 303
    assert [x["quote"] for x in stored(cid)] == [Q1, Q2] and all(x["library_id"] == lid for x in stored(cid))


def test_a_library_quote_naming_a_protected_client_is_never_stored_with_the_name(approved, lib_item, reg, monkeypatch):
    cid = approved()
    lid = lib_item(quotes=[{**LIB_QUOTES[0], "quote": f"{reg} runs passwordless sign in out of the box"}, LIB_QUOTES[1]])
    c = as_user("u1", RV)
    assert c.post(f"/review/{cid}/sources", data={"library_id": lid, "n": [0, 1], "s": token(cid)}).status_code in (200, 303)
    got = stored(cid)
    assert reg not in json.dumps(got) and [x["quote"] for x in got][0].startswith("a retailer runs") and len(got) == 2   # replaced
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)                                                    # apply() misses: skipped
    cid2 = approved()
    assert c.post(f"/review/{cid2}/sources", data={"library_id": lid, "n": [0, 1], "s": token(cid2)}).status_code in (200, 303)
    got = stored(cid2)
    assert reg not in json.dumps(got) and [x["quote"] for x in got] == [Q2]


def test_the_target_must_be_a_plain_ascii_id(approved):
    cid = approved()
    c = as_user("u1", RV)
    for bad in ("²:x", f"{'9' * 25}:{token(cid)}", "٣:x", "", f"-{cid}:x"):
        assert c.post("/sources/attach", data={"target": bad, "research_id": 1, "n": [0]}).status_code == 400, bad


def test_the_approach_heading_only_when_the_section_is_purely_the_attached_sources(rows, approved):
    cid = approved()
    attach(cid, rows([claim()], case_id=cid), [0])
    assert render.APPROACH_HEADING in doc_text(gen(None, [cid], "case_study_docx").content)
    same = rows([claim()])                                       # the same quote again: still purely the attached sources
    assert render.APPROACH_HEADING in doc_text(gen(same, [cid], "case_study_docx").content)
    extra = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x")])
    t = doc_text(gen(extra, [cid], "case_study_docx").content)   # ad-hoc research merged in
    assert render.APPROACH_HEADING not in t and render.SOURCES_HEADING in t and Q2 in t


MD = "**Entra** supports [passwordless](https://x.io/a) sign in `out` of the box"


def test_markdown_quote_is_plain_in_every_output_and_the_stored_row_stays_raw(rows, approved):
    rid = rows([claim(MD)])
    r = gen(rid, [approved()], ack=True)
    assert "Entra supports passwordless sign in out of the box" in r.text
    assert not any(x in r.text for x in ("](", "**Entra", "`out`"))
    d = doc_text(gen(rid, [approved()], "docx", ack=True).content)
    assert "Entra supports passwordless sign in out of the box" in d and "](" not in d and "**Entra" not in d
    with db.connect() as c:
        stored = c.execute("select results->'claims'->0->>'quote' from research where id=%s", (rid,)).fetchone()[0]
    assert stored == MD
    from app.schema import quote_in
    assert quote_in(MD, stored)


def test_a_client_name_split_by_markup_is_caught_on_the_page_and_in_a_download(rows, approved, reg):
    split = f"**{reg[:4]}**{reg[4:]} runs this in production daily"
    rid = rows([claim(split)])
    page_ = as_user("u1", G).get(f"/research/{rid}").text
    assert reg not in page_ and "**" not in page_ and "a retailer runs this in production daily" in page_
    dl = gen(rid, [approved()], ack=True).text
    assert reg not in dl and "a retailer runs this in production daily" in dl


def test_preview_shows_a_markdown_quote_as_plain_text(rows, approved):
    r = as_user("u1", G).post("/preview", data={"case_ids": [approved()], "research_id": rows([claim(MD)])})
    assert "Entra supports passwordless sign in out of the box" in r.text and "](" not in r.text and "**Entra" not in r.text
