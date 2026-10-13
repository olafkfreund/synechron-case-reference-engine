import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from jinja2 import Environment
from pptx import Presentation

from app import anonymise, db, office
from app.main import User, require
from app.review import PNG_HEADERS, VISIBLE, page
from app.schema import ReferenceCase, plain, vague_metric

router = APIRouter()
TEMPLATE = Path(__file__).resolve().parent.parent / "brand" / "reference.docx"
TEMPLATE_CASE_STUDY = TEMPLATE.with_name("case_study.docx")
MASTER = Path(__file__).resolve().parent.parent / "brand" / "master.pptx"
LAYOUT = "Reference case"
PLACEHOLDERS = ("Title", "Client", "Summary", "Challenge", "Solution", "Outcomes", "Technology")
WITHHELD = "output withheld: a protected client name is present"
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # invalid in XML; also used to split names
SLIDE_TEXT = 300  # challenge/solution excerpt on a slide; the Word version has the full text
SLIDE_OUTCOMES = 5
ENGAGEMENT_NOTE = "Engagement reference: contracted scope; no outcomes are claimed."


class Withheld(Exception):
    pass


COMPANY = "Synechron"
INDUSTRY_HEADING = "Industry context (public sources)"
SOURCES_HEADING = "Best practice and public sources"
APPROACH_HEADING = "Approach and industry practice"  # a case study: after our delivery sections, under the disclaimer
INDUSTRY_DISCLAIMER = f"These are public statements by third parties, not {COMPANY} delivery evidence."
# the type filters claims; it never reaches an output (#23)
CLAIM_TYPES = frozenset({"out_of_the_box", "configuration", "industry_practice", "vendor_claim"})
SLIDE_ITEMS = 5


def _host(url) -> str:
    try:
        return urlparse(str(url)).hostname or ""
    except ValueError:  # e.g. "http://[x"
        return ""


def industry_section(claims: list[dict], clients, heading=INDUSTRY_HEADING, pages=()) -> dict | None:
    """Verbatim quotes grouped by source page, cited [n], with a numbered source list. apply() then blocked()
    PER CLAIM: one that still names a protected client is dropped (and counted), not the whole document."""
    page_titles = {p.get("url"): p.get("title") for p in pages}
    groups, order, omitted = {}, [], 0  # url -> group; order = urls by first appearance
    for c in claims:
        if c.get("type") not in CLAIM_TYPES:
            continue
        # only quote and title are anonymised: rewriting a publisher or URL would forge the citation
        pl = (lambda t: t) if c.get("type") == "technical_note" else plain  # a note's code stays exact
        title = pl(str(c.get("title") or page_titles.get(c.get("url")) or "")) or _host(c.get("url", "")) or c.get("publisher", "")
        safe = {k: CTRL.sub("", str(v)) for k, v in
                {"quote": pl(str(c.get("quote", ""))), "title": title, "publisher": c.get("publisher", ""), "url": c.get("url", ""),
                 "retrieved_at": c.get("retrieved_at", "")}.items()}
        safe["quote"], safe["title"] = anonymise.apply(safe["quote"], clients), anonymise.apply(safe["title"], clients)
        safe["date"] = safe.pop("retrieved_at")[:10]
        if anonymise.blocked("\n".join(safe.values()), clients):  # incl. a client's own site as the source
            omitted += 1
            continue
        if safe["url"] not in groups:
            order.append(safe["url"])
            groups[safe["url"]] = {"title": safe["title"], "lead": f"{safe['publisher']} states:", "quotes": [],
                                   "source": {"n": len(order), "title": safe["title"], "publisher": safe["publisher"],
                                              "url": safe["url"], "date": safe["date"]}}
        g = groups[safe["url"]]
        g["quotes"].append({"quote": safe["quote"], "n": g["source"]["n"]})
    if not groups and not omitted:
        return None
    gl = [groups[u] for u in order]
    sources = [g["source"] for g in gl]
    statements = [{"text": t} for g in gl for t in [g["title"], *(f"\u201c{q['quote']}\u201d [{q['n']}]" for q in g["quotes"])]]
    if sources:
        statements.append({"text": "Sources: " + "; ".join(f"[{x['n']}] {x['title']}, {x['publisher']}, {x['url']}, retrieved {x['date']}"
                                                          for x in sources)})
    note = (f"{omitted} public statement{'s' if omitted != 1 else ''} omitted: "
            f"{'they' if omitted != 1 else 'it'} named a protected client") if omitted else ""
    return {"heading": heading, "disclaimer": INDUSTRY_DISCLAIMER,
            "groups": [{k: g[k] for k in ("title", "lead", "quotes")} for g in gl], "sources": sources,
            "statements": statements, "note": note}


