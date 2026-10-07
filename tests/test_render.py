import uuid
import zipfile
from io import BytesIO

import pytest
from docx import Document

from app import db, render
from app.schema import Outcome, Sourced
from tests.test_auth import client, env  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

R = ["g-user", DOCS]


@pytest.fixture
def reg(env):  # noqa: F811
    db.init()
    tag = uuid.uuid4().hex[:6]
    made = []

    def add(name, label, referenceable=False):
        with db.connect() as c:
            made.append(c.execute(
                "insert into clients(name, anonymised_label, referenceable) values (%s,%s,%s) returning id",
                (f"{name}{tag}", label, referenceable)).fetchone()[0])
        return f"{name}{tag}", made[-1]
    yield add
    with db.connect() as c:
        c.execute("update cases set client_id = null where client_id = any(%s)", (made,))
        c.execute("delete from clients where id = any(%s)", (made,))


def link(cid, client_id):
    with db.connect() as c:
        c.execute("update cases set client_id=%s where id=%s", (client_id, cid))


def gen(ids, fmt="md", groups=R, **kw):
    return client(groups).post("/generate", data={"case_ids": ids, "format": fmt}, **kw)


def doc_text(content):
    d = Document(BytesIO(content))
    parts = [p.text for p in d.paragraphs] + [p.text for p in d.sections[0].header.paragraphs]
    return "\n".join(parts)


def full(**kw):
    c = data(challenge=Sourced[str](value="Slow manual KYC & checks", source_quote="Hidden quote text here"),
             organisations=["Secret Org Ltd"], needs_attention=["Hidden attention note"], **kw)
    return c.model_copy(update={"duration_months": Sourced[int](value=18, source_quote="q")})


def test_docx_content_and_leaks(approved, reg):
    _, label_id = reg("Zorp", "a UK retailer")
    cid = approved(full())
    link(cid, label_id)
    r = gen([cid], "docx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    t = doc_text(r.content)
    for want in ("Faster onboarding", "a UK retailer", "Slow manual KYC & checks", "18 months", "Kubernetes",
                 "onboarding: 12 to 3 days", "Onboarding fell from 12 days", "DRAFT TEMPLATE"):
        assert want in t, want
    for bad in ("{{", "{%", "Hidden quote", "Secret Org", "Hidden attention"):
        assert bad not in t, bad


def test_markdown_fields_and_escaping(approved):
    cid = approved(full(title="A *bold* [title]\n# x"))
    t = gen([cid]).text
    assert "# A \\*bold\\* \\[title\\] \\# x" in t and "- **Industry:** Banking" in t and "- Kubernetes" in t
    assert "Hidden quote" not in t and "Secret Org" not in t


def test_client_display_rules(approved, reg):
    ref_name, ref_id = reg("Globex", "a manufacturer", True)
    _, anon_id = reg("Initech", "a software firm")
    a, b, c = approved(), approved(), approved()
    link(a, ref_id)
    link(b, anon_id)
    t = gen([a, b, c]).text
    assert f"*{ref_name}*" in t and "*a software firm*" in t and "*a client*" in t
    with db.connect() as conn:
        row = conn.execute("select user_id, format, case_ids, anonymised from generations order by id desc limit 1").fetchone()
    assert row == ("u1", "md", [a, b, c], True)
    gen([a])
    with db.connect() as conn:
        assert conn.execute("select anonymised from generations order by id desc limit 1").fetchone()[0] is False


def test_protected_name_replaced_or_withheld(approved, reg):
    name, cid_client = reg("Zorp", "a UK retailer")
    ok = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name} in 2024", source_quote="q")}))
    t = gen([ok]).text
    assert name not in t and "Built for a UK retailer in 2024" in t
    evasion = name.replace("o", "ö")  # apply() misses the accent, blocked() folds it
    bad = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {evasion}", source_quote="q")}))
    r = gen([bad])
    assert r.status_code == 409 and r.json()["detail"] == render.WITHHELD and name not in r.text
    with db.connect() as conn:
        assert conn.execute("select count(*) from generations where %s = any(case_ids)", (bad,)).fetchone()[0] == 0


