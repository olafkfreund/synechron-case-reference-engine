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


@pytest.fixture
def rows(env):  # noqa: F811
    db.init()
    made = []

    def add(claims, by="u1", status="done"):
        with db.connect() as c:
            made.append(c.execute("insert into research(query, created_by, status, results) values ('q',%s,%s,%s) returning id",
                                  (by, status, json.dumps({"pages": [], "skipped": [], "claims": claims}))).fetchone()[0])
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
    for phrase in render.PHRASES.values():
        assert phrase in sec
    assert f"“{Q1}” — learn.microsoft.com, https://learn.microsoft.com/entra, retrieved 2026-10-01" in sec.replace("\\", "")
    assert "AI summary" not in t
    assert not re.search(r"\b(we|our|ours|delivered)\b", sec.replace(Q1, ""), re.I), sec  # fixed wording only


def test_every_fixed_string_avoids_first_person_and_delivery_wording():
    fixed = [render.INDUSTRY_HEADING, render.INDUSTRY_DISCLAIMER, *render.PHRASES.values(), "retrieved"]
    assert not [s for s in fixed if re.search(r"\b(we|our|ours|us|delivered)\b", s, re.I)]
    assert render.COMPANY in render.INDUSTRY_DISCLAIMER


def test_docx_and_pdf_have_the_quote_and_source(rows, approved):
    rid, cid = rows([claim()]), approved()
    t = doc_text(gen(rid, [cid], "docx").content)
    for want in (render.INDUSTRY_HEADING, render.INDUSTRY_DISCLAIMER, "Supported out of the box", Q1, "learn.microsoft.com", "retrieved 2026-10-01", "Faster onboarding"):
        assert want in t, want
    text = " ".join(pdf_text(gen(rid, [cid], "pdf_docx").content)[0].split())  # the PDF wraps lines
    assert render.INDUSTRY_HEADING in text and Q1 in text and "learn.microsoft.com" in text
    assert "{{" not in t and "{%" not in t


def test_pptx_industry_slide_keeps_the_quote_next_to_the_phrase(rows, approved):
    rid, cid = rows([claim(), claim(Q2, "configuration", "docs.vendor.io", "https://docs.vendor.io/x")]), approved()
    prs, out = slides(gen(rid, [cid], "pptx").content)
    assert len(out) == 2 and prs.slides[1].slide_layout.name == render.INDUSTRY_LAYOUT
    ind = out[1]
    assert ind["Title"] == [render.INDUSTRY_HEADING] and ind["Summary"] == [render.INDUSTRY_DISCLAIMER]
    assert len(ind["Statements"]) == 2
    for line, phrase, quote in zip(ind["Statements"], ("Supported out of the box", "Available through configuration"), (Q1, Q2)):
        assert line.startswith(phrase) and quote[:40] in line  # the AI-chosen phrase never stands alone
    assert len(slides(gen(rid, [], "pptx", ack=True).content)[1]) == 1  # industry only
    assert gen(rid, [cid], "pdf_pptx").content.startswith(b"%PDF")


def test_a_claim_naming_a_protected_client_is_replaced_or_dropped_and_counted(rows, approved, reg):
    name = reg
    evasion = name.replace("o", "ö")
    rid = rows([claim(f"{name} runs passwordless sign in out of the box"), claim(f"{evasion} runs this in production daily"),
                claim(Q2, "configuration")])
    r = gen(rid, [approved()], ack=True)
    assert r.status_code == 200 and "Faster onboarding" in r.text                 # the case output is still produced
    t = r.text
    assert name not in t and evasion not in t and "a retailer runs passwordless" in t and Q2 in t
    assert "1 public statement omitted: it named a protected client" in t
    only = gen(rows([claim(f"{evasion} runs this in production daily")]), ack=True)
    assert only.status_code == 400                                                  # nothing usable is left


def test_pptx_industry_slide_shows_the_omitted_note(rows, approved, reg):
    rid = rows([claim(), claim(f"{reg.replace('o', 'ö')} runs this in production daily")])
    _, out = slides(gen(rid, [approved()], "pptx").content)
    st = out[1]["Statements"]
    assert st[0].startswith("Supported out of the box") and st[-1] == "1 public statement omitted: it named a protected client"
    many = rows([claim(f"{Q1} {i}") for i in range(render.SLIDE_ITEMS + 1)] + [claim(f"{reg.replace('o', 'ö')} runs this daily")])
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
    assert ind["Outcomes"][0].startswith("Supported out of the box") and Q1[:40] in ind["Outcomes"][0]
    assert ind["Challenge"] == [""] and ind["Solution"] == [""]


def test_fallback_layout_shows_the_omitted_note(rows, approved, reg, tmp_path, monkeypatch):
    from pptx import Presentation
    prs = Presentation(render.MASTER)
    next(l for l in prs.slide_layouts if l.name == render.INDUSTRY_LAYOUT).name = "Something else"
    alt = tmp_path / "m.pptx"
    prs.save(alt)
    monkeypatch.setattr(render, "MASTER", alt)
    rid = rows([claim(), claim(f"{reg.replace('o', 'ö')} runs this in production daily")])
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