def brief_section(body: dict, quotes: list[dict], clients) -> dict:
    """A brief for output: apply() on every string, then blocked() PER SENTENCE and per quote. What still names a
    protected client is dropped and counted, as in industry_section. Numbers [n] are the quote numbers the brief cites."""
    from app import brief  # brief imports render's neighbours; import here to keep render importable on its own
    clean = lambda t: anonymise.apply(CTRL.sub("", str(t or "")), clients)  # noqa: E731
    omitted, dead, sources, appendix = 0, set(), [], []
    for n, q in enumerate(quotes[:brief.MAX_QUOTES], 1):
        item = {"n": n, "quote": clean(plain(str(q.get("quote") or ""))),
                "title": clean(plain(str(q.get("title") or "")) or _host(q.get("url")) or q.get("publisher")),
                "publisher": CTRL.sub("", str(q.get("publisher") or "")), "url": CTRL.sub("", str(q.get("url") or "")),
                "date": CTRL.sub("", str(q.get("retrieved_at") or ""))[:10]}
        if anonymise.blocked("\n".join(str(v) for v in item.values()), clients):
            dead.add(n)
            omitted += 1
            continue
        sources.append({k: item[k] for k in ("n", "title", "publisher", "url", "date")})
        appendix.append({"n": n, "quote": item["quote"]})

    def prose(text):  # the sentences that survive, joined again
        nonlocal omitted
        kept = []
        for sent in brief.sentences(str(text or "")):
            c = clean(sent)
            if anonymise.blocked(c, clients) or dead & set(brief.cites(c)):
                omitted += 1
            else:
                kept.append(c)
        return " ".join(kept)

    title = clean(body.get("title"))
    if not title or anonymise.blocked(title, clients):
        title = "Best practice brief"
    themes = []
    for t in body.get("themes", []):
        heading = clean(t.get("heading"))
        if anonymise.blocked(heading, clients):
            omitted += 1
        elif text := prose(t.get("text")):
            themes.append({"heading": heading, "text": text})
    out = {"title": title, "disclaimer": INDUSTRY_DISCLAIMER, "takeaways": [x for x in map(prose, body.get("takeaways", [])) if x],
           "themes": themes, "sources": sources, "appendix": appendix,
           "note": (f"{omitted} statement{'s' if omitted != 1 else ''} omitted: "
                    f"{'they' if omitted != 1 else 'it'} named a protected client") if omitted else ""}
    if anonymise.blocked("\n".join(_strings(out)), clients):
        raise Withheld(WITHHELD)
    return out


BRIEF_TEMPLATE = TEMPLATE.with_name("brief.docx")


def to_brief_docx(sec: dict) -> bytes:
    tpl = DocxTemplate(BRIEF_TEMPLATE)
    tpl.render({"brief": sec}, autoescape=True)
    props = tpl.docx.core_properties  # never ship the template's own metadata
    props.title, props.author, props.last_modified_by = "Best practice brief", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)
    buf = BytesIO()
    tpl.save(buf)
    return buf.getvalue()


SLIDE_QUOTE = 140


def slide_statements(ind: dict) -> list[str]:
    """Every slide line starts with its source and keeps the verbatim quote next to it, so a reader can
    check it. The omitted-statements note (if any) comes last, as in
    Word and Markdown (#123)."""
    lines = [f"{g['lead']} \u201c{excerpt(g['quotes'][0]['quote'], SLIDE_QUOTE)}\u201d [{g['quotes'][0]['n']}]"
             for g in ind["groups"]]
    # a library brief is one line (its title and takeaways, no source numbers); briefs come first so they are not cut by the quotes
    briefs = [excerpt(f"{b['title']}: {' '.join(b['takeaways'])}".strip(": "), SLIDE_QUOTE) for b in ind.get("briefs", [])]
    return (briefs + lines)[:SLIDE_ITEMS] + ([ind["note"]] if ind["note"] else [])


