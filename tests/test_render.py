import html
import re
import zipfile
from io import BytesIO

import pytest
from docx import Document

from app import db, render
from app.schema import Outcome, Sourced
from tests.test_auth import client, env  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401
from tests.test_search import approved, data, link, reg  # noqa: F401

R = ["g-user", DOCS]


def gen(ids, fmt="md", groups=R, **kw):
    return client(groups).post("/generate", data={"case_ids": ids, "format": fmt}, **kw)


def prev(ids, groups=R, **kw):
    return client(groups).post("/preview", data={"case_ids": ids, "format": "md"}, **kw)  # format is ignored


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


def add_diagram(cid, caption="System overview", size=(40, 20), status="ready"):
    import json
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", size, "blue").save(buf, "PNG")
    with db.connect() as c:
        did = c.execute("insert into diagrams(case_id, png, created_by) values (%s,%s,'t') returning id",
                        (cid, buf.getvalue())).fetchone()[0]
        entry = {"id": did, "caption": caption, "status": status, "reason": "", "width": size[0], "height": size[1]}
        c.execute("update cases set data = data || jsonb_build_object('diagrams', coalesce(data->'diagrams', '[]') || %s::jsonb) "
                  "where id=%s", (json.dumps([entry]), cid))
    return did


def test_only_ready_diagrams_reach_outputs(approved):
    cid = approved(full())
    add_diagram(cid, "Ready one")
    add_diagram(cid, "Pending one", status="pending")  # all three have bytes, so only the status keeps them out
    add_diagram(cid, "Rejected one", status="rejected")
    out = {"md": gen([cid], "md").text, "preview": prev([cid]).text, "docx": doc_text(gen([cid], "docx").content)}
    for name, text in out.items():
        assert "Ready one" in text and "Pending one" not in text and "Rejected one" not in text, name
    assert out["preview"].count("<figure>") == 1
    xml = zipfile.ZipFile(BytesIO(gen([cid], "docx").content)).read("word/document.xml").decode()
    assert xml.count("<w:drawing>") == 1


def test_docx_embeds_diagram_with_caption(approved):
    cid = approved(full())
    add_diagram(cid, "System overview", (2000, 1000))  # wider than 160 mm at 96 dpi: scaled down
    r = gen([cid], "docx")
    z = zipfile.ZipFile(BytesIO(r.content))
    xml = z.read("word/document.xml").decode()
    assert "<w:drawing>" in xml and [n for n in z.namelist() if n.startswith("word/media/") and n.endswith(".png")]
    assert 'cx="5760000"' in xml  # 160 mm
    assert "System overview" in doc_text(r.content) and "{{" not in xml
    assert "<w:drawing>" not in zipfile.ZipFile(BytesIO(gen([approved(full())], "docx").content)).read("word/document.xml").decode()


def test_markdown_lists_diagram_captions(approved):
    cid = approved(full())
    assert "## Diagrams" not in gen([cid], "md").text
    add_diagram(cid, "# Big *flow*")
    m = gen([cid], "md").text
    assert "## Diagrams\n\n- \\# Big \\*flow\\* (in the Word, PowerPoint and PDF versions)\n" in m


def test_caption_is_anonymised_and_withheld_fail_closed(approved, reg, monkeypatch):
    name, label_id = reg("Zorp", "a UK retailer")
    cid = approved(full())
    link(cid, label_id)
    add_diagram(cid, f"Flow at {name}")
    m = gen([cid], "md").text
    assert name not in m and "Flow at a UK retailer" in m
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # a missed replacement must withhold, not leak
    r = gen([cid], "md")
    assert r.status_code == 409 and r.json()["detail"] == render.WITHHELD and name not in r.text


def engagement():
    return full().model_copy(update={"basis": "engagement", "basis_reason": "executed contract"})


def test_engagement_has_note_and_no_outcomes_in_docx_md_pptx(approved):
    cid = approved(engagement())  # the record still holds an outcome: output must drop it
    note = render.ENGAGEMENT_NOTE
    t = doc_text(gen([cid], "docx").content)
    assert note in t and "Outcomes" not in t and "12 to 3 days" not in t
    m = gen([cid], "md").text
    assert note in m and "Outcomes" not in m and "12 to 3" not in m
    _, out = slides(gen([cid], "pptx").content)
    assert out[0]["Outcomes"] == [note]


def test_delivered_has_no_note(approved):
    cid = approved(full())
    assert render.ENGAGEMENT_NOTE not in doc_text(gen([cid], "docx").content)
    assert render.ENGAGEMENT_NOTE not in gen([cid], "md").text
    assert "Outcomes" in gen([cid], "md").text


