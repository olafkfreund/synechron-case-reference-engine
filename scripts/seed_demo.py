"""Seed the local demo with made-up sources, clients and cases from demo/cases.json (no LLM calls).

    docker compose -p refsdemo ... exec worker python scripts/seed_demo.py

Safe to run again: S3 originals are re-put (moto is in memory). A document whose checksum changed is updated in
place, with its case; an unchanged one is left alone; nothing is deleted.
Every source it creates is named "Demo: ...", and every write is keyed on one of them.
Entry keys read by the seed only: expect_flag (one unsupported prose item is intended), diagram, document_image, override.
"""
import hashlib
import json
import os
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import boto3
from docx import Document
from PIL import Image, ImageDraw
from psycopg.types.json import Jsonb

from app import anonymise, db
from app.completeness import checklist, score
from app.extract import EXTRACTION_VERSION, check
from app.ingest import to_markdown
from app.review import fix_summary
from app.schema import Diagram, ReferenceCase, quote_in
from app.writeup import check_prose, quotes_of

DATA = Path(__file__).resolve().parents[1] / "demo" / "cases.json"


def docx_bytes(paragraphs: list[str]) -> bytes:
    """Same paragraphs, same bytes: the zip stamps its entries with the clock, so pin it."""
    buf = BytesIO()
    d = Document()
    for p in paragraphs:
        d.add_paragraph(p)
    d.save(buf)
    out = BytesIO()
    with zipfile.ZipFile(buf) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            zout.writestr(zipfile.ZipInfo(i.filename, (2020, 1, 1, 0, 0, 0)), zin.read(i.filename))
    return out.getvalue()