def section(case: ReferenceCase, client: str) -> dict:
    """Only the approved record: no quotes, organisations, needs_attention or AI-tailored text."""
    v = lambda s: None if s.unsourced else s.value  # noqa: E731
    period = " – ".join(p for p in (case.period.start, case.period.end) if p) if not case.period.unsourced else ""
    details = [("Industry", v(case.industry)), ("Region", v(case.region)),
               ("Engagement type", v(case.engagement_type)),
               ("Duration", f"{v(case.duration_months)} months" if v(case.duration_months) else None),
               ("Team size", v(case.team_size)), ("Period", period)]
    def prose(f, head):  # approved prose replaces the one-line value only when it is unflagged or the reviewer's own
        p = case.prose.get(f)
        if p and p.text.strip() and (not p.flags or p.by_reviewer):
            return p.text, [x.strip() for x in p.text.split("\n\n") if x.strip()]
        return head, [head]  # the headline value goes out unchanged: one paragraph, no split

    blocks = [("Challenge", prose("challenge", v(case.challenge))), ("Solution", prose("solution", v(case.solution))),
              ("Implementation approach", prose("implementation", v(case.implementation))),
              ("Configuration and integration", prose("configuration", v(case.configuration))),
              ("Lessons learned", (None, []) if case.basis == "engagement" else prose("lessons_learned", v(case.lessons_learned)))]
    lists = [("Capabilities", [v(c) for c in case.capabilities]),
             ("Technology", [v(t) for t in case.tech_stack]),
             ("Technical references", [v(t) for t in case.tech_references]),
             ("Outcomes", [(f"{o.metric}: {o.value}" if not vague_metric(o) else o.value) for o in case.outcomes if not o.unsourced])]
    engagement = case.basis == "engagement"
    if engagement:  # contracted scope claims no results, whatever the record holds
        lists = [x for x in lists if x[0] != "Outcomes"]
    return dict(
        title=v(case.title) or "", client=client, summary=case.summary,
        basis=case.basis, note=ENGAGEMENT_NOTE if engagement else "",
        details=[dict(label=a, value=str(b)) for a, b in details if b not in (None, "")],
        blocks=[dict(heading=a, text=t, paragraphs=ps) for a, (t, ps) in blocks if t],
        lists=[dict(heading=a, bullets=[str(i) for i in b if i]) for a, b in lists if any(b)],
        diagrams=[dict(id=d.id, caption=d.caption, width=d.width, height=d.height) for d in case.diagrams if d.status == "ready"])


def _walk(o, f):
    if isinstance(o, str):
        return f(o)
    if isinstance(o, dict):
        return {k: _walk(x, f) for k, x in o.items()}
    return [_walk(x, f) for x in o] if isinstance(o, list) else o


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for x in o.values():
            yield from _strings(x)
    elif isinstance(o, list):
        for x in o:
            yield from _strings(x)


def protect(sections: list[dict], clients) -> list[dict]:
    """apply() on every string, then fail closed: blocked() must find nothing in the whole output."""
    # strip control characters FIRST: stripped after the check, "Zo\x01rp" would turn back into "Zorp"
    out = _walk(sections, lambda s: anonymise.apply(CTRL.sub("", s), clients))
    if anonymise.blocked("\n".join(_strings(out)), clients):
        raise Withheld(WITHHELD)  # never name the client
    return out


def to_docx(sections: list[dict], industry: dict | None = None, images: dict[int, bytes] = {}, template=None) -> bytes:
    tpl = DocxTemplate(template or TEMPLATE)
    # images are added after protect(): an InlineImage is not a string; one missing from `images` is skipped
    sections = [{**s, "diagrams": [{**d, "image": InlineImage(tpl, BytesIO(images[d["id"]]), width=Mm(min(160, d["width"] * 25.4 / 96)))}
                                   for d in s["diagrams"] if d["id"] in images]} for s in sections]
    tpl.render({"cases": sections, "industry": industry}, autoescape=True)  # autoescape: & and < must not break the XML
    props = tpl.docx.core_properties  # never ship the template's own metadata
    props.title, props.author, props.last_modified_by = "Reference cases", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)  # not the template's dates
    buf = BytesIO()
    tpl.save(buf)
    return buf.getvalue()


