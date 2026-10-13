import importlib.util
import copy
import io
import json
import re
from collections import Counter
from pathlib import Path

import boto3
import pytest
from docx import Document
from moto import mock_aws

from app import db
from app.completeness import checklist, score
from app.extract import EXTRACTION_VERSION
from app.schema import ReferenceCase

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


def md(e):
    return "\n\n".join(e["paragraphs"])


def use(tmp_path, monkeypatch, demo):
    f = tmp_path / "cases.json"
    f.write_text(json.dumps(demo))
    monkeypatch.setattr(seed, "DATA", f)


def subset(n=2):
    """The first n demo cases; the first one exercises every optional seed path."""
    d = copy.deepcopy(DEMO)
    d["cases"] = d["cases"][:n]
    d["cases"][0].update(diagram=True, document_image=True, override=True)
    return d


def test_every_quote_is_in_its_document():
    assert seed.validate(DEMO, {e["file"]: md(e) for e in DEMO["cases"]}) == []


ALLOWED = {"OpenID Connect", "ServiceNow", "Kafka", "Azure", "Kubernetes", "PostgreSQL", "SWIFT", "ISO", "SAP", "Salesforce", "Oracle",
           "Docker", "Terraform", "Redis", "Python", "Java", "React", "Angular", "Power BI", "Active Directory",
           "Microsoft", "Google", "Amazon", "AWS", "OAuth", "OpenAPI", "REST", "GraphQL", "SAML", "Okta",
           "Snowflake", "Databricks", "Elasticsearch", "RabbitMQ", "MongoDB", "Jenkins", "GitHub", "GitLab"}


def test_only_fictional_names():
    clients = {n.casefold() for c in DEMO["clients"] for n in (c["name"], *c["aliases"])}
    ok = clients | {a.casefold() for a in ALLOWED}
    bad = []
    for e in DEMO["cases"]:
        c = e["case"]
        mention = c.get("client_mention", {}).get("value") or ""
        bad += [(e["file"], mention)] if mention.casefold() not in clients else []  # a vendor is never the customer
        names = [mention, *c.get("organisations", [])]
        names += re.findall(r"\b[A-Z][\w-]*(?: [A-Z][\w-]*)+", re.sub(r"\bThe ", "the ", " ".join(e["paragraphs"])))
        bad += [(e["file"], n) for n in names if n and n.casefold() not in ok]
    assert not bad


def test_refresh_updates_only_demo_rows(env, tmp_path, monkeypatch):
    demo = subset()
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 0
    q = ("select d.id, d.text, d.updated_at, c.id, c.status, md5(c.data::text) from documents d join cases c on c.document_id=d.id "
         "join sources s on s.id=d.source_id where s.name like 'Demo: %%' and d.title=%s")
    with db.connect() as c:
        first = c.execute(q, (demo["cases"][0]["file"],)).fetchone()
        other = c.execute(q, (demo["cases"][1]["file"],)).fetchone()
        cid = first[3]
        assert c.execute("select count(*) from diagrams where case_id=%s and created_by='demo-seed'", (cid,)).fetchone()[0] == 1
        assert c.execute("select count(*) from document_images where document_id=%s", (first[0],)).fetchone()[0] == 1
        assert c.execute("select count(*) from case_approval_overrides where case_id=%s", (cid,)).fetchone()[0] == 1
        data = c.execute("select data from cases where id=%s", (cid,)).fetchone()[0]
        assert data["extraction_version"] == EXTRACTION_VERSION
        assert [d["status"] for d in data["diagrams"]] == ["ready"] and data["diagrams"][0]["caption"]
        # a non-demo source holding the same external_id, and a hand edit to the demo case
        ext = c.execute("select external_id from documents where id=%s", (first[0],)).fetchone()[0]
        sid = c.execute("insert into sources(kind, name, config, acl_groups) values ('upload','Other: not demo','{}',%s) returning id",
                        ([],)).fetchone()[0]
        odoc = c.execute("insert into documents(source_id, external_id, title, checksum, s3_key, text, kind, acl_groups) "
                         "values (%s,%s,'t','old','k','old text','case',%s) returning id", (sid, ext, [])).fetchone()[0]
        c.execute("insert into cases(document_id, data, summary, search_text, basis, status) values (%s,%s,'','','delivered','extracted')",
                  (odoc, '{"title": {"value": "mine"}}'))
        c.execute("update cases set status='extracted', approved_by=null where id=%s", (cid,))
    try:
        demo["cases"][0]["paragraphs"].append("An extra closing note for the refresh.")
        use(tmp_path, monkeypatch, demo)
        assert seed.main() == 0
        with db.connect() as c:
            again = c.execute(q, (demo["cases"][0]["file"],)).fetchone()
            assert again[0] == first[0] and again[3] == cid  # updated in place, not re-inserted
            assert "extra closing note" in again[1] and again[2] > first[2]
            assert again[4] == "approved"  # back to the file's status
            assert c.execute("select approved_by from cases where id=%s", (cid,)).fetchone()[0] == "demo-seed"
            assert c.execute(q, (demo["cases"][1]["file"],)).fetchone() == other
            did = c.execute("select id from diagrams where case_id=%s", (cid,)).fetchall()
            assert len(did) == 1 and c.execute("select data->'diagrams'->0->>'id' from cases where id=%s", (cid,)).fetchone()[0] == str(did[0][0])
            assert c.execute("select count(*) from document_images where document_id=%s", (first[0],)).fetchone()[0] == 1
            assert c.execute("select count(*) from case_approval_overrides where case_id=%s", (cid,)).fetchone()[0] == 1
            assert c.execute("select d.text, d.checksum, c.data->'title'->>'value' from documents d join cases c on c.document_id=d.id "
                             "where d.id=%s", (odoc,)).fetchone() == ("old text", "old", "mine")
    finally:
        with db.connect() as c:
            c.execute("delete from sources where name='Other: not demo'")


def test_failed_validation_writes_nothing(env, tmp_path, monkeypatch, capsys):
    demo = subset()
    demo["cases"][1]["case"]["title"]["source_quote"] = "A sentence that is nowhere in this document."
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 2
    err = capsys.readouterr().err
    assert demo["cases"][1]["file"] in err and "title" in err
    with db.connect() as c:
        assert c.execute("select count(*) from sources where name like 'Demo: %'").fetchone()[0] == 0
    assert counts()[:2] == (0, 0)


def test_completeness_mix(env):
    assert seed.main() == 0
    want = {i: (9, 9) for i in (0, 1, 2, 3, 5, 6, 7, 8)} | {4: (6, 9), 10: (8, 9), 11: (8, 9)}
    with db.connect() as c:
        rows = {r[0]: ReferenceCase.model_validate(r[1]) for r in c.execute(
            "select d.title, c.data from cases c join documents d on d.id=c.document_id join sources s on s.id=d.source_id "
            "where s.name like 'Demo: %'").fetchall()}
    for i, (ok, total) in want.items():
        assert score(checklist(rows[DEMO["cases"][i]["file"]])) == (ok, total), i
    assert sum(len(p.flags) for p in rows[DEMO["cases"][9]["file"]].prose.values()) == 1