def test_empty_metric_renders_value_only(approved):
    cid = approved(full().model_copy(update={"outcomes": [Outcome(metric="", value="cut by half", source_quote="q")]}))
    t = gen([cid]).text
    assert "- cut by half" in t and ": cut by half" not in t


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


def test_protected_name_replaced_or_withheld(approved, reg, monkeypatch):
    name, cid_client = reg("Zorp", "a UK retailer")
    ok = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name} in 2024", source_quote="q")}))
    t = gen([ok]).text
    assert name not in t and "Built for a UK retailer in 2024" in t
    evasion = name.replace("o", "ö")  # #166: apply() replaces what blocked() finds
    ev = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {evasion}", source_quote="q")}))
    r = gen([ev])
    assert r.status_code == 200 and "Built for a UK retailer" in r.text and evasion not in r.text
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # force apply() to miss: the fail-closed path
    bad = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name}", source_quote="q")}))
    r = gen([bad])
    assert r.status_code == 409 and r.json()["detail"] == render.WITHHELD and name not in r.text
    with db.connect() as conn:
        assert conn.execute("select count(*) from generations where %s = any(case_ids)", (bad,)).fetchone()[0] == 0


def test_referenceable_name_containing_protected_name_uses_its_label(approved, reg):
    p, _ = reg("Zorp", "a retailer")
    n, ref = reg("Zorp", "a logistics firm", True, suffix=" Logistics")
    cid = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {n} in 2024", source_quote="q")}))
    link(cid, ref)
    t = gen([cid]).text
    assert "*a logistics firm*" in t and "Built for a logistics firm in 2024" in t
    assert p not in t and "a retailer Logistics" not in t
    with db.connect() as conn:
        assert conn.execute("select anonymised from generations order by id desc limit 1").fetchone()[0] is True


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


def test_preview_shows_the_download_content(approved):
    cid = approved(full())
    r = prev([cid])
    assert r.status_code == 200
    page = " ".join(html.unescape(re.sub(r"<[^>]+>", "", r.text)).split())
    lines = [ln for ln in gen([cid]).text.splitlines() if ln.strip() and ln.strip() != "---"]
    assert len(lines) > 8
    for ln in lines:
        want = " ".join(re.sub(r"^[#\s-]+|[*\\]", "", ln).split())
        assert want in page, want


def test_preview_shows_ready_diagrams_from_the_user_route(approved):
    a, b = approved(full()), approved(full())
    did = add_diagram(b, "Flow <b>&</b>", (900, 100))
    page = prev([a, b]).text
    url = f"/cases/{b}/diagrams/{did}.png"
    assert f'<img src="{url}" alt="Flow &lt;b&gt;&amp;&lt;/b&gt;" width="480">' in page
    assert "<figcaption>Flow &lt;b&gt;&amp;&lt;/b&gt;</figcaption>" in page and page.count("<figure>") == 1
    assert client(R).get(url).status_code == 200  # the same user can load the image
    assert "<figure>" not in prev([a]).text


def test_preview_is_not_logged(approved):
    cid = approved()

    def count():
        with db.connect() as conn:
            return conn.execute("select count(*) from generations").fetchone()[0]
    before = count()
    assert prev([cid]).status_code == 200 and count() == before
    assert gen([cid]).status_code == 200 and count() == before + 1


def test_preview_enforces_generate_rules(approved):
    ok = approved()
    for bad in (prev([approved(acl=("g-other",))]), prev([approved(due="-1 day")]), prev([ok, 999999999]),
                prev([approved(status="extracted", due=None)])):
        assert bad.status_code == 404 and bad.json()["detail"] == "no such case"  # the route, not an unknown path
    assert prev([ok, ok, ok, ok]).status_code == 200  # duplicates collapse
    assert prev([ok, approved(), approved(), approved()]).status_code == 400
    assert client(R, origin=None).post("/preview", data={"case_ids": [ok], "format": "md"}).status_code == 403


def test_preview_escapes_values(approved):
    t = prev([approved(full(title="<script>x</script>"))]).text
    assert "&lt;script&gt;" in t and "<script>x" not in t


def test_preview_withholds_a_protected_client(approved, reg, monkeypatch):
    name, _ = reg("Zorp", "a UK retailer")
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # force apply() to miss: the fail-closed path
    bad = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name}", source_quote="q")}))
    r = prev([bad])
    assert r.status_code == 409 and name not in r.text


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


def test_markdown_escapes_ampersand():
    assert render.md("Zo&#114;p") == "Zo\\&\\#114;p"  # neither & nor # left bare: no entity is decoded
    assert render.md("R&D") == "R\\&D"


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


def test_pptx_protected_name_withheld(approved, reg, monkeypatch):
    name, _ = reg("Zorp", "a UK retailer")
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)
    bad = approved(full().model_copy(update={"challenge": Sourced[str](value=name, source_quote="q")}))
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


