import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from docxtpl import DocxTemplate
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from jinja2 import Environment
from pptx import Presentation

from app import anonymise, db
from app.main import User, require
from app.review import ACL
from app.schema import ReferenceCase

router = APIRouter()
TEMPLATE = Path(__file__).resolve().parent.parent / "brand" / "reference.docx"
MASTER = Path(__file__).resolve().parent.parent / "brand" / "master.pptx"
LAYOUT = "Reference case"
PLACEHOLDERS = ("Title", "Client", "Summary", "Challenge", "Solution", "Outcomes", "Technology")
WITHHELD = "output withheld: a protected client name is present"
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # invalid in XML; also used to split names
SLIDE_TEXT = 300  # challenge/solution excerpt on a slide; the Word version has the full text
SLIDE_OUTCOMES = 5


class Withheld(Exception):
    pass


COMPANY = "Synechron"
INDUSTRY_HEADING = "Industry context (public sources)"
INDUSTRY_DISCLAIMER = f"These are public statements by third parties, not {COMPANY} delivery evidence."
# fixed wording per claim type: the AI's own statement is never presented as fact
PHRASES = {"out_of_the_box": "Supported out of the box", "configuration": "Available through configuration",
           "industry_practice": "Common industry practice", "vendor_claim": "Vendor statement"}
SLIDE_ITEMS = 5


def industry_section(claims: list[dict], clients) -> dict | None:
    """Verbatim quotes with their source, from research claims only. apply() then blocked() PER CLAIM:
    one that still names a protected client is dropped (and counted), not the whole document."""
    items, omitted = [], 0
    for c in claims:
        if c.get("type") not in PHRASES:
            continue
        # only the quote is anonymised: rewriting a publisher or URL would forge the citation
        safe = {k: CTRL.sub("", str(c.get(k, ""))) for k in ("quote", "publisher", "url", "retrieved_at")}
        safe["quote"], safe["date"] = anonymise.apply(safe["quote"], clients), safe.pop("retrieved_at")[:10]
        if anonymise.blocked("\n".join(safe.values()), clients):  # incl. a client's own site as the source
            omitted += 1
            continue
        phrase = PHRASES[c["type"]]
        items.append({**safe, "phrase": phrase,
                      "text": f"{phrase}: \u201c{safe['quote']}\u201d \u2014 {safe['publisher']}, {safe['url']}, retrieved {safe['date']}"})
    if not items and not omitted:
        return None
    note = (f"{omitted} public statement{'s' if omitted != 1 else ''} omitted: "
            f"{'they' if omitted != 1 else 'it'} named a protected client") if omitted else ""
    return {"heading": INDUSTRY_HEADING, "disclaimer": INDUSTRY_DISCLAIMER, "statements": items, "note": note}


SLIDE_QUOTE = 140


def slide_statements(ind: dict) -> list[str]:
    """Every slide line keeps its verbatim quote next to the phrase: the phrase comes from an AI-chosen
    type, the quote is what lets a reader check it."""
    return [f"{i['phrase']}: \u201c{excerpt(i['quote'], SLIDE_QUOTE)}\u201d \u2014 {i['publisher']}, {i['date']}"
            for i in ind["statements"]][:SLIDE_ITEMS]


def section(case: ReferenceCase, client: str) -> dict:
    """Only the approved record: no quotes, organisations, needs_attention or AI-tailored text."""
    v = lambda s: None if s.unsourced else s.value  # noqa: E731
    period = " – ".join(p for p in (case.period.start, case.period.end) if p) if not case.period.unsourced else ""
    details = [("Industry", v(case.industry)), ("Region", v(case.region)),
               ("Engagement type", v(case.engagement_type)),
               ("Duration", f"{v(case.duration_months)} months" if v(case.duration_months) else None),
               ("Team size", v(case.team_size)), ("Period", period)]
    blocks = [("Challenge", v(case.challenge)), ("Solution", v(case.solution))]
    lists = [("Capabilities", [v(c) for c in case.capabilities]),
             ("Technology", [v(t) for t in case.tech_stack]),
             ("Outcomes", [f"{o.metric}: {o.value}" for o in case.outcomes if not o.unsourced])]
    return dict(
        title=v(case.title) or "", client=client, summary=case.summary,
        details=[dict(label=a, value=str(b)) for a, b in details if b not in (None, "")],
        blocks=[dict(heading=a, text=b) for a, b in blocks if b],
        lists=[dict(heading=a, bullets=[str(i) for i in b if i]) for a, b in lists if any(b)])


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


def to_docx(sections: list[dict], industry: dict | None = None) -> bytes:
    tpl = DocxTemplate(TEMPLATE)
    tpl.render({"cases": sections, "industry": industry}, autoescape=True)  # autoescape: & and < must not break the XML
    props = tpl.docx.core_properties  # never ship the template's own metadata
    props.title, props.author, props.last_modified_by = "Reference cases", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)  # not the template's dates
    buf = BytesIO()
    tpl.save(buf)
    return buf.getvalue()


