import uuid
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