def test_visibility_and_validation(approved):
    ok = approved()
    assert gen([approved(acl=("g-other",))]).status_code == 404
    assert gen([approved(due="-1 day")]).status_code == 404
    assert gen([ok, 999999999]).status_code == 404
    assert gen([approved(status="extracted", due=None)]).status_code == 404
    assert gen([ok], "pdf").status_code == 400
    assert gen([ok, ok, ok, ok]).status_code == 200  # duplicates collapse
    assert gen([ok, approved(), approved(), approved()]).status_code == 400


def test_post_without_origin_refused(approved):
    cid = approved()
    r = client(R, origin=None).post("/generate", data={"case_ids": [cid], "format": "md"})
    assert r.status_code == 403


def test_docx_metadata_is_neutral(approved):
    props = Document(BytesIO(gen([approved()], "docx").content)).core_properties
    assert (props.title, props.author, props.comments) == ("Reference cases", "Reference Engine", "")


def test_markdown_values_cannot_create_structure():
    for raw in ("# Heading", "- # x", "2) item", "+ plus", "| a | b |", "~~strike~~"):
        out = render.md(raw)
        assert not out.startswith(("#", "-", "+", "2)", "|", "~")), (raw, out)


from pptx import Presentation  # noqa: E402

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
NAMES = ("Title", "Client", "Summary", "Challenge", "Solution", "Outcomes", "Technology")


def slides(content):
    """[{placeholder name: [paragraph texts]}] per slide, names taken from the slide's layout."""
    prs = Presentation(BytesIO(content))
    out = []
    for s in prs.slides:
        names = {p.placeholder_format.idx: p.name for p in s.slide_layout.placeholders}
        out.append({names[p.placeholder_format.idx]: [x.text for x in p.text_frame.paragraphs] for p in s.placeholders})
    return prs, out


def test_pptx_fills_named_placeholders(approved, reg):
    _, cid_client = reg("Zorp", "a UK retailer")
    a = approved(full().model_copy(update={"solution": Sourced[str](value="Cloud KYC", source_quote="q")}))
    b = approved(data(title="Second case"))
    link(a, cid_client)
    r = gen([a, b], "pptx")
    assert r.status_code == 200 and r.headers["content-type"] == PPTX
    assert r.headers["content-disposition"].endswith('reference-cases.pptx"')
    prs, out = slides(r.content)
    assert len(out) == 2 and all(set(s) == set(NAMES) for s in out)
    first = out[0]
    assert first["Title"] == ["Faster onboarding"] and first["Client"] == ["a UK retailer"]
    assert first["Summary"] == ["Onboarding fell from 12 days to 3 days."]
    assert first["Challenge"] == ["Slow manual KYC & checks"] and first["Solution"] == ["Cloud KYC"]
    assert first["Outcomes"] == ["onboarding: 12 to 3 days"] and first["Technology"] == ["Kubernetes"]
    assert out[1]["Solution"] == [""] and out[1]["Client"] == ["a client"]  # no content: empty, not prompt text
    everything = "\n".join(t for s in out for ps in s.values() for t in ps)
    for bad in ("Click to add", "Hidden quote", "Secret Org", "Hidden attention"):
        assert bad not in everything, bad
    cp = prs.core_properties
    assert (cp.title, cp.author, cp.last_modified_by, cp.comments) == ("Reference cases", "Reference Engine", "Reference Engine", "")


def test_pptx_slide_shaping(approved):
    long = "word " * 120
    c = data(tech=("Kubernetes", "Kafka", "Terraform"), challenge=Sourced[str](value=long.strip(), source_quote="q"))
    c = c.model_copy(update={"outcomes": [Outcome(metric=f"m{i}", value=f"{i}", source_quote="q") for i in range(7)]})
    _, out = slides(gen([approved(c)], "pptx").content)
    assert out[0]["Technology"] == ["Kubernetes, Kafka, Terraform"]  # one line
    assert len(out[0]["Outcomes"]) == render.SLIDE_OUTCOMES
    assert out[0]["Challenge"][0].endswith(" …") and len(out[0]["Challenge"][0]) <= render.SLIDE_TEXT + 2


