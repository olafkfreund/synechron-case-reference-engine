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
        c.execute("delete from model_approvals where model = 'mcp-client:local-dev-review'")


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
    with db.connect() as c:  # one live 30-day approval for the dev reviewer client, not one per run
        assert c.execute("select count(*) from model_approvals where model = 'mcp-client:local-dev-review' and data_class = 'confidential' "
                         "and expires_at > now() + interval '29 days'").fetchone()[0] == 1

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


def test_refresh_reopens_a_merged_case(env, tmp_path, monkeypatch):
    demo = copy.deepcopy(DEMO)
    demo["cases"] = demo["cases"][12:14]  # the two Fabrikam contracts
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 0
    q = "select c.id, c.data from cases c join documents d on d.id=c.document_id where d.title=%s"
    with db.connect() as c:
        ids = [c.execute(q, (e["file"],)).fetchone() for e in demo["cases"]]
        merged = c.execute("insert into cases(document_id, member_count, basis, status, data, summary, search_text) "
                           "values (null, 2, 'engagement', 'approved', %s, '', '') returning id",
                           (json.dumps(ids[0][1]),)).fetchone()[0]
        c.execute("update cases set merged_into=%s, merged_version=md5(data::text) where id = any(%s)", (merged, [i for i, _ in ids]))
    demo["cases"][0]["paragraphs"].append("A further note added to the contract for the refresh.")
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 0
    with db.connect() as c:
        assert c.execute("select status from cases where id=%s", (merged,)).fetchone()[0] == "extracted"
        assert c.execute("select merged_into from cases where id=%s", (ids[0][0],)).fetchone()[0] == merged
        assert "further note" in c.execute("select text from documents where title=%s", (demo["cases"][0]["file"],)).fetchone()[0]
        c.execute("delete from cases where id=%s", (merged,))  # the clean-up deletes sources only


def _bad_quote(d):
    d["cases"][1]["case"]["title"]["source_quote"] = "A sentence that is nowhere in this document."


def _reviewer_flag(d):  # a number no quote supports, in reviewer prose
    d["cases"][1]["case"]["prose"]["challenge"]["text"] += " It affected 999 customers."


def _unexpected_flag(d):  # a flagged section that is not marked expect_flag
    p = d["cases"][1]["case"]["prose"]["solution"]
    p["by_reviewer"], p["text"] = False, p["text"] + " Zorblax helped."


def _flag_expected_but_clean(d):
    d["cases"][1]["expect_flag"] = True
    d["cases"][1]["case"]["prose"]["solution"]["by_reviewer"] = False


def _planted_outside_region(d):  # only region may carry the planted unsourced exemption
    d["cases"][1]["case"]["industry"]["unsourced"] = True
    d["cases"][1]["case"]["industry"]["source_quote"] = "Not in the document at all."


def _unsupported_architecture(d):  # a name no point supports
    d["cases"][1]["case"]["architecture"]["components"][0]["name"] = "Zorblax store"


def _too_few_technologies(d):  # case 0 has a diagram and a candidate image to draw
    d["cases"][0]["case"]["tech_stack"] = d["cases"][0]["case"]["tech_stack"][:1]


@pytest.mark.parametrize("mutate,word", [(_bad_quote, "title"), (_reviewer_flag, "999"), (_unexpected_flag, "Zorblax"),
                                         (_flag_expected_but_clean, "expect_flag"), (_planted_outside_region, "industry"),
                                         (_unsupported_architecture, "Zorblax"),
                                         (_too_few_technologies, "tech_stack")])
def test_failed_validation_writes_nothing(env, tmp_path, monkeypatch, capsys, mutate, word):
    demo = subset()
    mutate(demo)
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 2
    err = capsys.readouterr().err
    assert word in err
    with db.connect() as c:
        assert c.execute("select count(*) from sources where name like 'Demo: %'").fetchone()[0] == 0
    assert counts()[:2] == (0, 0)