def test_lowercase_short_alias_is_rewritten_not_withheld():
    cl = {"id": 1, "name": "Union Bank of Zeta", "aliases": ["UBZ"], "anonymised_label": "a Swiss bank", "referenceable": False}
    out = render.protect([{"text": "Contact ops@ubz.example and the Ubz team"}], [cl])
    assert "ubz" not in out[0]["text"].lower()


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


def test_invisible_character_in_name_is_rewritten_not_shipped():
    cl = [{"id": 1, "name": "Zorp Bank", "aliases": ["Zorp"], "anonymised_label": "a bank", "referenceable": False}]
    assert render.protect([{"summary": "Run for Zo\u00adrp"}], cl)[0]["summary"] == "Run for a bank"


def sections(**kw):
    S = lambda v: Sourced[str](value=v, source_quote="q")  # noqa: E731
    return full(implementation=S("Phased rollout"), configuration=S("SSO via OIDC"), lessons_learned=S("Start earlier"), **kw)


def test_new_sections_in_word_and_markdown(approved):
    cid = approved(sections())
    for t in (doc_text(gen([cid], "docx").content), gen([cid], "md").text):
        for want in ("Implementation approach", "Phased rollout", "Configuration and integration", "SSO via OIDC",
                     "Lessons learned", "Start earlier"):
            assert want in t, want


def test_engagement_output_has_no_lessons_learned(approved):
    cid = approved(sections(basis="engagement", basis_reason="executed contract"))
    m = gen([cid], "md").text
    assert "Phased rollout" in m and "Lessons learned" not in m and "Start earlier" not in m


def test_pptx_unchanged_by_new_sections(approved):
    plain, rich = approved(full()), approved(sections())
    _, a = slides(gen([plain], "pptx").content)
    _, b = slides(gen([rich], "pptx").content)
    assert a == b


def test_new_section_naming_client_is_anonymised(approved, reg):
    name, label_id = reg("Zorp", "a UK retailer")
    cid = approved(full(implementation=Sourced[str](value=f"Rolled out at {name}", source_quote="q")))
    link(cid, label_id)
    t = gen([cid]).text
    assert name not in t and "Rolled out at a UK retailer" in t


def test_pptx_diagram_slide_per_diagram(approved):
    cid = approved(full())
    add_diagram(cid, "System overview", (400, 100))  # wider than the box ratio: fitted by width, not cropped
    add_diagram(cid, "Second", (50, 100))
    prs, out = slides(gen([cid], "pptx").content)
    assert [set(s) for s in out] == [set(NAMES), {"Title", "Caption"}, {"Title", "Caption"}]
    assert out[1]["Caption"] == ["System overview"] and out[1]["Title"] == ["Faster onboarding"]
    pics = [[sh for sh in s.shapes if sh.shape_type == 13] for s in prs.slides]
    assert [len(p) for p in pics] == [0, 1, 1]
    wide, tall = pics[1][0], pics[2][0]
    assert abs(wide.width / wide.height - 4) < .01 and abs(tall.width / tall.height - .5) < .01
    assert wide.width <= 9 * 914400 and tall.height <= 5 * 914400


def test_pptx_without_diagram_layout_fails_only_with_diagrams(tmp_path, monkeypatch):
    prs = Presentation(render.MASTER)
    next(l for l in prs.slide_layouts if l.name == "Diagram").name = "Other"
    odd = tmp_path / "odd.pptx"
    prs.save(odd)
    monkeypatch.setattr(render, "MASTER", odd)
    sec = lambda diagrams: dict(title="T", client="C", summary="S", note="", blocks=[], lists=[], diagrams=diagrams)  # noqa: E731
    d = dict(id=1, caption="c", width=10, height=10)
    render.to_pptx([sec([])])  # no diagrams: unaffected
    render.to_pptx([sec([d])])  # a diagram with no image (rejected or missing) is skipped
    with pytest.raises(ValueError, match="no slide layout named 'Diagram'"):
        render.to_pptx([sec([d])], images={1: b"x"})


# --- technical references (#212 phase D) ---
def with_refs(*values, **kw):
    return full(**kw).model_copy(update={"tech_references": [Sourced[str](value=v, source_quote="q") for v in values]
                                         + [Sourced[str](value="Unsourced ref", source_quote="q", unsourced=True)]})


def test_technical_references_render_in_md_docx_and_preview_without_unsourced_ones(approved):
    cid = approved(with_refs("ISO 20022", "OAuth 2.0"))
    md, preview, docx = gen([cid], "md").text, prev([cid]).text, doc_text(gen([cid], "docx").content)
    for name, text in (("md", md), ("preview", preview), ("docx", docx)):
        assert "Technical references" in text and "ISO 20022" in text and "OAuth 2.0" in text, name
        assert "Unsourced ref" not in text, name
    assert md.index("Technology") < md.index("Technical references")
    assert "Technical references" not in gen([approved(full())], "md").text  # none: the list is dropped