def excerpt(s: str, limit: int = SLIDE_TEXT) -> str:
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + " …"


INDUSTRY_LAYOUT = "Industry context"
INDUSTRY_PLACEHOLDERS = ("Title", "Summary", "Statements")


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


def to_pptx(sections: list[dict], industry: dict | None = None) -> bytes:
    prs = Presentation(MASTER)
    if len(prs.slides):  # a master's sample slides (and their text) must never ship
        raise ValueError(f"{MASTER.name} must contain no slides, only layouts")
    case_layout = _layout(prs, LAYOUT, PLACEHOLDERS)
    if case_layout is None:
        raise ValueError(f"{MASTER.name} has no slide layout named {LAYOUT!r}")
    for c in sections:
        block = lambda h: next((b["text"] for b in c["blocks"] if b["heading"] == h), "")  # noqa: E731
        bullets = lambda h: next((l["bullets"] for l in c["lists"] if l["heading"] == h), [])  # noqa: E731
        _fill(prs, *case_layout, {
            "Title": [c["title"]], "Client": [c["client"]], "Summary": [c["summary"]],
            "Challenge": [excerpt(block("Challenge"))], "Solution": [excerpt(block("Solution"))],
            "Outcomes": bullets("Outcomes")[:SLIDE_OUTCOMES], "Technology": [", ".join(bullets("Technology"))]},
            PLACEHOLDERS)
    if industry:
        if own := _layout(prs, INDUSTRY_LAYOUT, INDUSTRY_PLACEHOLDERS):
            _fill(prs, *own, {"Title": [industry["heading"]], "Summary": [industry["disclaimer"]],
                              "Statements": slide_statements(industry)}, INDUSTRY_PLACEHOLDERS)
        else:  # fallback: the case layout, its case-only boxes left empty
            publishers = list(dict.fromkeys(i["publisher"] for i in industry["statements"]))
            _fill(prs, *case_layout, {
                "Title": [industry["heading"]], "Client": [""], "Summary": [industry["disclaimer"]],
                "Challenge": [""], "Solution": [""], "Outcomes": slide_statements(industry),
                "Technology": ["Sources: " + ", ".join(publishers)] if publishers else [""]}, PLACEHOLDERS)
    props = prs.core_properties
    props.title, props.author, props.last_modified_by = "Reference cases", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)  # not the template's dates
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


SOFFICE = "soffice"
PDF_TIMEOUT = 60  # seconds


class PdfError(RuntimeError):
    """Conversion failed. Messages never carry case content."""


# The template comes from marketing: never let it fetch linked images/remote content (the task can
# still reach the VPC and the ECS metadata endpoint) or run macros.
LOCKDOWN = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
</oor:items>
"""
# ~235 MB per soffice; the threadpool would otherwise allow ~40 at once
PDF_SLOTS = threading.BoundedSemaphore(2)
PDF_WAIT = 10


def to_pdf(data: bytes, suffix: str) -> bytes:
    """Convert docx/pptx bytes with headless LibreOffice. Own temp dir and profile per call:
    concurrent soffice runs share (and lock) one profile otherwise."""
    if not PDF_SLOTS.acquire(timeout=PDF_WAIT):
        raise PdfError("PDF service busy; try again shortly")
    tmp = tempfile.mkdtemp(prefix="pdf-")
    try:
        src = Path(tmp) / f"in{suffix}"
        src.write_bytes(data)
        profile = Path(tmp) / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(LOCKDOWN)
        # new session so a timeout can kill soffice's child processes too; minimal env: no DB or AWS secrets
        proc = subprocess.Popen(
            [SOFFICE, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
             "--convert-to", "pdf", "--outdir", tmp, str(src)],
            env={"PATH": os.environ.get("PATH", ""), "HOME": tmp}, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=PDF_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise PdfError("PDF conversion timed out") from None
        out = Path(tmp) / "in.pdf"
        if code or not out.exists():
            raise PdfError(f"PDF conversion failed (exit {code})")
        return out.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        PDF_SLOTS.release()


def md(s: str) -> str:
    """Markdown escaping: newlines become spaces; backslash, backtick, * _ [ ] < > # | ~ are
    escaped; a leading list or numbered marker is escaped, so a value placed at the start of a
    paragraph or bullet cannot become a heading, list, table or strike-through."""
    s = re.sub(r"([\\`*_\[\]<>#|~])", r"\\\1", " ".join(str(s).split()))
    return re.sub(r"^(\d+)([.)])|^([-+=])", lambda m: f"{m[1]}\\{m[2]}" if m[1] else f"\\{m[3]}", s)


_env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)  # Markdown, not HTML
_env.filters["md"] = md
MD = _env.from_string("""\
{% for c in cases %}
# {{ c.title | md }}

