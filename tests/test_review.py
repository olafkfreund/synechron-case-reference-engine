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

    def make(status="extracted", due=None, data=None, acl=(DOCS,), deleted=False, basis="delivered", mention=None,
             text=DOC):
        # the source gets the same groups as the document: client() calls db.init(), and its schema repair (#58)
        # copies the source's groups onto the document
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,acl_groups) values ('s3',%s,%s) returning id",
                            (uuid.uuid4().hex, list(acl))).fetchone()[0]
            sids.append(sid)
            did = c.execute("insert into documents(source_id,external_id,title,checksum,text,acl_groups,deleted_at) "
                            "values (%s,'k','Source doc','x',%s,%s,case when %s then now() end) returning id",
                            (sid, text, list(acl), deleted)).fetchone()[0]
            extra = dict(basis=basis, **({"client_mention": Sourced[str](value=mention, source_quote=Q_TITLE)} if mention else {}))
            return c.execute(
                "insert into cases(document_id,status,data,review_due,basis) values (%s,%s,%s::jsonb,"
                "now() + %s::interval,%s) returning id",  # null interval -> null review_due
                (did, status, data or case_data(**extra), due, basis)).fetchone()[0]
    yield make
    with db.connect() as c:
        for sid in sids:  # merged rows first: deleting a member alone would orphan its merged row
            c.execute("delete from cases where document_id is null and id in (select merged_into from cases "
                      "where document_id in (select id from documents where source_id=%s))", (sid,))
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


SOURCED = dict(industry=Sourced[str](value="Aerospace", source_quote=Q_TITLE))  # found by in_search()


def in_search(cid):
    return f'name="case_id" value="{cid}"' in client([USER, DOCS]).post("/search", data={"industry": "Aerospace"}).text


def test_due_soon_case_can_be_reapproved_and_stays_searchable(make):
    # a sourced industry: approve strips unsourced fields, which would drop the case from this search
    cid = make("approved", "10 days", data=case_data(**SOURCED))
    page = client(R).get(f"/review/{cid}").text
    assert f'action="/review/{cid}/approve"' in page and "Approving it again renews it" in page
    assert in_search(cid)
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}, follow_redirects=False).status_code == 303
    assert row(cid)[0] == "approved" and row(cid)[4] and row(cid)[3]  # renewed, by this reviewer
    assert in_search(cid)


def test_edit_on_due_soon_case_withdraws_approval(make):
    cid = make("approved", "10 days", data=case_data(**SOURCED))
    assert in_search(cid)
    r = client(R).post(f"/review/{cid}/edit", data={"field": "summary", "value": "Edited.", "v": ver(cid)},
                       follow_redirects=False)  # a field search does not filter on
    assert r.status_code == 303 and row(cid)[0] == "extracted" and not row(cid)[5]
    assert f'/review/{cid}"' in client(R).get("/review").text
    assert not in_search(cid)


def test_expired_case_keeps_todays_behaviour(make):
    cid = make("approved", "-1 day", data=case_data(**SOURCED))
    page = client(R).get(f"/review/{cid}").text
    assert f'action="/review/{cid}/approve"' in page and "in search until" not in page
    r = client(R).post(f"/review/{cid}/edit", data={"field": "summary", "value": "Edited.", "v": ver(cid)},
                       follow_redirects=False)
    assert r.status_code == 303 and row(cid)[0] == "approved" and row(cid)[5]  # still expired, not withdrawn
    later = make("approved", "31 days")  # outside the 30-day window: not open yet
    assert f'action="/review/{later}/approve"' not in client(R).get(f"/review/{later}").text


def test_due_soon_case_not_listed_twice(make):
    cid = make("approved", "10 days")
    t = client(R).get("/review").text
    assert t.count(f'/review/{cid}"') == 1
    assert t.index(f'/review/{cid}"') > t.index("Due for re-review within 30 days")


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
    cid = make("approved", "60 days")
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