def draw(labels: list[str]) -> tuple[bytes, int, int]:
    """A plain boxes-and-arrows picture, one box per label, left to right."""
    w, h, gap, bh = 800, 320, 50, 80
    n = len(labels)
    bw = (w - 40 - gap * (n - 1)) // n
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = (h - bh) // 2
    for i, label in enumerate(labels):
        x = 20 + i * (bw + gap)
        d.rectangle([x, y, x + bw, y + bh], outline="#1f3a5f", fill="#e8eef7", width=2)
        tw, th = d.textbbox((0, 0), label)[2:]
        d.text((x + (bw - tw) / 2, y + (bh - th) / 2), label, fill="black")
        if i < n - 1:
            a, b = x + bw + 4, x + bw + gap - 4
            d.line([a, h // 2, b, h // 2], fill="#1f3a5f", width=2)
            d.polygon([(b, h // 2), (b - 10, h // 2 - 6), (b - 10, h // 2 + 6)], fill="#1f3a5f")
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue(), w, h


def _items(case: ReferenceCase):
    """(field, item) for everything that carries a source_quote."""
    for f in ("title", "client_mention", "industry", "region", "engagement_type", "challenge", "solution",
              "implementation", "configuration", "lessons_learned", "duration_months", "team_size", "period"):
        yield f, getattr(case, f)
    for f in ("capabilities", "tech_stack", "tech_references", "outcomes"):
        for i, s in enumerate(getattr(case, f)):
            yield f"{f}[{i}]", s
    for f, ps in case.points.items():
        for i, s in enumerate(ps):
            yield f"points.{f}[{i}]", s


def build(e: dict, text: str) -> ReferenceCase:
    """The case as it will be stored: checked against its own text, prose flags set, version stamped."""
    case = ReferenceCase.model_validate(e["case"])
    check(case, text)
    fix_summary(case)
    for f, p in case.prose.items():
        if not p.by_reviewer:
            p.flags = check_prose(p.text, quotes_of(case, f), case.quotes())
    case.extraction_version = EXTRACTION_VERSION
    return case


def validate(demo: dict, texts: dict[str, str]) -> list[str]:
    """Data-only checks, before any write. texts maps an entry's file to its markdown."""
    errors = []
    for e in demo["cases"]:
        name, text = e["file"], texts[e["file"]]
        try:
            case = build(e, text)
        except Exception as ex:  # noqa: BLE001 - a pydantic error names the field
            errors.append(f"{name}: case does not fit the schema: {ex}")
            continue
        planted = {f for f, it in _items(ReferenceCase.model_validate(e["case"])) if it.unsourced}  # the planted region example
        for f, it in _items(case):
            if f in planted:
                continue
            if it.source_quote and not quote_in(text, it.source_quote):
                errors.append(f"{name}: {f}: source_quote is not in the document")
            elif it.unsourced:
                errors.append(f"{name}: {f}: unsourced")
        for f, p in case.prose.items():
            if p.flags and (p.by_reviewer or not e.get("expect_flag")):
                errors.append(f"{name}: prose.{f}: {'; '.join(p.flags)}")
            if not p.flags and e.get("expect_flag") and not p.by_reviewer:
                errors.append(f"{name}: prose.{f}: expect_flag is set but the prose is clean")
    return errors


def main() -> int:
    demo = json.loads(DATA.read_text())
    bucket = os.environ["S3_BUCKET"]
    s3 = boto3.client("s3")
    blobs = {e["file"]: docx_bytes(e["paragraphs"]) for e in demo["cases"]}
    texts = {f: to_markdown(b, f) for f, b in blobs.items()}
    if errors := validate(demo, texts):
        print("demo seed: nothing written\n" + "\n".join(errors), file=sys.stderr)
        return 2
    new = updated = 0
    with db.connect() as conn:
        src = {}
        for s in demo["sources"]:
            conn.execute(
                "insert into sources(kind, name, config, acl_groups) values ('upload', %s, %s, %s) "
                "on conflict (name) do nothing",
                (s["name"], Jsonb({"bucket": bucket, "prefix": s["prefix"]}), s["groups"]))
            src[s["name"]] = conn.execute("select id, acl_groups from sources where name=%s", (s["name"],)).fetchone()
        for c in demo["clients"]:
            conn.execute(
                "insert into clients(name, aliases, anonymised_label, referenceable, logo_allowed) "
                "values (%s,%s,%s,%s,%s) on conflict (name) do nothing",
                (c["name"], c["aliases"], c["label"], c["referenceable"], c["logo"]))
        prefix = {s["name"]: s["prefix"] for s in demo["sources"]}
        for e in demo["cases"]:
            sid, groups = src[e["source"]]
            ext = prefix[e["source"]] + e["file"]
            data, text = blobs[e["file"]], texts[e["file"]]
            sha = hashlib.sha256(data).hexdigest()
            row = conn.execute("select id, s3_key, checksum from documents where source_id=%s and external_id=%s", (sid, ext)).fetchone()
            key = row[1] if row and row[2] == sha else f"originals/{sha}"
            for k in {key, ext}:
                s3.put_object(Bucket=bucket, Key=k, Body=data)
            if row and row[2] == sha:
                continue
            case = build(e, text)
            kind = "contract" if e["case"].get("basis") == "engagement" else "case"
            if row:
                doc_id = row[0]
                conn.execute("update documents set text=%s, checksum=%s, s3_key=%s, updated_at=now() where id=%s and source_id=%s",
                             (text, sha, key, doc_id, sid))
                conn.execute("delete from document_images where document_id=%s", (doc_id,))
                updated += 1
            else:
                doc_id = conn.execute(
                    "insert into documents(source_id, external_id, title, checksum, s3_key, text, kind, acl_groups) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (sid, ext, e["file"], sha, key, text, kind, groups)).fetchone()[0]
                new += 1
            labels = [t.value for t in case.tech_stack][:4]
            if e.get("document_image"):
                png, w, h = draw(labels)
                conn.execute("insert into document_images(document_id, png, width, height, hint) values (%s,%s,%s,%s,'Demo candidate')",
                             (doc_id, png, w, h))
            cid = conn.execute("select id from cases where document_id=%s", (doc_id,)).fetchone()
            if cid:
                cid = cid[0]
                conn.execute("delete from diagrams where case_id=%s and created_by='demo-seed'", (cid,))
            else:
                cid = conn.execute(
                    "insert into cases(document_id, data, summary, search_text, basis, status) values (%s,%s,%s,%s,%s,'extracted') returning id",
                    (doc_id, Jsonb(case.model_dump()), case.summary, case.search_text(), case.basis)).fetchone()[0]
            if e.get("diagram"):
                png, w, h = draw(labels)
                did = conn.execute("insert into diagrams(case_id, png, created_by) values (%s,%s,'demo-seed') returning id", (cid, png)).fetchone()[0]
                case.diagrams.append(Diagram(id=did, caption=f"Architecture: {' to '.join(labels)}", status="ready", width=w, height=h))
            # status goes back to the file's: a hand-approved or edited demo case returns to its scripted state
            client_id = anonymise.resolve(case.client_mention.value, anonymise.load_clients(conn)) if e["status"] == "approved" else None
            conn.execute(
                "update cases set data=%s, summary=%s, search_text=%s, basis=%s, status=%s, approved_by=%s, approved_at=%s, "
                "client_id=%s, review_due=%s where id=%s",
                (Jsonb(case.model_dump()), case.summary, case.search_text(), case.basis, e["status"],
                 None, None, client_id, None, cid))
            if e["status"] == "approved":
                conn.execute("update cases set approved_by='demo-seed', approved_at=now(), review_due=now() + interval '12 months' where id=%s", (cid,))
                items = checklist(case)
                if e.get("override") and not conn.execute("select 1 from case_approval_overrides where case_id=%s", (cid,)).fetchone():
                    ok, total = score(items)
                    conn.execute("insert into case_approval_overrides(case_id, reason, score, approved_by) values (%s,%s,%s,'demo-seed')",
                                 (cid, "Demo: approved below the minimum to show the override", f"{ok}/{total}"))
    print(f"demo seed: {new} new case(s), {updated} updated, {len(demo['cases']) - new - updated} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