def test_technical_references_are_escaped_and_the_pptx_has_none(approved):
    cid = approved(with_refs("<script>x</script> & y"))
    assert "&lt;script&gt;" in prev([cid]).text and "<script>x" not in prev([cid]).text
    assert "\\<script" in gen([cid], "md").text or "&lt;script" in gen([cid], "md").text
    from pptx import Presentation
    deck = Presentation(BytesIO(gen([cid], "pptx").content))
    assert "script" not in " ".join(s.text_frame.text for sl in deck.slides for s in sl.shapes if s.has_text_frame)


def test_a_reference_naming_a_protected_client_is_anonymised_or_withheld(approved, reg, monkeypatch):
    name, label_id = reg("Zorp", "a UK retailer")
    cid = approved(with_refs(f"{name} payments API v2"))
    link(cid, label_id)
    m = gen([cid], "md").text
    assert name not in m and "a UK retailer payments API v2" in m
    monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)  # a missed replacement must withhold
    r = gen([cid], "md")
    assert r.status_code == 409 and name not in r.text


def test_section_prose_replaces_value_only_when_clean():
    from app.schema import Prose, ReferenceCase
    c = ReferenceCase.model_validate({"title": Sourced[str](value="T", source_quote="T").model_dump()})
    c.challenge = Sourced[str](value="one line", source_quote="one line")
    c.solution = Sourced[str](value="sol line", source_quote="sol line")
    c.prose = {"challenge": Prose(text="A\n\n\nB\n\n"), "solution": Prose(text="X", flags=["number"])}
    b = {x["heading"]: x for x in render.section(c, "C")["blocks"]}
    assert b["Challenge"]["paragraphs"] == ["A", "B"]
    assert b["Solution"]["text"] == "sol line" and b["Solution"]["paragraphs"] == ["sol line"]


def test_placeholder_metric_is_hidden_in_outputs():
    from app.schema import ReferenceCase
    c = ReferenceCase(title=Sourced[str](value="T", source_quote="T"), outcomes=[
        Outcome(metric="metric", value="flagged 17 percent more", source_quote="q"),
        Outcome(metric="Fraud", value="up 17%", source_quote="q")])
    out = render.section(c, "C")["lists"][0]["bullets"]
    assert out == ["flagged 17 percent more", "Fraud: up 17%"]


def test_docx_templates_loop_paragraphs():
    from docxtpl import DocxTemplate
    sec = {"title": "T", "client": "C", "summary": "S", "note": "", "details": [], "lists": [], "diagrams": [],
           "blocks": [{"heading": "Challenge", "text": "A\n\nB", "paragraphs": ["A", "B"]}]}
    for path in (render.TEMPLATE, render.TEMPLATE.with_name("case_study.docx")):
        tpl = DocxTemplate(path)
        tpl.render({"cases": [{**sec, "footer": "F"}], "industry": None}, autoescape=True)
        buf = BytesIO()
        tpl.save(buf)
        paras = [p.text for p in Document(buf).paragraphs]
        assert "A" in paras and "B" in paras, path


def test_case_study_docx_pdf_footer_and_one_case(approved, reg):
    name, label_id = reg("Zorp", "a UK retailer")
    c = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {name}", source_quote="q")}))
    link(c, label_id)
    r = gen([c], "case_study_docx")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('case-study.docx"')
    t = doc_text(r.content)
    assert "Faster onboarding" in t and "At a glance" in t and "Based on 1 source document reviewed" in t
    assert name not in t and "{{" not in t and "{%" not in t
    assert [p.text for p in Document(BytesIO(r.content)).paragraphs] and Document(BytesIO(r.content)).core_properties.author == "Reference Engine"
    pdf = gen([c], "case_study_pdf")
    assert pdf.headers["content-disposition"].endswith('case-study.pdf"') and pdf.content.startswith(b"%PDF")
    assert gen([c, approved(full())], "case_study_docx").status_code == 400
    one = html.unescape(prev([c]).text)
    note = re.search(r"Case study downloads also end with: (Based on 1 source document reviewed[^<]*)", one)
    assert note and note[1] in t and one.count("Based on 1 source document") == 1  # the note only, not the bid section
    assert "Case study downloads also end" not in html.unescape(prev([c, approved(full())]).text)


def test_case_study_withheld_gives_409(approved, reg, monkeypatch):
    c = approved(full())
    monkeypatch.setattr(render.anonymise, "blocked", lambda *a, **k: True)
    assert gen([c], "case_study_docx").status_code == 409