def test_approve_409_if_client_deleted_mid_approval(make, monkeypatch):  # #84
    from app import anonymise
    tag = uuid.uuid4().hex[:8]
    with db.connect() as c:
        client_id = c.execute("insert into clients(name, aliases, anonymised_label) "
                              "values (%s, '{Acme}', 'a bank') returning id", (f"Acme {tag}",)).fetchone()[0]
    real = anonymise.load_clients

    def load_then_delete(conn):  # an admin deletes the client after approve has read the registry
        clients = real(conn)
        with db.connect() as other:
            other.execute("delete from clients where id=%s", (client_id,))
        return clients
    monkeypatch.setattr(anonymise, "load_clients", load_then_delete)
    try:
        cid = make(data=case_data(client_mention=Sourced[str](value="Acme", source_quote=Q_TITLE)))
        before = ver(cid)
        r = client(R).post(f"/review/{cid}/approve", data={"v": before})
        assert r.status_code == 409 and "client changed" in r.text
        status, data, *_ = row(cid)
        with db.connect() as c:
            linked = c.execute("select client_id from cases where id=%s", (cid,)).fetchone()[0]
        assert (status, linked, ver(cid)) == ("extracted", None, before)  # nothing written
    finally:
        with db.connect() as c:
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


def test_engagement_add_outcome_refused(make):  # #139
    cid = make(basis="engagement")
    before = row(cid)[1]["outcomes"]
    r = post(client(R), cid, field="outcomes.new", metric="cost", value="30 percent", quote=Q_TITLE)
    assert r.status_code == 400
    assert row(cid)[1]["outcomes"] == before


def test_engagement_existing_outcome_can_be_removed_not_edited(make):  # #139
    cid = make(basis="engagement")
    before = row(cid)[1]["outcomes"]
    assert before  # the fixture's outcome, from before the fix
    r = post(client(R), cid, field="outcomes.0", metric="cost", value="40 percent", quote=Q_TITLE)
    assert r.status_code == 400 and row(cid)[1]["outcomes"] == before
    r = client(R).post(f"/review/{cid}/edit", data={"field": "outcomes.0", "action": "remove", "v": ver(cid)},
                       follow_redirects=False)
    assert r.status_code == 303 and row(cid)[1]["outcomes"] == []


def test_engagement_review_page_has_no_add_outcome(make):  # #139
    assert "outcomes.new" not in client(R).get(f"/review/{make(basis='engagement')}").text
    assert "outcomes.new" in client(R).get(f"/review/{make()}").text  # guard against a vacuous pass


def test_approve_engagement_drops_outcomes(make):  # #139
    cid = make(basis="engagement")
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 200
    status, data, *_ = row(cid)
    assert status == "approved" and data["outcomes"] == []


def test_add_needs_value_and_quote(make):
    cid = make()
    before = row(cid)[1]
    c = client(R)
    for f in (dict(field="capabilities.new", value="X", quote=""), dict(field="capabilities.new", value="", quote=Q_TITLE),
              dict(field="outcomes.new", value="x", quote=Q_TITLE),
              dict(field="capabilities.new", value="X", quote="   "), dict(field="outcomes.-1", value="x")):
        assert post(c, cid, **f).status_code == 400
    assert row(cid)[1] == before


def test_whitespace_metric_stored_empty(make):
    from app.render import section
    cid = make()
    assert post(client(R), cid, field="outcomes.0", metric="   ", value=" 12 to 3 days ", quote=Q_TITLE).status_code == 303
    _, data, text = row(cid)[:3]
    assert (data["outcomes"][0]["metric"], data["outcomes"][0]["value"]) == ("", "12 to 3 days")
    lists = section(ReferenceCase.model_validate(data), "Client")["lists"]
    assert [x["bullets"] for x in lists if x["heading"] == "Outcomes"] == [["12 to 3 days"]]
    assert "12 to 3 days" in text


def test_whitespace_value_not_sourced_and_dropped_on_approval(make):
    cid = make()
    c = client(R)
    assert post(c, cid, field="industry", value="   ", quote=Q_REGION).status_code == 303
    ind = row(cid)[1]["industry"]
    assert ind["value"] is None and ind["unsourced"] is False
    assert c.post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 200  # after the redirect
    assert row(cid)[0] == "approved" and row(cid)[1]["industry"]["value"] is None