def test_completeness_mix(env):
    assert seed.main() == 0
    want = {i: (9, 9) for i in (0, 1, 2, 3, 5, 6, 7, 8)} | {4: (7, 9), 10: (8, 9), 11: (8, 9)}  # 4 has no picture: its architecture meets "diagram"
    with db.connect() as c:
        rows = {r[0]: ReferenceCase.model_validate(r[1]) for r in c.execute(
            "select d.title, c.data from cases c join documents d on d.id=c.document_id join sources s on s.id=d.source_id "
            "where s.name like 'Demo: %'").fetchall()}
    for i, (ok, total) in want.items():
        assert score(checklist(rows[DEMO["cases"][i]["file"]])) == (ok, total), i
    assert sum(len(p.flags) for p in rows[DEMO["cases"][9]["file"]].prose.values()) == 1
    assert [s for s, p in rows[DEMO["cases"][9]["file"]].prose.items() if p.flags] == ["solution"]
    with db.connect() as c:
        ids = {r[0]: r[1] for r in c.execute(
            "select d.title, c.id from cases c join documents d on d.id=c.document_id join sources s on s.id=d.source_id "
            "where s.name like 'Demo: %'").fetchall()}
        for e in DEMO["cases"]:
            n = c.execute("select count(*) from diagrams where case_id=%s and created_by='demo-seed'", (ids[e["file"]],)).fetchone()[0]
            assert n == (1 if e.get("diagram") else 0), e["file"]
        assert c.execute("select count(*) from case_approval_overrides where case_id = any(%s)", (list(ids.values()),)).fetchone()[0] == 1


def test_every_approved_demo_case_has_a_checked_architecture(env):
    from app.schema import check_architecture
    assert seed.main() == 0
    with db.connect() as c:
        rows = {r[0]: ReferenceCase.model_validate(r[1]) for r in c.execute(
            "select d.title, c.data from cases c join documents d on d.id=c.document_id join sources s on s.id=d.source_id "
            "where s.name like 'Demo: %' and c.status='approved'").fetchall()}
    assert len(rows) == 9
    for name, case in rows.items():
        a = case.architecture
        assert a and 6 <= len(a.components) <= 10 and 6 <= len(a.flows) <= 12 and 1 <= len(a.callouts) <= 2, name
        assert check_architecture(case) == [] and not a.flags, name
        assert a.title in case.search_text()


def test_seed_refreshes_only_the_architecture_of_an_unchanged_document(env, tmp_path, monkeypatch, capsys):
    demo = subset()
    use(tmp_path, monkeypatch, demo)
    assert seed.main() == 0
    q = ("select d.updated_at, c.id, c.data from documents d join cases c on c.document_id=d.id join sources s on s.id=d.source_id "
         "where s.name like 'Demo: %%' and d.title=%s")
    with db.connect() as c:
        first = c.execute(q, (demo["cases"][0]["file"],)).fetchone()
        other = c.execute(q, (demo["cases"][1]["file"],)).fetchone()
        c.execute("update cases set data = jsonb_set(data, '{summary}', '\"hand edited\"') where id=%s", (first[1],))
    demo["cases"][0]["case"]["architecture"]["title"] = "Claims intake, refreshed"
    use(tmp_path, monkeypatch, demo)
    capsys.readouterr()
    assert seed.main() == 0
    assert "1 architecture(s) refreshed" in capsys.readouterr().out
    with db.connect() as c:
        again = c.execute(q, (demo["cases"][0]["file"],)).fetchone()
        assert again[0] == first[0] and again[1] == first[1]  # the document is untouched
        assert again[2]["architecture"]["title"] == "Claims intake, refreshed" and again[2]["summary"] == "hand edited"
        assert "refreshed" in c.execute("select search_text from cases where id=%s", (first[1],)).fetchone()[0]
        assert c.execute(q, (demo["cases"][1]["file"],)).fetchone() == other
    assert seed.main() == 0 and "refreshed" not in capsys.readouterr().out  # nothing left to refresh


def test_first_demo_source_is_the_staff_source_even_on_an_old_database(env):
    first = DEMO["sources"][0]["name"]
    assert seed.main() == 0
    with db.connect() as c:
        c.execute("update sources set config = config - 'staff' where name=%s", (first,))  # a database seeded before #249
    assert seed.main() == 0
    with db.connect() as c:
        got = dict(c.execute("select name, coalesce(config->>'staff', '') from sources where name like 'Demo: %'").fetchall())
    assert got[first] == "true" and [n for n, v in got.items() if v] == [first]


def test_compose_mock_idp_gives_the_review_client_its_scope():
    import yaml
    cfg = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    maps = json.loads(cfg["services"]["idp"]["environment"]["JSON_CONFIG"])["tokenCallbacks"][0]["requestMappings"]
    by = {m["match"]: m["claims"] for m in maps}
    assert by["local-dev-review"]["scp"] == "refs.access refs.review" and by["local-dev-review"]["azp"] == "local-dev-review"
    assert by["local-dev"]["scp"] == "refs.access"
    assert {"local-dev", "local-dev-review"} <= set(cfg["services"]["web"]["environment"]["MCP_CLIENT_IDS"].split(","))
