"""Seed the local demo with made-up sources, clients and cases from demo/cases.json (no LLM calls).

    docker compose -p refsdemo ... exec web python scripts/seed_demo.py

Safe to run again: S3 originals are re-put (moto is in memory); rows that already exist are left alone.
Every source it creates is named "Demo: ...", and it touches nothing else.
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
from psycopg.types.json import Jsonb

from app import anonymise, db
from app.extract import check
from app.ingest import to_markdown
from app.review import fix_summary
from app.schema import ReferenceCase

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


def main() -> int:
    demo = json.loads(DATA.read_text())
    bucket = os.environ["S3_BUCKET"]
    s3 = boto3.client("s3")
    new = 0
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
            data = docx_bytes(e["paragraphs"])
            row = conn.execute("select id, s3_key from documents where source_id=%s and external_id=%s", (sid, ext)).fetchone()
            key = row[1] if row else f"originals/{hashlib.sha256(data).hexdigest()}"
            for k in {key, ext}:
                s3.put_object(Bucket=bucket, Key=k, Body=data)
            if row:
                continue
            text = to_markdown(data, e["file"])
            kind = "contract" if e["case"].get("basis") == "engagement" else "case"
            doc_id = conn.execute(
                "insert into documents(source_id, external_id, title, checksum, s3_key, text, kind, acl_groups) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (sid, ext, e["file"], hashlib.sha256(data).hexdigest(), key, text, kind, groups)).fetchone()[0]
            case = ReferenceCase.model_validate(e["case"])
            check(case, text)
            fix_summary(case)
            conn.execute(
                "insert into cases(document_id, data, summary, search_text, basis, status) values (%s,%s,%s,%s,%s,'extracted')",
                (doc_id, Jsonb(case.model_dump()), case.summary, case.search_text(), case.basis))
            if e["status"] == "approved":
                client_id = anonymise.resolve(case.client_mention.value, anonymise.load_clients(conn))
                conn.execute(
                    "update cases set status='approved', approved_by='demo-seed', approved_at=now(), client_id=%s, "
                    "review_due=now() + interval '12 months' where document_id=%s", (client_id, doc_id))
            new += 1
    print(f"demo seed: {new} new case(s), {len(demo['cases']) - new} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