def test_padded_add_is_trimmed(make):
    cid = make()
    assert post(client(R), cid, field="outcomes.new", metric=" Revenue ", value=" 30% ", quote=Q_TITLE).status_code == 303
    o = row(cid)[1]["outcomes"][-1]
    assert (o["metric"], o["value"]) == ("Revenue", "30%")


def test_edit_outcome_without_value_refused(make):
    cid = make()
    before = row(cid)[1]
    c = client(R)
    for f in (dict(metric="onboarding", value="  "), dict(metric="", value=""),
              dict(metric="onboarding")):  # no value field: used to wipe the stored value
        assert post(c, cid, field="outcomes.0", quote=Q_TITLE, **f).status_code == 400
    assert row(cid)[1] == before


def test_stored_empty_outcome_dropped_on_approval(make):
    cid = make(data=case_data(outcomes=[Outcome(metric="Revenue", value="", source_quote=Q_TITLE),
                                        Outcome(metric="onboarding", value="12 to 3 days", source_quote=Q_TITLE)]))
    client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)})
    assert row(cid)[0] == "approved"
    assert [(o["metric"], o["value"]) for o in row(cid)[1]["outcomes"]] == [("onboarding", "12 to 3 days")]


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
    page = client(R).get(f"/review/{make('approved', '60 days')}").text
    assert 'value="remove"' not in page and ">Add<" not in page


# ---- merging engagements (#55) ----

@pytest.fixture
def acme(make):
    name = f"Acme {uuid.uuid4().hex[:8]}"
    with db.connect() as c:
        cid = c.execute("insert into clients(name, aliases, anonymised_label) values (%s, '{Acme}', 'a bank') returning id",
                        (name,)).fetchone()[0]
    yield cid
    with db.connect() as c:
        c.execute("update cases set client_id=null where client_id=%s", (cid,))  # set by approving a merged case
        c.execute("delete from clients where id=%s", (cid,))


TEXT_A = "Acme cut customer onboarding from 12 days to 3 days. Built with Python on AWS for a UK bank."
TEXT_B = "Acme moved payments to the cloud. Built with Rust and Python on Azure for a UK bank."


def eng(make, text, caps, tech, mention="Acme", **kw):
    q = text.split(".")[0]
    d = case_data(title=Sourced[str](value=f"T {q[:12]}", source_quote=q),
                  client_mention=Sourced[str](value=mention, source_quote=q), outcomes=[],
                  capabilities=[Sourced[str](value=v, source_quote=q) for v in caps],
                  tech_stack=[Sourced[str](value=v, source_quote=v) for v in tech],
                  basis="engagement", basis_reason="executed contract", organisations=["Acme"])
    return make(basis="engagement", data=d, text=text, **kw)


def merge(c, ids, **pick):
    return c.post("/review/merge", follow_redirects=False,
                  data={"members": [f"{i}:{ver(i)}" for i in ids], **{f"pick_{k}": v for k, v in pick.items()}})


def merged_of(cid):
    with db.connect() as c:
        return c.execute("select merged_into from cases where id=%s", (cid,)).fetchone()[0]


def _merged(make):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(client(R), [a, b]).headers["location"].rsplit("/", 1)[1])
    return new, b


def _data_and_doc(cid, member=None):
    with db.connect() as c:
        data = c.execute("select data from cases where id=%s", (cid,)).fetchone()[0]
        doc = c.execute("select document_id from cases where id=%s", (member,)).fetchone()[0] if member else None
    return data, doc


def test_merged_edit_can_quote_another_member(make, acme):
    new, b = _merged(make)
    client(R).post(f"/review/{new}/edit", data={"field": "industry", "value": "Payments",
                   "quote": "Acme moved payments to the cloud", "v": ver(new)})
    data, doc_b = _data_and_doc(new, b)
    assert data["industry"]["unsourced"] is False and data["industry"]["document_id"] == doc_b


def test_merged_edit_quote_in_no_member_is_unsourced(make, acme):
    new, _ = _merged(make)
    client(R).post(f"/review/{new}/edit", data={"field": "industry", "value": "Payments",
                   "quote": "this sentence is in neither of the two contracts", "v": ver(new)})
    data, _ = _data_and_doc(new)
    assert data["industry"]["unsourced"] is True and data["industry"]["document_id"] is None