def to_case_study_docx(sections: list[dict], industry: dict | None = None, images: dict[int, bytes] = {}) -> bytes:
    return to_docx(sections, industry, images, TEMPLATE_CASE_STUDY)


def excerpt(s: str, limit: int = SLIDE_TEXT) -> str:
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + " …"


INDUSTRY_LAYOUT = "Industry context"
INDUSTRY_PLACEHOLDERS = ("Title", "Summary", "Statements")


DIAGRAM_LAYOUT = "Diagram"
DIAGRAM_PLACEHOLDERS = ("Title", "Picture", "Caption")


def _layout(prs, name: str, required) -> tuple | None:
    """(layout, idx -> placeholder name) after validation; None when the master has no such layout.
    Slide placeholders get generic names when cloned, so the layout's names are the source of truth:
    its placeholder idx is only the join key, never a position we rely on."""
    layout = next((l for l in prs.slide_layouts if l.name == name), None)
    if layout is None:
        return None
    names = {ph.placeholder_format.idx: ph.name for ph in layout.placeholders}
    if len(names) != len(list(layout.placeholders)):
        raise ValueError(f"layout {name!r} has duplicate placeholder idx values")
    if missing := [n for n in required if n not in names.values()]:
        raise ValueError(f"layout {name!r} is missing placeholders: {', '.join(missing)}")
    return layout, names


def _fill(prs, layout, names, content: dict, required) -> None:
    slide = prs.slides.add_slide(layout)
    filled = set()
    for ph in list(slide.placeholders):
        name = names.get(ph.placeholder_format.idx)
        if name not in content:  # logo/subtitle/footer etc.: removed, so no "Click to add text"
            ph._element.getparent().remove(ph._element)
            continue
        first, *rest = content[name] or [""]  # no content: empty text
        ph.text_frame.text = first
        for item in rest:
            ph.text_frame.add_paragraph().text = item
        filled.add(name)
    if filled != set(required):
        raise ValueError(f"layout {layout.name!r} did not yield placeholders: {', '.join(sorted(set(required) - filled))}")


def _diagram_slide(prs, layout, names, title: str, d: dict, png: bytes) -> None:
    _fill(prs, layout, names, {"Title": [title], "Picture": [""], "Caption": [d["caption"]]}, DIAGRAM_PLACEHOLDERS)
    slide = prs.slides[-1]
    idx = next(i for i, n in names.items() if n == "Picture")
    box = next(ph for ph in slide.placeholders if ph.placeholder_format.idx == idx)
    left, top, w, h = box.left, box.top, box.width, box.height
    box._element.getparent().remove(box._element)
    # add_picture at the box, not insert_picture: a picture placeholder crops the image to fill it
    scale = min(w / d["width"], h / d["height"])
    pw, ph_ = int(d["width"] * scale), int(d["height"] * scale)
    slide.shapes.add_picture(BytesIO(png), left + (w - pw) // 2, top + (h - ph_) // 2, width=pw, height=ph_)


def to_pptx(sections: list[dict], industry: dict | None = None, images: dict[int, bytes] = {}) -> bytes:
    prs = Presentation(MASTER)
    if len(prs.slides):  # a master's sample slides (and their text) must never ship
        raise ValueError(f"{MASTER.name} must contain no slides, only layouts")
    case_layout = _layout(prs, LAYOUT, PLACEHOLDERS)
    if case_layout is None:
        raise ValueError(f"{MASTER.name} has no slide layout named {LAYOUT!r}")
    diagram_layout = None
    if any(d["id"] in images for c in sections for d in c["diagrams"]):  # a master without it fails only now
        diagram_layout = _layout(prs, DIAGRAM_LAYOUT, DIAGRAM_PLACEHOLDERS)
        if diagram_layout is None:
            raise ValueError(f"{MASTER.name} has no slide layout named {DIAGRAM_LAYOUT!r}")
    for c in sections:
        block = lambda h: next((b["text"] for b in c["blocks"] if b["heading"] == h), "")  # noqa: E731
        bullets = lambda h: next((l["bullets"] for l in c["lists"] if l["heading"] == h), [])  # noqa: E731
        _fill(prs, *case_layout, {
            "Title": [c["title"]], "Client": [c["client"]], "Summary": [c["summary"]],
            "Challenge": [excerpt(block("Challenge"))], "Solution": [excerpt(block("Solution"))],
            "Outcomes": [c["note"]] if c["note"] else bullets("Outcomes")[:SLIDE_OUTCOMES], "Technology": [", ".join(bullets("Technology"))]},
            PLACEHOLDERS)
        for d in c["diagrams"]:
            if d["id"] in images:
                _diagram_slide(prs, *diagram_layout, c["title"], d, images[d["id"]])
    if industry:
        if own := _layout(prs, INDUSTRY_LAYOUT, INDUSTRY_PLACEHOLDERS):
            _fill(prs, *own, {"Title": [industry["heading"]], "Summary": [industry["disclaimer"]],
                              "Statements": slide_statements(industry)}, INDUSTRY_PLACEHOLDERS)
        else:  # fallback: the case layout, its case-only boxes left empty
            refs = [f"[{x['n']}] {x['publisher']}" for x in industry["sources"]]
            _fill(prs, *case_layout, {
                "Title": [industry["heading"]], "Client": [""], "Summary": [industry["disclaimer"]],
                "Challenge": [""], "Solution": [""], "Outcomes": slide_statements(industry),
                "Technology": ["Sources: " + ", ".join(refs)] if refs else [""]}, PLACEHOLDERS)
    props = prs.core_properties
    props.title, props.author, props.last_modified_by = "Reference cases", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)  # not the template's dates
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


