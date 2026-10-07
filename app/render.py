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


def to_docx(sections: list[dict]) -> bytes:
    tpl = DocxTemplate(TEMPLATE)
    tpl.render({"cases": sections}, autoescape=True)  # autoescape: & and < must not break the XML
    props = tpl.docx.core_properties  # never ship the template's own metadata
    props.title, props.author, props.last_modified_by = "Reference cases", "Reference Engine", "Reference Engine"
    props.comments = props.subject = props.keywords = props.category = ""
    props.created = props.modified = datetime.now(timezone.utc).replace(tzinfo=None)  # not the template's dates
    buf = BytesIO()
    tpl.save(buf)
    return buf.getvalue()


def excerpt(s: str, limit: int = SLIDE_TEXT) -> str:
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + " …"


def to_pptx(sections: list[dict]) -> bytes:
    prs = Presentation(MASTER)
    if len(prs.slides):  # a master's sample slides (and their text) must never ship
        raise ValueError(f"{MASTER.name} must contain no slides, only layouts")
    layout = next((l for l in prs.slide_layouts if l.name == LAYOUT), None)
    if layout is None:
        raise ValueError(f"{MASTER.name} has no slide layout named {LAYOUT!r}")
    # slide placeholders get generic names when cloned, so the layout's names are the source of
    # truth: its placeholder idx is only the join key, never a position we rely on
    names = {ph.placeholder_format.idx: ph.name for ph in layout.placeholders}
    if len(names) != len(list(layout.placeholders)):
        raise ValueError(f"layout {LAYOUT!r} has duplicate placeholder idx values")
    if missing := [n for n in PLACEHOLDERS if n not in names.values()]:
        raise ValueError(f"layout {LAYOUT!r} is missing placeholders: {', '.join(missing)}")
    for c in sections:
        block = lambda h: next((b["text"] for b in c["blocks"] if b["heading"] == h), "")  # noqa: E731
        bullets = lambda h: next((l["bullets"] for l in c["lists"] if l["heading"] == h), [])  # noqa: E731
        content = {"Title": [c["title"]], "Client": [c["client"]], "Summary": [c["summary"]],
                   "Challenge": [excerpt(block("Challenge"))], "Solution": [excerpt(block("Solution"))],
                   "Outcomes": bullets("Outcomes")[:SLIDE_OUTCOMES],
                   "Technology": [", ".join(bullets("Technology"))]}
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
        if filled != set(PLACEHOLDERS):
            raise ValueError(f"layout {LAYOUT!r} did not yield placeholders: {', '.join(sorted(set(PLACEHOLDERS) - filled))}")
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
""")


def to_markdown(sections: list[dict]) -> str:
    return MD.render(cases=sections)


OFFICE = "application/vnd.openxmlformats-officedocument."
OUTPUTS = {"docx": (to_docx, OFFICE + "wordprocessingml.document"),
           "pptx": (to_pptx, OFFICE + "presentationml.presentation"),
           "md": (lambda s: to_markdown(s).encode(), "text/markdown; charset=utf-8"),
           # one format value per source keeps the form a single field
           "pdf_docx": (lambda s: to_pdf(to_docx(s), ".docx"), "application/pdf"),
           "pdf_pptx": (lambda s: to_pdf(to_pptx(s), ".pptx"), "application/pdf")}


@router.post("/generate")
def generate(case_ids: list[int] = Form(), format: str = Form(), user: User = Depends(require("user"))):
    ids = list(dict.fromkeys(case_ids))
    if not 1 <= len(ids) <= 3:
        raise HTTPException(400, "choose 1 to 3 cases")
    if format not in OUTPUTS:
        raise HTTPException(400, f"format must be one of {', '.join(OUTPUTS)}")
    with db.connect() as conn:
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
        make, mime = OUTPUTS[format]
        try:
            body = make(sections)  # before the audit row: a broken template must not log a generation
        except PdfError as e:
            raise HTTPException(503, str(e)) from None
        conn.execute("insert into generations(user_id, format, case_ids, anonymised, industry_context_ack) "
                     "values (%s,%s,%s,%s,false)", (user.sub, format, ids, anonymised))
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="reference-cases.{format.split('_')[0]}"', "X-Content-Type-Options": "nosniff"})