def test_merged_edit_same_quote_keeps_origin(make, acme):
    new, b = _merged(make)
    _, doc_b = _data_and_doc(new, b)
    q = "for a UK bank"  # in both members: clearing the origin would move it to the first, a
    with db.connect() as c:
        c.execute("update cases set data = jsonb_set(jsonb_set(data, '{title,document_id}', to_jsonb(%s::bigint)), "
                  "'{title,source_quote}', to_jsonb(%s::text)) where id=%s", (doc_b, q, new))
    client(R).post(f"/review/{new}/edit", data={"field": "title", "value": "UK bank", "quote": "for a\r\nUK  bank",
                                                "v": ver(new)})  # the same quote, as a textarea posts it
    after, _ = _data_and_doc(new)
    assert after["title"]["value"] == "UK bank" and after["title"]["document_id"] == doc_b


def test_merge_combines_checked_fields(make, acme):
    a = eng(make, TEXT_A, ["Onboarding", "Payments"], ["Python", "AWS"])
    b = eng(make, TEXT_B, ["payments", "Cloud"], ["Rust", "python"])
    r = merge(client(R), [a, b], title=b)
    assert r.status_code == 303
    new = int(r.headers["location"].rsplit("/", 1)[1])
    assert merged_of(a) == merged_of(b) == new
    with db.connect() as c:
        status, mc, did, data, summary = c.execute(
            "select status, member_count, document_id, data, summary from cases where id=%s", (new,)).fetchone()
        docs = dict(c.execute("select id, document_id from cases where id = any(%s)", ([a, b],)).fetchall())
    assert (status, mc, did, summary) == ("extracted", 2, None, "")
    assert data["title"]["value"].startswith("T Acme moved") and data["title"]["document_id"] == docs[b]
    assert [(x["value"], x["document_id"]) for x in data["capabilities"]] == [
        ("Onboarding", docs[a]), ("Payments", docs[a]), ("Cloud", docs[b])]  # "payments" is a duplicate
    assert [x["value"] for x in data["tech_stack"]] == ["Python", "AWS", "Rust"]
    assert data["outcomes"] == [] and data["basis"] == "engagement" and not data["title"]["unsourced"]
    assert "merged from 2 contracts: write a summary" in data["needs_attention"]
    assert data["basis_reason"] == "merged: executed contract"
    assert all(not x["unsourced"] for x in data["capabilities"])
    # members leave the review list and the detail page; the merged case replaces them
    page = client(R).get("/review").text
    assert f"/review/{new}\"" in page and f"/review/{a}\"" not in page and f"/review/{b}\"" not in page
    assert client(R).get(f"/review/{a}").status_code == 404 and client(R).get(f"/review/{new}").status_code == 200


def _rc(**kw):
    return ReferenceCase.model_validate_json(case_data(**kw))


def test_combine_prefers_sourced_scalar():
    """Unpicked, a merge takes the sourced copy, not the first member's unsourced one (#140)."""
    from app.review import combine
    a = _rc(title=Sourced[str](value="Same", source_quote="q", unsourced=True))
    b = _rc(title=Sourced[str](value="Same", source_quote="q"))
    members = [(1, 10, a), (2, 20, b)]
    t = combine(members, {}).title
    assert not t.unsourced and t.document_id == 20
    t = combine(members, {"title": 1}).title  # a reviewer's pick still wins
    assert t.unsourced and t.document_id == 10
    t = combine([(1, 10, a), (2, 20, a)], {}).title  # all unsourced: the first member
    assert t.unsourced and t.document_id == 10


def test_combine_dedupe_keeps_sourced_copy():
    """A duplicate keeps its place but takes the sourced copy and its document (#140)."""
    from app.review import combine
    a = _rc(capabilities=[Sourced[str](value="Onboarding", source_quote="q"),
                          Sourced[str](value="Payments", source_quote="q", unsourced=True)])
    b = _rc(capabilities=[Sourced[str](value="payments", source_quote="q")])
    caps = combine([(1, 10, a), (2, 20, b)], {}).capabilities
    assert [x.value for x in caps] == ["Onboarding", "payments"]  # A's position, B's copy and spelling
    assert not caps[1].unsourced and caps[1].document_id == 20