PDF_TIMEOUT = 60  # seconds
PDF_WAIT = 10


class PdfError(RuntimeError):
    """Conversion failed. Messages never carry case content."""


def to_pdf(data: bytes, suffix: str) -> bytes:
    """Convert docx/pptx bytes with headless LibreOffice (see office.convert)."""
    name = f"in{suffix}"
    try:
        out = office.convert({name: data}, "pdf", PDF_TIMEOUT, PDF_WAIT)
    except RuntimeError as e:
        raise PdfError({"busy": "PDF service busy; try again shortly",
                        "timeout": "PDF conversion timed out"}.get(str(e), f"PDF conversion failed ({e})")) from None
    if name not in out:
        raise PdfError("PDF conversion failed (exit 0)")
    return out[name]


def md(s: str) -> str:
    """Markdown escaping: newlines become spaces; backslash, backtick, * _ [ ] < > # | ~ & are
    escaped; a leading list or numbered marker is escaped, so a value placed at the start of a
    paragraph or bullet cannot become a heading, list, table or strike-through."""
    s = re.sub(r"([\\`*_\[\]<>#|~&])", r"\\\1", " ".join(str(s).split()))  # &: no entity decodes to a name (#126)
    return re.sub(r"^(\d+)([.)])|^([-+=])", lambda m: f"{m[1]}\\{m[2]}" if m[1] else f"\\{m[3]}", s)


def link(url: str) -> str:
    """An autolink only for a plain http(s) URL: a '>' or space would end it and let the rest become Markdown."""
    return f"<{url}>" if re.fullmatch(r"https?://[^\s<>]+", url) else md(url)