*{{ c.client | md }}*

{{ c.summary | md }}

{% for x in c.details %}
- **{{ x.label }}:** {{ x.value | md }}
{% endfor %}

{% for b in c.blocks %}
## {{ b.heading }}

{{ b.text | md }}

{% endfor %}
{% for l in c.lists %}
## {{ l.heading }}

{% for i in l.bullets %}
- {{ i | md }}
{% endfor %}

{% endfor %}
{% if not loop.last %}
---

{% endif %}
{% endfor %}
{% if industry %}
{% if cases %}
---

{% endif %}
# {{ industry.heading | md }}

*{{ industry.disclaimer | md }}*

{% for i in industry["statements"] %}
- {{ i.phrase | md }}: \u201c{{ i.quote | md }}\u201d \u2014 {{ i.publisher | md }}, {{ i.url | md }}, retrieved {{ i.date | md }}
{% endfor %}
{% if industry.note %}

{{ industry.note | md }}
{% endif %}
{% endif %}
""")


def to_markdown(sections: list[dict], industry: dict | None = None) -> str:
    return MD.render(cases=sections, industry=industry)


OFFICE = "application/vnd.openxmlformats-officedocument."
OUTPUTS = {"docx": (to_docx, OFFICE + "wordprocessingml.document"),
           "pptx": (to_pptx, OFFICE + "presentationml.presentation"),
           "md": (lambda s, i: to_markdown(s, i).encode(), "text/markdown; charset=utf-8"),
           # one format value per source keeps the form a single field
           "pdf_docx": (lambda s, i: to_pdf(to_docx(s, i), ".docx"), "application/pdf"),
           "pdf_pptx": (lambda s, i: to_pdf(to_pptx(s, i), ".pptx"), "application/pdf")}


@router.post("/generate")
def generate(format: str = Form(), case_ids: list[int] = Form([]), research_id: int | None = Form(None),
             industry_context_ack: bool = Form(False), user: User = Depends(require("user"))):
    ids = list(dict.fromkeys(case_ids))
    if len(ids) > 3:
        raise HTTPException(400, "choose up to 3 cases")
    if not ids:  # industry context alone is never presented as our delivery without an explicit acknowledgement
        if research_id is None:
            raise HTTPException(400, "choose 1 to 3 cases, or a research result for an industry-context-only output")
        if not industry_context_ack:
            raise HTTPException(400, "an output with no case needs the acknowledgement that it contains public "
                                     "third-party statements only, not delivery evidence")
    if format not in OUTPUTS:
        raise HTTPException(400, f"format must be one of {', '.join(OUTPUTS)}")
    with db.connect() as conn:
        claims = []
        if research_id is not None:  # only your own, finished research
            row = conn.execute("select results from research where id=%s and created_by=%s and status='done'",
                               (research_id, user.sub)).fetchone()
            if not row:
                raise HTTPException(404, "no such research")
            claims = row[0].get("claims", [])
        # same restrictions as search: approved, in date, and the user can open the source document
        rows = conn.execute(
            "select c.id, c.data, cl.name, cl.anonymised_label, cl.referenceable, cl.id is not null "
            "from cases c join documents d on d.id = c.document_id left join clients cl on cl.id = c.client_id "
            f"where c.id = any(%s) and c.status = 'approved' and c.review_due > now() and {ACL}",
            (ids, list(user.groups))).fetchall()
        by_id = {r[0]: r for r in rows}
        if len(by_id) != len(ids):
            raise HTTPException(404, "no such case")
        clients = anonymise.load_clients(conn)
        sections, anonymised = [], False
        for i in ids:
            _, data, name, label, referenceable, linked = by_id[i]
            shown = name if linked and referenceable else (label if linked else "a client")
            anonymised |= not (linked and referenceable)
            sections.append(section(ReferenceCase.model_validate(data), shown))
        try:
            sections = protect(sections, clients)
        except Withheld as e:
            raise HTTPException(409, str(e)) from None
        industry = industry_section(claims, clients) if research_id is not None else None
        if not ids and not (industry and industry["statements"]):
            raise HTTPException(400, "the research has no usable public statements")
        make, mime = OUTPUTS[format]
        try:
            body = make(sections, industry)  # before the audit row: a broken template must not log a generation
        except PdfError as e:
            raise HTTPException(503, str(e)) from None
        conn.execute("insert into generations(user_id, format, case_ids, anonymised, industry_context_ack, research_id) "
                     "values (%s,%s,%s,%s,%s,%s)", (user.sub, format, ids, anonymised, industry_context_ack and not ids,
                                                    research_id if industry else None))
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="reference-cases.{format.split('_')[0]}"', "X-Content-Type-Options": "nosniff"})