def test_merge_refusals(make, acme):
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    other = eng(make, TEXT_B, ["y"], [], mention="Globex")
    nobody = eng(make, TEXT_B, ["y"], [], mention="Nobody Ltd")
    delivered = make(mention="Acme")
    rejected = eng(make, TEXT_B, ["y"], [], status="rejected")
    hidden = eng(make, TEXT_B, ["y"], [], acl=("g-board",))
    assert merge(c, [a]).status_code == 400  # fewer than 2
    assert merge(c, [a, a]).status_code == 400
    assert c.post("/review/merge", data={"members": ["x:y", f"{a}:1"]}).status_code == 400  # malformed
    for bad in (other, nobody, delivered, rejected):
        assert merge(c, [a, bad]).status_code == 400, bad
    assert merge(c, [a, hidden]).status_code == 404
    assert c.post("/review/merge", data={"members": [f"{a}:{ver(a)}", f"{10**9}:x"]}).status_code == 404
    assert c.post("/review/merge", data={"members": [f"{a}:stale", f"{b}:{ver(b)}"]}).status_code == 409
    assert merge(c, [a, b], title=other).status_code == 400  # a pick that is not a member
    assert merge(c, [a, b], title="x").status_code == 400
    assert merge(client([USER]), [a, b]).status_code == 403
    assert merged_of(a) is None
    assert merge(c, [a, b]).status_code == 303
    third = eng(make, TEXT_B, ["y"], [])
    assert merge(c, [a, third]).status_code == 409  # a is already merged


