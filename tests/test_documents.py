import pytest

from app import db
from app.review import SKIP_CONTRACT, doc_status
from tests.test_auth import ADMIN, REV, USER, client, env  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401

R = [REV, DOCS]


@pytest.mark.parametrize("args,want", [
    (("case", None, None, None, "queued"), ("Waiting for extraction", None)),
    (("case", "extracted", None, None, "running"), ("Waiting for extraction", None)),
    (("case", None, None, None, "failed"), ("Extraction failed", None)),
    (("case", "extracted", None, None, "done"), ("Case waiting for review", "case")),
    (("case", "approved", None, None, "done"), ("Approved", "case")),
    (("contract", "rejected", "untick", None, "done"), (SKIP_CONTRACT, None)),
    (("case", "rejected", None, None, "done"), ("Rejected in review", "case")),
    (("contract", None, None, None, None), (SKIP_CONTRACT, None)),
    (("proposal", None, None, None, None), ("Skipped: proposal or deck, not delivered work", None)),
    (("deck", None, None, None, "done"), ("Skipped: proposal or deck, not delivered work", None)),
    (("other", None, None, None, None), ("Skipped: not a case document", None)),
    (("case", "approved", None, 7, "done"), ("Part of a merged case", "/review/7")),
])
def test_doc_status(args, want):
    assert doc_status(*args) == want


def doc_of(cid):
    with db.connect() as c:
        return c.execute("select document_id from cases where id=%s", (cid,)).fetchone()[0]


def test_plain_user_forbidden(env):  # noqa: F811
    assert client([USER]).get("/documents").status_code == 403


def test_lists_only_documents_in_the_reviewers_groups_with_status(make):  # noqa: F811
    a = make(status="extracted")
    b = make(status="approved", due="30 days")
    hidden = make(acl=("g-other",))
    da = doc_of(a)
    with db.connect() as c:
        c.execute("update documents set title='Hidden doc' where id=%s", (doc_of(hidden),))
        c.execute("delete from cases where id=%s", (a,))
        c.execute("insert into jobs(kind,payload) values ('extract', jsonb_build_object('document_id', %s::bigint))", (da,))
    try:
        r = client(R).get("/documents")
        assert r.status_code == 200
        assert "Hidden doc" not in r.text
        assert "Waiting for extraction" in r.text and f'href="/review/{b}">Approved</a>' in r.text
    finally:
        with db.connect() as c:
            c.execute("delete from jobs where payload->>'document_id' = %s", (str(da),))


def test_skipped_contract_links_to_sources_for_admin_only(make):  # noqa: F811
    cid = make()
    with db.connect() as c:
        did = doc_of(cid)
        c.execute("delete from cases where id=%s", (cid,))
        c.execute("update documents set kind='contract' where id=%s", (did,))
    assert SKIP_CONTRACT in client([ADMIN, DOCS]).get("/documents").text
    assert 'href="/admin/sources">Sources</a>' in client([ADMIN, DOCS]).get("/documents").text
    assert 'href="/admin/sources">Sources</a>' not in client(R).get("/documents").text.split("<main")[1]


def test_merged_member_links_to_the_merged_case(make):  # noqa: F811
    parent, member = make(status="extracted"), make(status="approved", due="30 days")
    with db.connect() as c:
        c.execute("update cases set merged_into=%s where id=%s", (parent, member))
    t = client(R).get("/documents").text
    assert f'href="/review/{parent}">Part of a merged case</a>' in t


def test_order_follows_updated_at(make):  # noqa: F811
    old, new = make(), make()
    with db.connect() as c:
        c.execute("update documents set title='Older doc', updated_at=now()-interval '2 days' where id=%s", (doc_of(old),))
        c.execute("update documents set title='Newer doc' where id=%s", (doc_of(new),))
    t = client(R).get("/documents").text
    assert t.index("Newer doc") < t.index("Older doc")