def test_pptx_protected_name_withheld(approved, reg):
    name, _ = reg("Zorp", "a UK retailer")
    bad = approved(full().model_copy(update={"challenge": Sourced[str](value=name.replace("o", "ö"), source_quote="q")}))
    r = gen([bad], "pptx")
    assert r.status_code == 409 and r.json()["detail"] == render.WITHHELD


def test_pptx_missing_placeholder_or_layout_fails_loudly(tmp_path, monkeypatch):
    prs = Presentation(render.MASTER)
    layout = next(l for l in prs.slide_layouts if l.name == "Reference case")
    next(p for p in layout.placeholders if p.name == "Solution").name = "Soluton"
    broken = tmp_path / "broken.pptx"
    prs.save(broken)
    monkeypatch.setattr(render, "MASTER", broken)
    with pytest.raises(ValueError, match="missing placeholders: Solution"):
        render.to_pptx([])
    layout.name = "Other"
    prs.save(broken)
    with pytest.raises(ValueError, match="no slide layout named 'Reference case'"):
        render.to_pptx([])


def test_master_template_is_marked_draft():
    prs = Presentation(render.MASTER)
    layout = next(l for l in prs.slide_layouts if l.name == "Reference case")
    assert any("DRAFT TEMPLATE" in s.text_frame.text for s in layout.shapes if s.has_text_frame)


def test_control_character_cannot_rebuild_a_protected_name(approved, reg):
    name, _ = reg("Zorp", "a UK retailer")
    sneaky = name[:2] + "\x01" + name[2:]  # stripped after the check, this used to become the name again
    cid = approved(full().model_copy(update={"challenge": Sourced[str](value=sneaky, source_quote="q")}))
    for fmt in ("md", "docx", "pptx"):
        r = gen([cid], fmt)
        assert r.status_code == 200, fmt  # stripped first, then replaced by the label
        body = r.content if fmt == "md" else b"".join(
            z.read(n) for z in [zipfile.ZipFile(BytesIO(r.content))] for n in z.namelist())
        assert name.encode() not in body and b"a UK retailer" in body, fmt


def test_master_layout_has_unique_idx_and_no_slides():
    prs = Presentation(render.MASTER)
    layout = next(l for l in prs.slide_layouts if l.name == "Reference case")
    idx = [p.placeholder_format.idx for p in layout.placeholders]
    assert len(idx) == len(set(idx)) and len(prs.slides) == 0
    assert {p.name for p in layout.placeholders} == set(NAMES)


def test_extra_layout_placeholder_is_removed_and_sample_slides_refused(tmp_path, monkeypatch, approved):
    import copy
    prs = Presentation(render.MASTER)
    layout = next(l for l in prs.slide_layouts if l.name == "Reference case")
    extra = copy.deepcopy(next(p for p in layout.placeholders if p.name == "Summary")._element)
    extra.nvSpPr.cNvPr.set("name", "Logo")
    extra.nvSpPr.nvPr.ph.set("idx", "99")
    layout.shapes._spTree.append(extra)
    extended = tmp_path / "extended.pptx"
    prs.save(extended)
    monkeypatch.setattr(render, "MASTER", extended)
    out = Presentation(BytesIO(render.to_pptx([render.section(data(), "a client")])))
    assert len(list(out.slides[0].placeholders)) == len(NAMES)  # the logo placeholder was removed
    prs.slides.add_slide(layout)  # a master that ships its own sample slide
    prs.save(extended)
    with pytest.raises(ValueError, match="no slides"):
        render.to_pptx([])