def test_candidate_form_lists_only_same_client_engagements(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    other = eng(make, TEXT_B, ["y"], [], mention="Globex")
    delivered = make(mention="Acme")
    page = client(R).get(f"/review/{a}").text
    assert f'value="{a}:{ver(a)}"' in page and f'value="{b}:{ver(b)}"' in page
    assert f'value="{other}:' not in page and f'value="{delivered}:' not in page
    assert "<script" not in page and "onclick" not in page
    assert "/review/merge/preview" not in client(R).get(f"/review/{other}").text  # unregistered client


def test_preview_has_one_radio_group_per_differing_field(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    r = client(R).post("/review/merge/preview", data={"members": [f"{a}:{ver(a)}", f"{b}:{ver(b)}"]})
    assert r.status_code == 200
    page = r.text
    assert page.count('name="pick_title"') == 2 and page.count(f'value="{a}" checked') >= 1
    assert 'name="pick_client_mention"' not in page  # identical in both
    assert page.count(f'name="members" value="{a}:{ver(a)}"') == 1 and "<script" not in page
    assert client(R).post("/review/merge/preview", data={"members": [f"{a}:{ver(a)}"]}).status_code == 400


def test_preview_preselects_the_sourced_copy(make, acme):
    """The radio the browser sends by default is the sourced copy, as combine's default is (#140)."""
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    with db.connect() as c:  # a's industry stays unsourced (case_data); b's is sourced and differs
        c.execute("""update cases set data = jsonb_set(data, '{industry}', '{"value": "Banking", "source_quote": "a UK bank", "unsourced": false}') where id=%s""", (b,))
    page = client(R).post("/review/merge/preview", data={"members": [f"{a}:{ver(a)}", f"{b}:{ver(b)}"]}).text
    group = page.split("<legend>industry</legend>", 1)[1].split("</fieldset>", 1)[0]
    assert f'value="{b}" checked' in group and f'value="{a}" checked' not in group


def test_combine_default_skips_an_empty_copy():
    from app.review import combine
    a = _rc(title=Sourced[str](value="X", source_quote="q", unsourced=True))
    empty, c = _rc(title=Sourced[str](value="", source_quote="")), _rc(title=Sourced[str](value="X", source_quote="q"))
    t = combine([(1, 10, a), (2, 20, empty), (3, 30, c)], {}).title
    assert t.document_id == 30 and not t.unsourced


def test_merged_detail_and_list_show_members(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(client(R), [a, b]).headers["location"].rsplit("/", 1)[1])
    page = client(R).get(f"/review/{new}").text
    assert "Merged from 2 contracts" in page and page.count("Source doc") >= 2
    assert "<small>(Source doc)</small>" in page and "<script" not in page
    assert "/review/merge/preview" not in page
    assert "2 contracts" in client(R).get("/review").text


def status_of(cid):
    with db.connect() as c:
        return c.execute("select status from cases where id=%s", (cid,)).fetchone()[0]


@pytest.mark.parametrize("approved", [False, True])
def test_unmerge_returns_members_to_the_queue(make, acme, approved):
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    if approved:
        with db.connect() as d:
            d.execute("update cases set status='approved', approved_by='u', approved_at=now(), "
                      "review_due=now() + interval '60 days' where id=%s", (new,))
    assert f"/review/{new}/unmerge" in c.get(f"/review/{new}").text
    assert c.post(f"/review/{new}/unmerge", data={"v": "stale"}).status_code == 409
    assert c.post(f"/review/{new}/unmerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    assert (status_of(a), status_of(b), status_of(new)) == ("extracted", "extracted", "rejected")
    assert merged_of(a) is None and merged_of(b) is None
    page = c.get("/review").text
    assert f"/review/{a}\"" in page and f"/review/{b}\"" in page and f"/review/{new}\"" not in page
    assert c.get(f"/review/{new}").status_code == 404
    assert c.post(f"/review/{new}/unmerge", data={"v": ver(new)}).status_code == 404


def test_merged_case_cannot_be_approved_with_a_rejected_member_or_rejected(make, acme):
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    assert c.post(f"/review/{new}/reject", data={"v": ver(new)}).status_code == 400
    with db.connect() as d:
        d.execute("update cases set status='rejected' where id=%s", (b,))
    assert c.post(f"/review/{new}/approve", data={"v": ver(new)}).status_code == 409
    assert status_of(new) == "extracted"


# ---- need-to-know across every surface (#55) ----

GA, GB = "g-a", "g-b"


def ver_or_none(cid):  # a hard-deleted member has no row left to hash
    with db.connect() as c:
        r = c.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()
    return r[0] if r else "gone"


FIND = {  # surface -> does this user reach the case through it? (a 404 or an absence is False)
    "list": lambda c, i: f'/review/{i}"' in c.get("/review").text,
    "detail": lambda c, i: c.get(f"/review/{i}").status_code == 200,
    "edit": lambda c, i: c.post(f"/review/{i}/edit", data={"field": "industry", "value": "Banking", "v": ver_or_none(i)}).status_code != 404,
    "approve": lambda c, i: c.post(f"/review/{i}/approve", data={"v": ver_or_none(i)}).status_code != 404,
    "search": lambda c, i: f'name="case_id" value="{i}"' in c.post("/search", data={"industry": "Aerospace"}).text,
    "generate": lambda c, i: c.post("/generate", data={"case_ids": [i], "format": "md"}).status_code != 404,
    "research_from_case": lambda c, i: c.post("/research/from-case", data={"case_id": i}).status_code != 404,
    "research_send": lambda c, i: c.post("/research/send", data={"query": "cloud kyc", "case_id": i}, follow_redirects=False).status_code != 404,
}
STATES = {  # state -> (groups, withdraw or delete member b, expect the merged case reachable on review pages / elsewhere)
    "one_group": ([REV, GA], None, False, False),
    "both_groups": ([REV, GA, GB], None, True, True),
    "member_withdrawn": ([REV, GA, GB], "withdraw", True, False),
    "member_deleted": ([REV, GA, GB], "delete", False, False),
}


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("surface", FIND)
def test_need_to_know_matrix(make, acme, monkeypatch, surface, state):
    from app import research as rs
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Query(query="cloud kyc"))
    groups, change, on_review, elsewhere = STATES[state]
    a = eng(make, TEXT_A, ["x"], [], acl=(GA,))
    b = eng(make, TEXT_B, ["y"], [], acl=(GB,))
    new = int(merge(client([REV, GA, GB]), [a, b]).headers["location"].rsplit("/", 1)[1])
    open_surface = surface in ("list", "detail", "edit", "approve")
    with db.connect() as c:
        if not open_surface:  # search, generate and research need an approved, in-date case
            c.execute("update cases set status='approved', review_due=now() + interval '60 days' "
                      "where id = any(%s)", ([a, b, new],))
        if change == "withdraw":
            c.execute("update documents set deleted_at=now() where id=(select document_id from cases where id=%s)", (b,))
        elif change == "delete":
            c.execute("delete from documents where id=(select document_id from cases where id=%s)", (b,))
    try:
        c = client(groups)
        want = on_review if surface in ("list", "detail") else elsewhere
        if surface in ("edit", "approve"):
            want = elsewhere or (on_review and change is None)
        assert FIND[surface](c, new) is want
        for member in (a, b):  # a member is never reachable on its own while merged
            assert FIND[surface](c, member) is False
    finally:
        with db.connect() as d:
            d.execute("delete from jobs where kind='research' and (payload->>'research_id')::bigint in "
                      "(select id from research where case_id=%s)", (new,))
            d.execute("delete from research where case_id=%s", (new,))


def test_unmerge_locks_members_before_the_merged_case(make, acme):
    """Ingest locks a member, then reopens M; unmerge must take the same order or they deadlock (#144)."""
    import threading
    from tests.test_ingest import _wait_for_lock_wait
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    v, got = ver(new), []
    holder = db.connect()  # stands in for ingest: holds member a, then wants M
    try:
        holder.execute("select 1 from cases where id=%s for update", (a,))
        t = threading.Thread(target=lambda: got.append(c.post(f"/review/{new}/unmerge", data={"v": v},
                                                               follow_redirects=False).status_code))
        t.start()
        _wait_for_lock_wait(holder)
        holder.execute("set local lock_timeout = '5s'")
        holder.execute("select 1 from cases where id=%s for update", (new,))  # deadlocks if unmerge holds M
        holder.commit()
    finally:
        holder.close()
    t.join(10)
    assert got == [303] and status_of(new) == "rejected"


def test_one_visible_member_cannot_merge_unmerge_or_list_the_other(make, acme):
    a = eng(make, TEXT_A, ["x"], [], acl=(GA,))
    b = eng(make, TEXT_B, ["y"], [], acl=(GB,))
    c = client([REV, GA])
    ms = [f"{a}:{ver(a)}", f"{b}:{ver(b)}"]
    assert c.post("/review/merge/preview", data={"members": ms}).status_code == 404
    assert c.post("/review/merge", data={"members": ms}).status_code == 404
    assert f'value="{b}:' not in c.get(f"/review/{a}").text  # candidate list
    new = int(merge(client([REV, GA, GB]), [a, b]).headers["location"].rsplit("/", 1)[1])
    assert c.post(f"/review/{new}/unmerge", data={"v": ver(new)}).status_code == 404
    assert status_of(new) == "extracted" and merged_of(a) == new


def test_approve_refused_when_a_member_is_no_longer_an_engagement(make, acme):
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    with db.connect() as d:
        d.execute("update cases set basis='delivered' where id=%s", (b,))
    assert c.post(f"/review/{new}/approve", data={"v": ver(new)}).status_code == 409


def test_withdrawn_member_can_still_be_unmerged_but_not_edited(make, acme):
    a = eng(make, TEXT_A, ["x"], [], acl=(GA,))
    b = eng(make, TEXT_B, ["y"], [], acl=(GB,))
    c = client([REV, GA, GB])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    with db.connect() as d:
        d.execute("update documents set deleted_at=now() where id=(select document_id from cases where id=%s)", (b,))
    page = c.get(f"/review/{new}").text
    assert "withdrawn" in page and f"/review/{new}/unmerge" in page
    assert f"/review/{new}/approve" not in page and ">Save<" not in page
    assert c.post(f"/review/{new}/unmerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    assert status_of(new) == "rejected" and merged_of(a) is None