_env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)  # Markdown, not HTML
_env.filters["md"] = md
_env.filters["link"] = link
MD = _env.from_string("""\
{% for c in cases %}
# {{ c.title | md }}

*{{ c.client | md }}*

{% if c.note %}
{{ c.note | md }}

{% endif %}
{{ c.summary | md }}

{% for x in c.details %}
- **{{ x.label }}:** {{ x.value | md }}
{% endfor %}

{% for b in c.blocks %}
## {{ b.heading }}

{% for p in b.paragraphs %}{{ p | md }}

{% endfor %}{% endfor %}
{% for l in c.lists %}
## {{ l.heading }}

{% for i in l.bullets %}
- {{ i | md }}
{% endfor %}

{% endfor %}
{% if c.diagrams %}
## Diagrams

{% for d in c.diagrams %}
- {{ d.caption | md }} (in the Word, PowerPoint and PDF versions)
{% endfor %}

{% endif %}
{% if not loop.last %}
---

{% endif %}
{% endfor %}
{% if industry %}
{% if cases %}
---

{% endif %}
{% if industry.show_head %}
# {{ industry.heading | md }}

*{{ industry.disclaimer | md }}*

{% for g in industry.groups %}
## {{ g.title | md }}

*{{ g.lead | md }}*

{% for q in g.quotes %}
> \u201c{{ q.quote | md }}\u201d [{{ q.n }}]

{% endfor %}
{% endfor %}
{% if industry.sources %}
## Sources

{% for s in industry.sources %}
{{ s.n }}. {{ s.title | md }}, {{ s.publisher | md }}, {{ s.url | link }}, retrieved {{ s.date | md }}
{% endfor %}
{% endif %}
{% endif %}
{% if industry.note %}

{{ industry.note | md }}
{% endif %}
{% for b in industry.briefs %}

# {{ b.title | md }}

*{{ b.disclaimer | md }}*

{% if b.takeaways %}
## Key takeaways

{% for t in b.takeaways %}
- {{ t | md }}
{% endfor %}

{% endif %}
{% for t in b.themes %}
## {{ t.heading | md }}

{{ t.text | md }}

{% endfor %}
{% if b.sources %}
## Sources

{% for s in b.sources %}
{{ s.n }}. {{ s.title | md }}, {{ s.publisher | md }}, {{ s.url | link }}, retrieved {{ s.date | md }}
{% endfor %}
{% endif %}
{% if b.note %}

{{ b.note | md }}
{% endif %}
{% endfor %}
{% endif %}
""")

BRIEF_MD = _env.from_string("""\
# {{ b.title | md }}

*{{ b.disclaimer | md }}*

{% if b.takeaways %}
## Key takeaways

{% for t in b.takeaways %}
- {{ t | md }}
{% endfor %}

{% endif %}
{% for t in b.themes %}
## {{ t.heading | md }}

{{ t.text | md }}

{% endfor %}
{% if b.sources %}
## Sources

{% for s in b.sources %}
{{ s.n }}. {{ s.title | md }}, {{ s.publisher | md }}, {{ s.url | link }}, retrieved {{ s.date | md }}
{% endfor %}

## Quotes

{% for q in b.appendix %}
> \u201c{{ q.quote | md }}\u201d [{{ q.n }}]

{% endfor %}
{% endif %}
{% if b.note %}
{{ b.note | md }}
{% endif %}
""")


def to_markdown(sections: list[dict], industry: dict | None = None) -> str:
    return MD.render(cases=sections, industry=industry)


OFFICE = "application/vnd.openxmlformats-officedocument."
OUTPUTS = {"docx": (to_docx, OFFICE + "wordprocessingml.document"),
           "pptx": (to_pptx, OFFICE + "presentationml.presentation"),
           "md": (lambda s, i, images={}: to_markdown(s, i).encode(), "text/markdown; charset=utf-8"),
           # one format value per source keeps the form a single field
           "pdf_docx": (lambda s, i, images={}: to_pdf(to_docx(s, i, images), ".docx"), "application/pdf"),
           "pdf_pptx": (lambda s, i, images={}: to_pdf(to_pptx(s, i, images), ".pptx"), "application/pdf"),
           "case_study_docx": (to_case_study_docx, OFFICE + "wordprocessingml.document"),
           "case_study_pdf": (lambda s, i, images={}: to_pdf(to_case_study_docx(s, i, images), ".docx"), "application/pdf")}


LIBRARY_MAX = 3


