import json
import uuid

import pytest

from app import db
from app.schema import Outcome, ReferenceCase, Sourced
from tests.test_auth import ADMIN, ORIGIN, REV, USER, client, env  # noqa: F401

DOC = ("Acme cut customer onboarding from 12 days to 3 days. The programme was delivered "
       "for a large retail bank in the United Kingdom.")
Q_TITLE = "Acme cut customer onboarding from 12 days to 3 days"
Q_REGION = "for a large retail bank in the United Kingdom"
DOCS = "g-docs"  # ACL group on the fixture documents
R = [REV, DOCS]  # a reviewer who can open them


def case_data(**kw):
    base = dict(
        title=Sourced[str](value="Faster onboarding", source_quote=Q_TITLE),
        region=Sourced[str](value="United Kingdom", source_quote=Q_REGION),
        industry=Sourced[str](value="Aerospace", source_quote="made up passage that is not present", unsourced=True),
        outcomes=[Outcome(metric="onboarding", value="12 to 3 days", source_quote=Q_TITLE)],
        summary="Onboarding fell from 12 days to 3 days.",
        needs_attention=["check the summary"],
    )
    return ReferenceCase(**{**base, **kw}).model_dump_json()


@pytest.fixture
def make(env):  # noqa: F811
    db.init()
    sids = []

    def make(status="extracted", due=None, data=None, acl=(DOCS,), deleted=False):
        # the source gets no groups and the document gets `acl`: a later db.init() (schema repair, #58)
        # copies the source's '{}' onto the document, so don't call it after make()
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name) values ('s3',%s) returning id",
                            (uuid.uuid4().hex,)).fetchone()[0]
            sids.append(sid)
            did = c.execute("insert into documents(source_id,external_id,title,checksum,text,acl_groups,deleted_at) "
                            "values (%s,'k','Source doc','x',%s,%s,case when %s then now() end) returning id",
                            (sid, DOC, list(acl), deleted)).fetchone()[0]
            return c.execute(
                "insert into cases(document_id,status,data,review_due) values (%s,%s,%s::jsonb,"
                "now() + %s::interval) returning id",  # null interval -> null review_due
                (did, status, data or case_data(), due)).fetchone()[0]
    yield make
    with db.connect() as c:
        for sid in sids:
            c.execute("delete from sources where id=%s", (sid,))


def ver(cid):
    with db.connect() as c:
        return c.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()[0]


def row(cid):
    with db.connect() as c:
        return c.execute("select status, data, search_text, approved_by, review_due > now() + interval '364 days', "
                         "review_due is not null from cases where id=%s", (cid,)).fetchone()


def test_plain_user_forbidden(make):
    cid = make()
    c = client([USER])
    assert c.get("/review").status_code == 403
    assert c.get(f"/review/{cid}").status_code == 403
    assert c.post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 403
    assert c.get("/").status_code == 200


def test_list_shows_extracted_and_expired_only(make):
    new, expired = make(), make("approved", "-1 day")
    fresh = make("approved", "60 days")
    r = client(R).get("/review").text
    assert f"/review/{new}\"" in r and f"/review/{expired}\"" in r and f"/review/{fresh}\"" not in r


def test_detail_shows_basis_badge_and_reason(make):
    assert "Delivered case" in client(R).get(f"/review/{make()}").text
    cid = make(data=json.dumps({**json.loads(case_data()), "basis": "engagement", "basis_reason": "executed contract"}))
    page = client(R).get(f"/review/{cid}").text
    assert "Engagement (contracted scope)" in page and "executed contract" in page


def test_detail_hides_document_text_and_escapes_script(make):
    evil = "<script>alert(1)</script>"
    cid = make(data=case_data(title=Sourced[str](value=evil, source_quote=evil)))
    r = client(R).get(f"/review/{cid}").text
    assert "<script>alert" not in r and "&lt;script&gt;" in r
    assert "United Kingdom" in r and "check the summary" in r and "unsourced" in r
    assert DOC not in r  # full text is not shown


def test_edit_with_bad_quote_is_unsourced_whatever_the_form_says(make):
    cid = make()
    c = client(R)
    r = c.post(f"/review/{cid}/edit", follow_redirects=False,
               data={"field": "industry", "value": "Banking", "quote": "this passage is not in the document",
                     "unsourced": "false", "v": ver(cid)})
    assert r.status_code == 303
    assert row(cid)[1]["industry"]["unsourced"] is True
    c.post(f"/review/{cid}/edit", data={"field": "industry", "value": "Banking", "v": ver(cid),
                                        "quote": "for a large retail bank in the United Kingdom"})
    d = row(cid)
    assert d[1]["industry"]["unsourced"] is False and "Banking" in d[2]


def test_approve_empties_unsourced_and_sets_review_due(make):
    cid = make()
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}, follow_redirects=False)
    assert r.status_code == 303
    status, data, search_text, by, due_ok, _ = row(cid)
    assert (status, by, due_ok) == ("approved", "u1", True)
    assert data["industry"]["value"] is None and data["needs_attention"] == []
    assert "Aerospace" not in search_text and "Faster onboarding" in search_text


def test_approve_refused_without_title(make):
    cid = make(data=case_data(title=Sourced[str](value="X", source_quote="nope nope nope nope", unsourced=True)))
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 400
    assert row(cid)[0] == "extracted"


def test_approving_decided_case_is_409(make):
    cid = make("approved", "30 days")
    c = client(R)
    assert c.post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 409
    assert c.post(f"/review/{cid}/edit", data={"field": "summary", "value": "x", "v": ver(cid)}).status_code == 409
    assert c.post("/review/999999999/approve", data={"v": "x"}).status_code == 404