def test_broken_template_writes_no_audit_row(approved, monkeypatch):
    cid = approved()
    monkeypatch.setattr(render, "MASTER", render.MASTER.with_name("missing.pptx"))
    with db.connect() as c:
        before = c.execute("select count(*) from generations").fetchone()[0]
    with pytest.raises(Exception):
        gen([cid], "pptx")
    with db.connect() as c:
        assert c.execute("select count(*) from generations").fetchone()[0] == before


import threading  # noqa: E402

import pypdfium2 as pdfium  # noqa: E402


def pdf_text(content):
    pdf = pdfium.PdfDocument(content)
    return "\n".join(p.get_textpage().get_text_range() for p in pdf), pdf.get_metadata_dict()


@pytest.mark.parametrize("fmt", ["pdf_docx", "pdf_pptx"])
def test_pdf_from_route_has_label_not_name(approved, reg, fmt):
    name, cid_client = reg("Zorp", "a UK retailer")
    c = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name} in 2024", source_quote="q")}))
    link(c, cid_client)
    r = gen([c], fmt)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].endswith('reference-cases.pdf"') and r.content.startswith(b"%PDF")
    text, meta = pdf_text(r.content)
    assert "Faster onboarding" in text and "a UK retailer" in text and name not in text
    assert name not in " ".join(meta.values()) and "Secret Org" not in text and "Hidden quote" not in text
    assert meta.get("Author") == "Reference Engine"


def test_pdf_failures_are_clear_and_leak_nothing(monkeypatch):
    docx = render.to_docx([])
    monkeypatch.setattr(render, "PDF_TIMEOUT", 0.01)
    with pytest.raises(render.PdfError, match="timed out"):
        render.to_pdf(docx, ".docx")
    monkeypatch.setattr(render, "PDF_TIMEOUT", 60)
    monkeypatch.setattr(render, "SOFFICE", "false")
    with pytest.raises(render.PdfError, match=r"failed \(exit 1\)"):
        render.to_pdf(docx, ".docx")
    monkeypatch.setattr(render, "SOFFICE", "true")  # exits 0 but writes nothing
    with pytest.raises(render.PdfError, match="failed"):
        render.to_pdf(docx, ".docx")


def test_pdf_route_reports_503_and_no_audit_row(approved, monkeypatch):
    cid = approved()
    monkeypatch.setattr(render, "SOFFICE", "false")
    r = gen([cid], "pdf_docx")
    assert r.status_code == 503 and "PDF conversion failed" in r.json()["detail"]
    with db.connect() as conn:
        assert conn.execute("select count(*) from generations where %s = any(case_ids)", (cid,)).fetchone()[0] == 0


def test_parallel_conversions_do_not_collide():
    docx = render.to_docx([])
    results, errors = [], []

    def run():
        try:
            results.append(render.to_pdf(docx, ".docx"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=run) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len(results) == 3 and all(r.startswith(b"%PDF") for r in results)


def test_pdf_conversion_does_not_fetch_linked_content():
    """A marketing template could link images by URL; soffice must not fetch them."""
    import http.server
    import threading as th
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/linked.png"
    d = Document()
    d.add_paragraph("before")
    # a linked (not embedded) picture, as Word writes it: external relationship + r:link on the blip
    rid = d.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", is_external=True)
    from docx.oxml import parse_xml
    d.paragraphs[0]._p.append(parse_xml(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:drawing>'
        '<wp:inline><wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="linked"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        '<pic:nvPicPr><pic:cNvPr id="1" name="linked"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:link="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r>'))
    buf = BytesIO()
    d.save(buf)
    try:
        assert render.to_pdf(buf.getvalue(), ".docx").startswith(b"%PDF")
    finally:
        srv.shutdown()
    assert hits == []


def test_pdf_busy_when_slots_are_taken(monkeypatch):
    monkeypatch.setattr(render, "PDF_WAIT", 0.1)
    held = [render.PDF_SLOTS.acquire() for _ in range(2)]
    try:
        with pytest.raises(render.PdfError, match="busy"):
            render.to_pdf(b"x", ".docx")
    finally:
        for _ in held:
            render.PDF_SLOTS.release()