def build(conn, case_ids, research_id, industry_context_ack, user, fmt=None, library_ids=()):
    """The content of an output, shared by the download and the preview: (ids, sections, industry, anonymised, claims, library_used).
    Library items (#235) join the industry section (quotes) or render as briefs in industry["briefs"]; library_used lists those shown."""
    ids = list(dict.fromkeys(case_ids))
    if len(ids) > 3:
        raise HTTPException(400, "choose up to 3 cases")
    lib = list(dict.fromkeys(library_ids))
    if len(lib) > LIBRARY_MAX:
        raise HTTPException(400, f"choose up to {LIBRARY_MAX} library items")
    if not ids:  # industry context alone is never presented as our delivery without an explicit acknowledgement
        if research_id is None and not lib:
            raise HTTPException(400, "choose 1 to 3 cases, or a research result for an industry-context-only output")
        if not industry_context_ack:
            raise HTTPException(400, "an output with no case needs the acknowledgement that it contains public "
                                     "third-party statements only, not delivery evidence")
    if fmt is not None and fmt not in OUTPUTS:  # after the input checks, as on main
        raise HTTPException(400, f"format must be one of {', '.join(OUTPUTS)}")
    study = bool(fmt) and fmt.startswith("case_study")
    if study and len(ids) != 1:
        raise HTTPException(400, "a case study is one case")
    claims, pages = [], []
    if research_id is not None:  # only your own, finished research
        row = conn.execute("select results from research where id=%s and created_by=%s and status='done'",
                           (research_id, user.sub)).fetchone()
        if not row:
            raise HTTPException(404, "no such research")
        claims, pages = row[0].get("claims", []), row[0].get("pages", [])
    items = {r[0]: r for r in conn.execute("select id, kind, body from library_items where id = any(%s)", (lib,)).fetchall()} if lib else {}
    if len(items) != len(lib):
        raise HTTPException(404, "no such library item")  # every user may read every item, so an unknown id is the only miss
    # same restrictions as search: approved, in date, and the user can open the source document
    rows = conn.execute(
        "select c.id, c.data, cl.name, cl.anonymised_label, cl.referenceable, cl.id is not null, c.public_sources, c.member_count, c.approved_at "
        "from cases c left join clients cl on cl.id = c.client_id "
        f"where c.id = any(%s) and c.status = 'approved' and c.review_due > now() and {VISIBLE}",
        (ids, list(user.groups))).fetchall()
    by_id = {r[0]: r for r in rows}
    if len(by_id) != len(ids):
        raise HTTPException(404, "no such case")
    clients = anonymise.load_clients(conn)
    sections, anonymised, attached = [], False, []
    for i in ids:
        _, data, name, label, referenceable, linked, public_sources, members, approved = by_id[i]
        attached += public_sources
        shown = anonymise.shown(name, label, referenceable, linked)
        anonymised |= not (linked and referenceable) or anonymise.apply(shown, clients) != shown
        sec = section(ReferenceCase.model_validate(data), shown)
        if study:  # a role and a date, never a person; goes through protect() with the rest
            n = members or 1
            sec["footer"] = f"Based on {n} source document{'s' if n != 1 else ''} reviewed{f' on {approved:%Y-%m-%d}' if approved else ''}"
        sections.append(sec)
    try:
        sections = protect(sections, clients)
    except Withheld as e:
        raise HTTPException(409, str(e)) from None
    from app import research  # here: research imports render's neighbours
    lib_claims, briefs, used, withheld = [], [], [], 0
    for lid in lib:  # in the order chosen; text is re-checked now, whatever was true when it was saved
        _, kind, body = items[lid]
        if kind == "quotes":
            lib_claims += research.visible_claims(body["quotes"], clients)[0]
            used.append(lid)
        else:
            try:
                briefs.append(brief_section(body["brief"], body["quotes"], clients))
                used.append(lid)
            except Withheld:
                withheld += 1
    seen, merged = set(), []
    for c in attached + claims + lib_claims:  # attached first: they win the slide's 5 places
        if (k := (c.get("url"), c.get("quote"))) not in seen:
            seen.add(k); merged.append(c)
    # the new heading only if an attached claim survives apply()/blocked() at render
    cited = bool(attached) and bool((industry_section(attached, clients, pages=pages) or {}).get("statements"))
    mine = {(c.get("url"), c.get("quote")) for c in attached}
    only_attached = not any((c.get("url"), c.get("quote")) not in mine for c in claims + lib_claims)  # no ad-hoc research or library text merged in
    heading = (APPROACH_HEADING if study and only_attached else SOURCES_HEADING) if cited else INDUSTRY_HEADING
    industry = industry_section(merged, clients, heading=heading, pages=pages) \
        if merged else None
    if not ids and not (industry and industry["statements"]) and not briefs:
        raise HTTPException(400, "the research has no usable public statements")
    if briefs or withheld:
        industry = industry or {"heading": heading, "disclaimer": INDUSTRY_DISCLAIMER, "groups": [], "sources": [], "statements": [], "note": ""}
        if withheld:
            n = f"{withheld} library brief{'s' if withheld != 1 else ''} withheld: {'they' if withheld != 1 else 'it'} named a protected client"
            industry["note"] = f"{industry['note']}; {n}" if industry["note"] else n
    if industry:  # the heading and disclaimer come with quotes, or when there is no brief to stand alone
        industry["briefs"] = briefs
        industry["show_head"] = bool(industry["groups"]) or not (briefs or withheld)
    return ids, sections, industry, anonymised, claims, used