def test_reject(make):
    cid = make()
    assert client(R).post(f"/review/{cid}/reject", data={"v": ver(cid)}, follow_redirects=False).status_code == 303
    assert row(cid)[0] == "rejected"
    assert client(R).post(f"/review/{cid}/reject", data={"v": ver(cid)}).status_code == 409


def test_browser_without_session_goes_to_login(env):  # noqa: F811
    r = client().get("/review", headers={"Accept": "text/html,*/*"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client().get("/review").status_code == 401


def test_reviewer_without_document_access_sees_nothing(make):
    hidden = make(acl=("g-board",))
    gone = make(deleted=True)
    c = client(R)
    page = c.get("/review").text
    assert f"/review/{hidden}\"" not in page and f"/review/{gone}\"" not in page
    for cid in (hidden, gone):  # 404, never confirming the case exists
        assert c.get(f"/review/{cid}").status_code == 404
        assert c.post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 404
        assert row(cid)[0] == "extracted"


def test_changed_case_cannot_be_approved_unseen(make):
    cid = make()
    seen = ver(cid)
    with db.connect() as c:  # a re-extraction lands after the reviewer loaded the page
        c.execute("update cases set data = data || '{\"summary\": \"new text\"}' where id=%s", (cid,))
    r = client(R).post(f"/review/{cid}/approve", data={"v": seen})
    assert r.status_code == 409 and "reload" in r.text
    assert row(cid)[0] == "extracted"


def test_detail_forms_carry_version_and_row_ids(make):
    cid = make()
    page = client(R).get(f"/review/{cid}").text
    assert page.count(f'name="v" value="{ver(cid)}"') >= 3
    assert 'form="f1"' in page and 'id="f1"' in page and "<tr>\n<form" not in page


def test_approve_links_case_to_registry_client(make):
    tag = uuid.uuid4().hex[:8]
    with db.connect() as c:
        client_id = c.execute("insert into clients(name, aliases, anonymised_label) "
                              "values (%s, '{Acme}', 'a bank') returning id", (f"Acme {tag}",)).fetchone()[0]
    try:  # the mention must be sourced, or approval empties it (and nothing is linked)
        cid = make(data=case_data(client_mention=Sourced[str](value="Acme", source_quote=Q_TITLE)))
        client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)})
        with db.connect() as c:
            assert c.execute("select client_id from cases where id=%s", (cid,)).fetchone()[0] == client_id
    finally:
        with db.connect() as c:
            c.execute("update cases set client_id=null where client_id=%s", (client_id,))
            c.execute("delete from clients where id=%s", (client_id,))


def post(c, cid, **f):
    return c.post(f"/review/{cid}/edit", follow_redirects=False, data={"v": ver(cid), **f})


def test_add_capability_with_document_quote_is_sourced(make):
    cid = make()
    assert post(client(R), cid, field="capabilities.new", value="Customer onboarding", quote=Q_TITLE).status_code == 303
    _, data, search_text, *_ = row(cid)
    assert data["capabilities"][-1]["unsourced"] is False and "Customer onboarding" in search_text


def test_add_outcome_with_foreign_quote_is_dropped_on_approval(make):
    cid = make()
    c = client(R)
    q = "cost fell by thirty percent overall"
    assert post(c, cid, field="outcomes.new", metric="cost", value="30 percent", quote=q).status_code == 303
    assert row(cid)[1]["outcomes"][-1]["unsourced"] is True
    assert c.post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 200  # after the redirect
    assert [o["metric"] for o in row(cid)[1]["outcomes"]] == ["onboarding"]


def test_add_needs_value_and_quote(make):
    cid = make()
    before = row(cid)[1]
    c = client(R)
    for f in (dict(field="capabilities.new", value="X", quote=""), dict(field="capabilities.new", value="", quote=Q_TITLE),
              dict(field="outcomes.new", value="x", quote=Q_TITLE),
              dict(field="capabilities.new", value="X", quote="   "), dict(field="outcomes.-1", value="x")):
        assert post(c, cid, **f).status_code == 400
    assert row(cid)[1] == before


def test_remove_list_item(make):
    cid = make(data=case_data(tech_stack=[Sourced[str](value="Acme", source_quote=Q_TITLE)]))
    assert post(client(R), cid, action="remove", field="tech_stack.0").status_code == 303
    assert row(cid)[1]["tech_stack"] == []


def test_remove_only_list_items(make):
    cid = make()
    c = client(R)
    for f in ("industry", "capabilities.new", "outcomes.-1"):
        assert post(c, cid, action="remove", field=f).status_code == 400
    assert post(c, cid, action="bogus", field="industry").status_code == 400


def test_double_remove_is_409(make):
    cid = make()
    c = client(R)
    v = ver(cid)
    assert c.post(f"/review/{cid}/edit", data={"action": "remove", "field": "outcomes.0", "v": v},
                  follow_redirects=False).status_code == 303
    assert c.post(f"/review/{cid}/edit", data={"action": "remove", "field": "outcomes.0", "v": v}).status_code == 409


def test_detail_has_add_rows_and_remove_buttons(make):
    page = client(R).get(f"/review/{make()}").text
    assert all(p in page for p in ("capabilities.new", "tech_stack.new", "outcomes.new"))
    assert page.count('value="remove"') == 1


def test_no_add_or_remove_buttons_when_not_reviewable(make):
    page = client(R).get(f"/review/{make('approved', '30 days')}").text
    assert 'value="remove"' not in page and ">Add<" not in page
