import importlib.util
import io
import json
from collections import Counter
from pathlib import Path

import boto3
import pytest
from docx import Document
from moto import mock_aws

from app import db

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

DEMO = json.loads((ROOT / "demo" / "cases.json").read_text())


def clean():
    with db.connect() as c:
        c.execute("delete from sources where name = any(%s)", ([s["name"] for s in DEMO["sources"]],))
        c.execute("delete from clients where name = any(%s)", ([x["name"] for x in DEMO["clients"]],))


@pytest.fixture
def env(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "demo")
    monkeypatch.setattr(seed, "to_markdown",
                        lambda data, name: "\n\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs))
    db.init()
    clean()
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="demo")
        yield
    clean()


def counts():
    with db.connect() as c:
        return (c.execute("select count(*) from cases c join documents d on d.id=c.document_id "
                          "join sources s on s.id=d.source_id where s.name like 'Demo: %'").fetchone()[0],
                c.execute("select count(*) from documents d join sources s on s.id=d.source_id "
                          "where s.name like 'Demo: %'").fetchone()[0],
                c.execute("select count(*) from clients where name = any(%s)", ([x["name"] for x in DEMO["clients"]],)).fetchone()[0])


def test_seed_twice(env):
    assert seed.main() == 0
    first = counts()
    assert first == (len(DEMO["cases"]), len(DEMO["cases"]), len(DEMO["clients"]))
    assert seed.main() == 0
    assert counts() == first  # the second run adds no rows

    with db.connect() as c:
        rows = c.execute("select c.status, c.basis, c.client_id, c.data, d.external_id from cases c "
                         "join documents d on d.id=c.document_id join sources s on s.id=d.source_id "
                         "where s.name like 'Demo: %'").fetchall()
    assert Counter(r[0] for r in rows) == Counter(e["status"] for e in DEMO["cases"])

    for status, basis, client_id, data, ext in rows:
        planned = next(e for e in DEMO["cases"] if e["source"] and ext.endswith(e["file"]))["case"]
        if status == "approved":
            assert client_id and not data["needs_attention"]
        for f in ("title", "client_mention", "industry", "region", "challenge", "solution", "duration_months"):
            if planned.get(f) and planned[f].get("source_quote") and not planned[f].get("unsourced"):
                assert data[f]["unsourced"] is False, (ext, f)
        for f in ("region",):  # the planted unsourced field stays flagged
            if planned.get(f, {}).get("unsourced"):
                assert data[f]["unsourced"] is True

    fab = [r for r in rows if "fabrikam" in r[4]]
    assert len(fab) == 2 and {r[1] for r in fab} == {"engagement"}
    with db.connect() as c:  # the #58 repair copies a source's groups onto its documents: they must already match
        assert c.execute("select count(*) from documents d join sources s on s.id = d.source_id "
                         "where s.name like 'Demo: %%' and d.acl_groups is distinct from s.acl_groups").fetchone()[0] == 0

    s3 = boto3.client("s3")
    with db.connect() as c:
        keys = [r[0] for r in c.execute("select s3_key from documents d join sources s on s.id=d.source_id "
                                        "where s.name like 'Demo: %'").fetchall()]
    for k in keys:
        s3.head_object(Bucket="demo", Key=k)