@router.post("/preview")
def preview(request: Request, case_ids: list[int] = Form([]), research_id: int | None = Form(None),
            industry_context_ack: bool = Form(False), library_ids: list[int] = Form([]), user: User = Depends(require("user"))):
    with db.connect() as conn:  # same content and errors as /generate; nothing is logged
        ids, sections, industry, _, _, used = build(conn, case_ids, research_id, industry_context_ack, user, library_ids=library_ids)
        footer, retitled = "", False  # what a case-study download adds; the same code path as /generate, so the text cannot drift
        if len(ids) == 1:
            _, study, study_industry, _, _, _ = build(conn, case_ids, research_id, industry_context_ack, user, "case_study_docx", library_ids)
            footer, retitled = study[0]["footer"], bool(study_industry) and study_industry["heading"] != (industry or {}).get("heading")
    return page(request, "output_preview.html", user, cases=sections, industry=industry, ids=ids,
                research_id=research_id, library_ids=used, ack=industry_context_ack, footer=footer, retitled=APPROACH_HEADING if retitled else "")


@router.post("/generate")
def generate(format: str = Form(), case_ids: list[int] = Form([]), research_id: int | None = Form(None),
             industry_context_ack: bool = Form(False), library_ids: list[int] = Form([]), user: User = Depends(require("user"))):
    with db.connect() as conn:
        ids, sections, industry, anonymised, claims, library_used = build(conn, case_ids, research_id, industry_context_ack, user, format, library_ids)
        make, mime = OUTPUTS[format]
        pairs = [(ids[n], d["id"]) for n, sec in enumerate(sections) for d in sec["diagrams"]]  # (case, diagram)
        images = {i: bytes(png) for i, png in conn.execute(
            "select id, png from diagrams where (case_id, id) in (select * from unnest(%s::bigint[], %s::bigint[])) "
            "and png is not null", ([c for c, _ in pairs], [d for _, d in pairs])).fetchall()} if pairs else {}
        try:
            body = make(sections, industry, images=images)  # before the audit row: a broken template must not log a generation
        except PdfError as e:
            raise HTTPException(503, str(e)) from None
        conn.execute("insert into generations(user_id, format, case_ids, anonymised, industry_context_ack, research_id, library_ids) "
                     "values (%s,%s,%s,%s,%s,%s,%s)", (user.sub, format, ids, anonymised, industry_context_ack and not ids,
                                                       research_id if (industry and claims) else None, library_used or None))
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{'case-study' if format.startswith('case_study') else 'reference-cases'}.{'pdf' if 'pdf' in format else format.rsplit('_', 1)[-1]}"', "X-Content-Type-Options": "nosniff"})


@router.get("/cases/{cid}/diagrams/{did}.png")
def diagram_png(cid: int, did: int, user: User = Depends(require("user"))):
    with db.connect() as conn:  # a ready diagram of a case /generate would also accept; raw bytes are never selected
        row = conn.execute(
            "select d.png from diagrams d join cases c on c.id = d.case_id where d.id = %s and c.id = %s "
            f"and c.status = 'approved' and c.review_due > now() and {VISIBLE} and d.png is not null "
            "and exists (select 1 from jsonb_array_elements(c.data->'diagrams') e "
            "where (e->>'id')::bigint = d.id and e->>'status' = 'ready')", (did, cid, list(user.groups))).fetchone()
    if not row:
        raise HTTPException(404, "no such diagram")
    return Response(bytes(row[0]), media_type="image/png", headers=PNG_HEADERS)
