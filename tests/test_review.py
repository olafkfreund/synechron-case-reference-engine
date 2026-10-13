import json
import uuid

import pytest

from app import db
from app.schema import Outcome, Prose, ReferenceCase, Sourced
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
        # reviewer-written text meets the completeness minimum (#222): challenge, solution and two more sections
        prose={f: Prose(text=" ".join(["word"] * 80), by_reviewer=True) for f in ("challenge", "solution", "implementation", "configuration")},
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


# ---- a merged case and a re-extracted member (#190) ----

def _set_tech(cid, text_add="We added Kafka."):
    """What a re-extraction does to a member: new data, quoted from new document text."""
    with db.connect() as c:
        c.execute("""update cases set data = jsonb_set(data, '{tech_stack}',
                     '[{"value": "Kafka", "source_quote": "Kafka", "unsourced": false}]') where id=%s""", (cid,))
        c.execute("update documents set text = text || ' ' || %s where id=(select document_id from cases where id=%s)",
                  (text_add, cid))


def test_merge_records_member_versions(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    merge(client(R), [a, b])
    with db.connect() as c:
        rows = c.execute("select merged_version, md5(data::text) from cases where id = any(%s)", ([a, b],)).fetchall()
    assert len(rows) == 2 and all(mv == md5 for mv, md5 in rows)


def test_member_change_shows_on_merged_case_and_remerge_takes_new_facts(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    c = client(R)
    new = int(merge(c, [a, b], industry=b).headers["location"].rsplit("/", 1)[1])
    _, doc_b = _data_and_doc(new, b)
    _set_tech(b)
    page = c.get(f"/review/{new}").text
    assert "changed since merge" in page and f'action="/review/{new}/remerge"' in page
    assert c.post(f"/review/{new}/remerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    data, _ = _data_and_doc(new)
    assert [x["value"] for x in data["tech_stack"]] == ["Kafka"]
    assert data["industry"]["document_id"] == doc_b and status_of(new) == "extracted"
    assert "changed since merge" not in c.get(f"/review/{new}").text


def test_unchanged_merged_case_offers_no_remerge(make, acme):
    new, b = _merged(make)
    with db.connect() as x:
        x.execute("update cases set data = data where id=%s", (b,))  # a rewrite with the same content
    page = client(R).get(f"/review/{new}").text
    assert "changed since merge" not in page and "/remerge" not in page
    _set_tech(b)  # a real change does show: the check above is not vacuous
    assert "changed since merge" in client(R).get(f"/review/{new}").text


def test_remerge_drops_a_withdrawn_member(make, acme):
    a, b, d = (eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], []), eng(make, TEXT_B, ["z"], []))
    c = client(R)
    new = int(merge(c, [a, b, d]).headers["location"].rsplit("/", 1)[1])
    with db.connect() as x:
        x.execute("update documents set deleted_at=now() where id=(select document_id from cases where id=%s)", (d,))
    assert f'action="/review/{new}/remerge"' in c.get(f"/review/{new}").text
    assert c.post(f"/review/{new}/remerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    with db.connect() as x:
        assert x.execute("select member_count from cases where id=%s", (new,)).fetchone()[0] == 2
    assert merged_of(d) is None and merged_of(a) == new
    assert c.get(f"/review/{new}").status_code == 200


def test_remerge_refusals(make, acme):
    def post(cl, new, v=None):
        return cl.post(f"/review/{new}/remerge", data={"v": v or ver(new)}).status_code

    c = client(R)
    new, b = _merged(make)
    assert post(c, new, "stale") == 409
    assert post(client([USER]), new) == 403
    assert post(client([REV]), new) == 404
    with db.connect() as x:  # another registered client
        gid = x.execute("insert into clients(name, aliases, anonymised_label) values (%s, '{Globex}', 'a retailer') "
                        "returning id", (f"Globex {uuid.uuid4().hex[:8]}",)).fetchone()[0]
    try:
        with db.connect() as x:
            x.execute("""update cases set data = jsonb_set(data, '{client_mention,value}', '"Globex"') where id=%s""", (b,))
        assert post(c, new) == 400
    finally:
        with db.connect() as x:
            x.execute("delete from clients where id=%s", (gid,))
    new, b = _merged(make)
    with db.connect() as x:
        x.execute("update cases set status='rejected' where id=%s", (b,))
    assert post(c, new) == 409
    new, b = _merged(make)
    with db.connect() as x:
        x.execute("update documents set deleted_at=now() where id=(select document_id from cases where id=%s)", (b,))
    assert post(c, new) == 409  # fewer than 2 remain


def test_remerge_locks_members_before_the_merged_case(make, acme):
    """Same order as unmerge: ingest locks a member, then reopens M (#144)."""
    import threading
    from tests.test_ingest import _wait_for_lock_wait
    c = client(R)
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    v, got = ver(new), []
    holder = db.connect()
    try:
        holder.execute("select 1 from cases where id=%s for update", (a,))
        t = threading.Thread(target=lambda: got.append(c.post(f"/review/{new}/remerge", data={"v": v},
                                                               follow_redirects=False).status_code))
        t.start()
        _wait_for_lock_wait(holder)
        holder.execute("set local lock_timeout = '5s'")
        holder.execute("select 1 from cases where id=%s for update", (new,))
        holder.commit()
    finally:
        holder.close()
    t.join(10)
    assert got == [303] and status_of(new) == "extracted"


# ---- reviewer-written sections (#198) ----

def test_reviewer_writes_section_and_approve_keeps_it(make):
    cid = make()
    assert post(client(R), cid, field="implementation", value="Phased rollout", written="true").status_code == 303
    item = row(cid)[1]["implementation"]
    assert item["by_reviewer"] is True and item["unsourced"] is False and item["source_quote"] == ""
    assert "written by reviewer" in client(R).get(f"/review/{cid}").text
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 200
    assert row(cid)[1]["implementation"]["value"] == "Phased rollout"


def test_written_flag_refused_on_other_fields(make):
    cid = make()
    before = row(cid)[1]
    for f in ("title", "capabilities.new", "summary"):
        assert post(client(R), cid, field=f, value="x", quote=Q_TITLE, written="true").status_code == 400
    assert post(client(R), cid, field="implementation", value="", written="true").status_code == 400
    assert row(cid)[1] == before


def test_save_without_written_returns_to_quote_check(make):
    cid = make()
    post(client(R), cid, field="configuration", value="Own words", written="true")
    assert post(client(R), cid, field="configuration", value="Own words").status_code == 303
    item = row(cid)[1]["configuration"]
    assert item["by_reviewer"] is False and item["unsourced"] is True


def test_engagement_lessons_learned_edit_refused(make):
    cid = make(basis="engagement")
    r = post(client(R), cid, field="lessons_learned", value="Start earlier", written="true")
    assert r.status_code == 400 and row(cid)[1]["lessons_learned"]["value"] is None
    assert 'value="lessons_learned' not in client(R).get(f"/review/{cid}").text


def test_merged_case_takes_sections_from_picked_member(make, acme):
    def with_impl(text, impl):
        cid = eng(make, text, ["x"], [])
        data = row(cid)[1]
        data["implementation"] = {"value": impl, "source_quote": "", "unsourced": False, "document_id": None, "by_reviewer": True}
        with db.connect() as c:
            c.execute("update cases set data=%s where id=%s", (json.dumps(data), cid))
        return cid
    a, b = with_impl(TEXT_A, "From A"), with_impl(TEXT_B, "From B")
    new = int(merge(client(R), [a, b], implementation=b).headers["location"].rsplit("/", 1)[1])
    assert row(new)[1]["implementation"]["value"] == "From B"


def test_merged_case_has_no_lessons_learned(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    for i, w in ((a, "From A"), (b, "From B")):  # stale values from before the case became an engagement
        with db.connect() as c:
            c.execute("update cases set data = jsonb_set(data, '{lessons_learned,value}', to_jsonb(%s::text)) where id=%s", (w, i))
    members = [f"{i}:{ver(i)}" for i in (a, b)]
    assert "lessons learned" not in client(R).post("/review/merge/preview", data={"members": members}).text
    new = int(merge(client(R), [a, b]).headers["location"].rsplit("/", 1)[1])
    assert row(new)[1]["lessons_learned"]["value"] is None
    assert "lessons_learned" not in client(R).get(f"/review/{new}").text


def test_quote_posted_with_written_is_ignored(make):
    cid = make()
    post(client(R), cid, field="implementation", value="Own words", quote=Q_TITLE, written="true")
    item = row(cid)[1]["implementation"]
    assert item["by_reviewer"] is True and item["source_quote"] == ""


def test_approve_clears_lessons_learned_on_engagement(make):
    cid = make(basis="engagement")
    with db.connect() as c:
        c.execute("update cases set data = jsonb_set(data, '{lessons_learned}', "
                  "'{\"value\": \"Stale\", \"source_quote\": \"\", \"by_reviewer\": true}') where id=%s", (cid,))
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 200
    assert row(cid)[1]["lessons_learned"]["value"] is None


def _sources(cid, items=None):
    with db.connect() as c:
        if items is not None:
            c.execute("update cases set public_sources=%s where id=%s", (json.dumps(items), cid))
        return c.execute("select public_sources from cases where id=%s", (cid,)).fetchone()[0]


def _src(i):
    return {"quote": f"q{i}", "url": f"https://example.org/{i}", "publisher": "example.org", "type": "vendor_claim"}


def test_merge_unions_member_sources(make, acme):  # #199
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    _sources(a, [_src(1), _src(2)])
    _sources(b, [_src(2), *[_src(i) for i in range(3, 25)]])
    new = int(merge(client(R), [a, b]).headers["location"].rsplit("/", 1)[1])
    got = _sources(new)
    assert [c["quote"] for c in got] == [f"q{i}" for i in range(1, 21)]  # deduplicated, capped at 20, no failure


def test_remerge_and_unmerge_keep_source_lists(make, acme):  # #199
    new, b = _merged(make)
    _sources(new, [_src(9)])
    with db.connect() as c:
        c.execute("update cases set data = jsonb_set(data, '{summary}', to_jsonb('x'::text)) where id=%s", (b,))
    client(R).post(f"/review/{new}/remerge", data={"v": ver(new)}, follow_redirects=False)
    assert _sources(new) == [_src(9)]
    client(R).post(f"/review/{new}/unmerge", data={"v": ver(new)}, follow_redirects=False)
    assert _sources(new) == [_src(9)]


# --- diagrams (#200) ---
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20  # the web task checks magic bytes only; the worker decodes


@pytest.fixture
def jobs():
    with db.connect() as c:
        c.execute("delete from jobs where kind='diagram'")
    yield
    with db.connect() as c:
        c.execute("delete from jobs where kind='diagram'")


def add_diagram(cid, data=PNG, caption="Architecture", name="a.png", v=None, rev=None):
    return (rev or client(R)).post(f"/review/{cid}/diagrams", data={"caption": caption, "v": v or ver(cid)},
                                   files={"file": (name, data)}, follow_redirects=False)


def entries(cid):
    with db.connect() as c:
        return c.execute("select data->'diagrams' from cases where id=%s", (cid,)).fetchone()[0]


def test_upload_diagram_queues_job_and_shows_processing(make, jobs):
    cid = make()
    assert add_diagram(cid).status_code == 303
    (e,) = entries(cid)
    assert (e["caption"], e["status"]) == ("Architecture", "pending")
    with db.connect() as c:
        raw, = c.execute("select raw from diagrams where id=%s and case_id=%s", (e["id"], cid)).fetchone()
        assert bytes(raw) == PNG
        assert c.execute("select payload from jobs where kind='diagram'").fetchall() == [({"diagram_id": e["id"]},)]


def test_upload_diagram_refuses_svg_gif_wrong_magic_oversize_and_the_one_over_the_cap(make, jobs):
    cid = make()
    svg, gif = b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a" + b"x" * 20
    assert [add_diagram(cid, d).status_code for d in (svg, gif, b"", b"x" * 20)] == [400] * 4
    assert add_diagram(cid, PNG + b"x" * (5 * 1024 * 1024)).status_code == 413
    assert add_diagram(cid, caption="  ").status_code == 400
    assert add_diagram(cid, caption="x" * 201).status_code == 400
    assert entries(cid) == []
    from app.diagrams import MAX_PER_CASE
    for _ in range(MAX_PER_CASE):
        assert add_diagram(cid).status_code == 303
    assert add_diagram(cid).status_code == 400 and len(entries(cid)) == MAX_PER_CASE
    with db.connect() as c:
        assert c.execute("select count(*) from jobs where kind='diagram'").fetchone()[0] == MAX_PER_CASE


def test_upload_diagram_refuses_caption_naming_protected_client(make, acme, jobs):
    cid = make()
    with db.connect() as c:
        name = c.execute("select name from clients where id=%s", (acme,)).fetchone()[0]
    r = add_diagram(cid, caption=f"Flow at {name}")
    assert r.status_code == 400 and name not in r.text and entries(cid) == []
    assert add_diagram(cid, caption="Flow").status_code == 303
    r = client(R).post(f"/review/{cid}/diagrams/{entries(cid)[0]['id']}/caption", data={"caption": name, "v": ver(cid)})
    assert r.status_code == 400 and name not in r.text and entries(cid)[0]["caption"] == "Flow"


def test_diagram_change_reopens_approved_case(make, jobs):
    cid = make("approved", "10 days")
    assert add_diagram(cid).status_code == 303 and row(cid)[0] == "extracted" and not row(cid)[5]
    did = entries(cid)[0]["id"]
    for status, path, data in (("caption", "caption", {"caption": "New"}), ("remove", "remove", {})):
        with db.connect() as c:
            c.execute("update cases set status='approved', review_due=now() + interval '10 days' where id=%s", (cid,))
        r = client(R).post(f"/review/{cid}/diagrams/{did}/{path}", data={**data, "v": ver(cid)}, follow_redirects=False)
        assert r.status_code == 303 and row(cid)[0] == "extracted", status


def test_diagram_change_with_stale_version_is_409(make, jobs):
    cid = make()
    v = ver(cid)
    assert add_diagram(cid, v=v).status_code == 303
    assert add_diagram(cid, v=v).status_code == 409 and len(entries(cid)) == 1
    did = entries(cid)[0]["id"]
    assert client(R).post(f"/review/{cid}/diagrams/{did}/remove", data={"v": v}).status_code == 409


def test_remove_diagram_deletes_unreferenced_bytes(make, jobs):
    cid = make()
    add_diagram(cid)
    did = entries(cid)[0]["id"]
    assert client(R).post(f"/review/{cid}/diagrams/{did + 1}/remove", data={"v": ver(cid)}).status_code == 404
    assert client(R).post(f"/review/{cid}/diagrams/{did}/remove", data={"v": ver(cid)}, follow_redirects=False).status_code == 303
    assert entries(cid) == []
    with db.connect() as c:
        assert c.execute("select count(*) from diagrams where id=%s", (did,)).fetchone()[0] == 0


def test_upload_never_calls_image_open(make, jobs, monkeypatch):
    from PIL import Image
    def boom(*a, **k):
        raise AssertionError("the web task decoded an upload")
    monkeypatch.setattr(Image, "open", boom)
    cid = make()
    assert add_diagram(cid).status_code == 303 and len(entries(cid)) == 1


def test_diagram_route_serves_only_ready_png_to_reviewers(make, jobs):
    cid = make()
    add_diagram(cid)
    did = entries(cid)[0]["id"]
    url = f"/review/{cid}/diagrams/{did}.png"
    assert client(R).get(url).status_code == 404  # pending: raw is never served
    with db.connect() as c:
        c.execute("update diagrams set png=%s, raw=null where id=%s", (PNG, did))
        c.execute("update cases set data = jsonb_set(data, '{diagrams,0,status}', '\"ready\"') where id=%s", (cid,))
    r = client(R).get(url)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'; default-src 'none'"  # the guard's framing rule comes first
    assert client([USER]).get(url).status_code == 403
    assert client(R).get(f"/review/{cid + 1}/diagrams/{did}.png").status_code == 404


def set_diagram(cid, status):
    entry = {"id": 1, "caption": "c", "status": status, "reason": "", "width": 0, "height": 0}
    with db.connect() as c:
        c.execute("update cases set data = data || jsonb_build_object('diagrams', %s::jsonb) where id=%s",
                  (json.dumps([entry]), cid))


def test_approve_refused_while_diagram_pending(make):
    cid = make()
    set_diagram(cid, "pending")
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid), "diagrams_checked": "on"})
    assert r.status_code == 409 and row(cid)[0] == "extracted"


def test_approve_with_diagrams_requires_checkbox(make):
    cid = make()
    set_diagram(cid, "ready")
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}).status_code == 400
    assert row(cid)[0] == "extracted"
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid), "diagrams_checked": "on"}, follow_redirects=False)
    assert r.status_code == 303 and row(cid)[0] == "approved"
    other = make()
    set_diagram(other, "rejected")  # a rejected diagram neither blocks nor needs the box
    assert client(R).post(f"/review/{other}/approve", data={"v": ver(other)}, follow_redirects=False).status_code == 303


def test_stale_version_cannot_approve_after_diagram_added(make, jobs):
    cid = make()
    v = ver(cid)  # the page the reviewer loaded, before the upload
    assert add_diagram(cid).status_code == 303
    assert client(R).post(f"/review/{cid}/approve", data={"v": v, "diagrams_checked": "on"}).status_code == 409
    assert row(cid)[0] == "extracted"


def test_merge_starts_without_diagrams_and_remerge_keeps_them(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    set_diagram(a, "ready")
    c = client(R)
    new = int(merge(c, [a, b]).headers["location"].rsplit("/", 1)[1])
    assert _data_and_doc(new)[0]["diagrams"] == []
    set_diagram(new, "ready")
    _set_tech(b)
    assert c.post(f"/review/{new}/remerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    assert [d["status"] for d in _data_and_doc(new)[0]["diagrams"]] == ["ready"]


def test_detail_shows_diagrams_card(make, jobs):
    cid = make()
    page = client(R).get(f"/review/{cid}").text
    assert 'enctype="multipart/form-data"' in page and 'accept="image/png,image/jpeg"' in page
    assert "diagrams_checked" not in page
    add_diagram(cid, caption="<b>Flow</b>")
    page = client(R).get(f"/review/{cid}").text
    assert "Processing: reload in a moment" in page and "&lt;b&gt;Flow&lt;/b&gt;" in page and "<b>Flow" not in page
    did = entries(cid)[0]["id"]
    with db.connect() as c:
        c.execute("update cases set data = jsonb_set(jsonb_set(data, '{diagrams,0,status}', '\"ready\"'), "
                  "'{diagrams,0,width}', '900') where id=%s", (cid,))
    page = client(R).get(f"/review/{cid}").text
    assert f'<img src="/review/{cid}/diagrams/{did}.png" alt="&lt;b&gt;Flow&lt;/b&gt;" width="480">' in page
    assert 'name="diagrams_checked" required' in page and "no client name, logo, person" in page
    set_diagram(cid, "rejected")
    page = client(R).get(f"/review/{cid}").text
    assert "Rejected:" in page and "diagrams_checked" not in page


# --- case-study text (#212 B4) ---
def prosed(make, status="extracted", due=None, **kw):
    pt = Sourced[str](value="Onboarding cut", source_quote=Q_TITLE)
    gone = Sourced[str](value="Made up", source_quote="a passage that is not in the document", unsourced=True)
    return make(status, due, data=case_data(points={"solution": [pt, gone]}, **kw))


LONG = " ".join(["word"] * 80)  # keeps an edited section above the completeness minimum (#222)


def edit_prose(cid, section, text, c=None):
    return (c or client(R)).post(f"/review/{cid}/edit", data={"field": f"prose.{section}", "value": text, "v": ver(cid)},
                                 follow_redirects=False)


def test_prose_edit_sets_reviewer_flags_and_removal(make):
    from app.schema import Prose
    cid = prosed(make)
    assert edit_prose(cid, "solution", "Onboarding fell to 3 days at Zanzibar Ltd.").status_code == 303
    p = row(cid)[1]["prose"]["solution"]
    assert p["by_reviewer"] is True and p["flags"] == ["names 'Zanzibar Ltd', which no quote supports"] and "Zanzibar" in row(cid)[2]
    assert Prose.model_validate(p).text.startswith("Onboarding")
    assert edit_prose(cid, "solution", "").status_code == 303 and "solution" not in row(cid)[1]["prose"]
    assert edit_prose(cid, "lessons_learned", "x").status_code == 400
    assert edit_prose(cid, "nope", "x").status_code == 400


def test_approval_blocked_on_flagged_model_prose_until_edited(make):
    flagged = {"solution": {"text": "It took 99 days.", "by_reviewer": False, "flags": ["number 99 is in no quote"]}}
    # the other sections stay long enough for the completeness minimum (#222)
    flagged |= {f: {"text": LONG, "by_reviewer": True, "flags": []} for f in ("challenge", "implementation", "configuration")}
    cid = prosed(make, prose=flagged)
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)})
    assert r.status_code == 400 and "flagged write-up" in r.text
    edit_prose(cid, "solution", f"Onboarding fell to 3 days. {LONG}")  # an edit confirms it
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}, follow_redirects=False)
    assert r.status_code == 303 and row(cid)[0] == "approved"
    assert [p["value"] for p in row(cid)[1]["points"]["solution"]] == ["Onboarding cut"]  # the unsourced point is dropped


def test_edit_reopens_an_approved_case_with_prose(make):
    cid = prosed(make, "approved", "10 days")  # due within 30 days: still editable (#143)
    assert row(cid)[0] == "approved"
    edit_prose(cid, "challenge", "Onboarding was slow.")
    assert row(cid)[0] == "extracted"


def test_detail_shows_prose_escaped_with_points_and_flags(make):
    bad = "<script>alert(1)</script>"
    cid = prosed(make, prose={"solution": {"text": bad, "by_reviewer": False, "flags": [f"names '{bad}'"]}})
    html = client(R).get(f"/review/{cid}").text
    assert "Case-study text" in html and "Sourced points (2)" in html and "Onboarding cut" in html
    assert bad not in html and "&lt;script&gt;" in html
    assert 'name="field" value="prose.solution"' in html


def test_merge_unions_points_and_leaves_prose_empty(make, acme):
    a, b = eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], [])
    for cid, vals in ((a, ["Cut onboarding", "Shared"]), (b, ["shared", "Moved payments"])):
        with db.connect() as c:
            d = c.execute("select data from cases where id=%s", (cid,)).fetchone()[0]
            q = "Acme cut customer onboarding from 12 days to 3 days" if cid == a else "Acme moved payments to the cloud"
            d["points"] = {"solution": [{"value": v, "source_quote": q} for v in vals]}
            d["prose"] = {"solution": {"text": "member text", "by_reviewer": True, "flags": []}}
            c.execute("update cases set data=%s where id=%s", (json.dumps(d), cid))
    new = int(merge(client(R), [a, b]).headers["location"].rsplit("/", 1)[1])
    data, _ = _data_and_doc(new)
    assert [p["value"] for p in data["points"]["solution"]] == ["Cut onboarding", "Shared", "Moved payments"]
    docs = {cid: _data_and_doc(new, cid)[1] for cid in (a, b)}
    assert [p["document_id"] for p in data["points"]["solution"]] == [docs[a], docs[a], docs[b]]
    # the same point, unsourced in the first member and sourced in the second: the sourced copy and its origin win
    with db.connect() as c:
        for cid, unsourced in ((a, True), (b, False)):
            d = c.execute("select data from cases where id=%s", (cid,)).fetchone()[0]
            d["points"] = {"solution": [{"value": "Shared", "source_quote": "q", "unsourced": unsourced}]}
            c.execute("update cases set data=%s where id=%s", (json.dumps(d), cid))
    from app.review import combine
    from app.schema import ReferenceCase
    got = combine([(cid, docs[cid], ReferenceCase.model_validate(row(cid)[1])) for cid in (a, b)], {})
    assert [(p.unsourced, p.document_id) for p in got.points["solution"]] == [(False, docs[b])]
    assert data["prose"] == {} and "merged: write the case-study text for each section" in data["needs_attention"]


def test_reviewer_text_still_flagged_for_a_number_can_be_approved(make):
    cid = prosed(make)
    edit_prose(cid, "solution", f"It took 99 days. {LONG}")
    assert row(cid)[1]["prose"]["solution"]["flags"] == ["number 99 is in no quote"]
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}, follow_redirects=False).status_code == 303


def test_a_price_in_the_write_up_blocks_approval_even_for_the_reviewer(make):
    cid = prosed(make)
    edit_prose(cid, "solution", "Onboarding cost £1,200 per day.")
    assert row(cid)[1]["prose"]["solution"]["by_reviewer"] is True
    r = client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)})
    assert r.status_code == 400 and "price" in r.text and row(cid)[0] == "extracted"


def test_below_the_minimum_needs_a_reason_and_leaves_an_override_row(make):
    cid = make(data=case_data(prose={}))  # thin: no sections
    c = client(R)
    r = c.post(f"/review/{cid}/approve", data={"v": ver(cid)})
    assert r.status_code == 400 and "below the minimum" in r.text and row(cid)[0] == "extracted"
    r = c.post(f"/review/{cid}/approve", data={"v": ver(cid), "reason": "  <b>urgent</b> " + "x" * 600}, follow_redirects=False)
    assert r.status_code == 303 and row(cid)[0] == "approved"
    with db.connect() as conn:
        got = conn.execute("select reason, score, approved_by from case_approval_overrides where case_id=%s", (cid,)).fetchall()
    assert len(got) == 1 and got[0][0].startswith("<b>urgent</b>") and len(got[0][0]) == 500 and got[0][1] == "1/9"
    # the page offers the reason box only below the minimum
    thin = make(data=case_data(prose={}))
    page = client(R).get(f"/review/{thin}").text
    assert "Completeness: 1 of 9" in page and 'name="reason"' in page and 'href="#text"' not in page


def test_complete_enough_case_needs_no_reason_and_writes_no_override(make):
    cid = make()
    page = client(R).get(f"/review/{cid}").text
    assert "Completeness:" in page and 'name="reason"' not in page
    assert client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)}, follow_redirects=False).status_code == 303
    with db.connect() as conn:
        assert conn.execute("select count(*) from case_approval_overrides where case_id=%s", (cid,)).fetchone()[0] == 0


# --- diagram candidates found in the documents (#212 C4) ---
def real_png(side=450):
    import io as _io
    from PIL import Image
    b = _io.BytesIO()
    Image.new("RGB", (side, side), "navy").save(b, "PNG")
    return b.getvalue()


def add_candidate(cid, hint="Target architecture", png=None, doc=None):
    with db.connect() as c:
        did = doc or c.execute("select document_id from cases where id=%s", (cid,)).fetchone()[0]
        return c.execute("insert into document_images(document_id, png, width, height, hint) values (%s,%s,450,450,%s) returning id",
                         (did, png or real_png(), hint)).fetchone()[0]


def img_post(cid, iid, what, c=None, **data):
    return (c or client(R)).post(f"/review/{cid}/images/{iid}/{what}", data={"v": ver(cid), **data}, follow_redirects=False)


def test_candidate_thumbnail_headers_and_card(make):
    cid = make()
    iid = add_candidate(cid, hint="<b>Zed</b> flow")
    r = client(R).get(f"/review/{cid}/images/{iid}.png")
    assert r.status_code == 200 and r.content == real_png()
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    page = client(R).get(f"/review/{cid}").text
    assert "Diagrams found in the documents" in page and "Nothing here reaches an output until you choose Use" in page
    assert f"/review/{cid}/images/{iid}.png" in page and "<b>Zed</b>" not in page and "&lt;b&gt;Zed" in page


def test_use_goes_through_the_diagram_path_and_the_image_stops_being_a_candidate(make, jobs):
    cid = make("approved", "10 days")
    iid = add_candidate(cid)
    assert img_post(cid, iid, "use", caption="Target architecture").status_code == 303
    (e,) = entries(cid)
    assert (e["status"], e["caption"], e["image_id"]) == ("pending", "Target architecture", iid)
    with db.connect() as c:
        assert c.execute("select raw from diagrams where id=%s", (e["id"],)).fetchone()[0] is not None  # the worker decodes it again
        assert c.execute("select count(*) from jobs where kind='diagram'").fetchone()[0] == 1
    assert row(cid)[0] == "extracted"  # reopened, as for an upload
    assert f"/images/{iid}.png" not in client(R).get(f"/review/{cid}").text
    assert img_post(cid, iid, "use", caption="again").status_code == 404 and len(entries(cid)) == 1


def test_dismiss_hides_the_candidate_and_blocks_use(make, jobs):
    cid = make("approved", "10 days")
    iid = add_candidate(cid)
    assert img_post(cid, iid, "dismiss").status_code == 303
    assert row(cid)[1]["dismissed_images"] == [iid] and row(cid)[0] == "approved"  # no content changed: not reopened
    assert f"/images/{iid}.png" not in client(R).get(f"/review/{cid}").text
    assert img_post(cid, iid, "use", caption="x").status_code == 404 and entries(cid) == []


def test_use_respects_the_cap(make, jobs):
    from app.diagrams import MAX_PER_CASE
    cid = make()
    for _ in range(MAX_PER_CASE):
        assert add_diagram(cid).status_code == 303
    iid = add_candidate(cid)
    assert img_post(cid, iid, "use", caption="one too many").status_code == 400 and len(entries(cid)) == MAX_PER_CASE


def test_another_cases_or_an_outside_reviewers_candidate_is_404(make, jobs):
    mine, other = make(), make()
    theirs = add_candidate(other)
    assert client(R).get(f"/review/{mine}/images/{theirs}.png").status_code == 404
    assert img_post(mine, theirs, "use", caption="x").status_code == 404 and img_post(mine, theirs, "dismiss").status_code == 404
    iid = add_candidate(mine)
    outsider = client([REV, "g-other"])  # a reviewer who may not open this document
    assert outsider.get(f"/review/{mine}/images/{iid}.png").status_code == 404
    assert img_post(mine, iid, "use", outsider, caption="x").status_code == 404 and img_post(mine, iid, "dismiss", outsider).status_code == 404
    assert client([USER, DOCS]).get(f"/review/{mine}/images/{iid}.png").status_code == 403  # not a reviewer
    assert entries(mine) == [] and row(mine)[1]["dismissed_images"] == []


def test_use_refuses_a_caption_naming_a_protected_client(make, acme, jobs):
    cid = make()
    iid = add_candidate(cid)
    with db.connect() as c:
        name = c.execute("select name from clients where id=%s", (acme,)).fetchone()[0]
    r = img_post(cid, iid, "use", caption=f"Flow at {name}")
    assert r.status_code == 400 and name not in r.text and entries(cid) == []


def test_merged_case_offers_its_members_images(make, acme):
    new, member = _merged(make)
    iid = add_candidate(new, doc=_data_and_doc(new, member)[1])
    assert client(R).get(f"/review/{new}/images/{iid}.png").status_code == 200
    assert f"/images/{iid}.png" in client(R).get(f"/review/{new}").text


def test_removing_a_used_diagram_dismisses_its_image_for_good(make, jobs):
    cid = make()
    iid = add_candidate(cid)
    img_post(cid, iid, "use", caption="Flow")
    assert client(R).get(f"/review/{cid}/images/{iid}.png").status_code == 404  # used: not served as a candidate
    did = entries(cid)[0]["id"]
    assert client(R).post(f"/review/{cid}/diagrams/{did}/remove", data={"v": ver(cid)}, follow_redirects=False).status_code == 303
    assert row(cid)[1]["dismissed_images"] == [iid] and entries(cid) == []
    assert f"/images/{iid}.png" not in client(R).get(f"/review/{cid}").text
    assert client(R).get(f"/review/{cid}/images/{iid}.png").status_code == 404


def test_dismissed_image_thumbnail_is_not_served(make):
    cid = make()
    iid = add_candidate(cid)
    img_post(cid, iid, "dismiss")
    assert client(R).get(f"/review/{cid}/images/{iid}.png").status_code == 404


def test_use_buttons_are_hidden_at_the_cap(make, jobs):
    from app.diagrams import MAX_PER_CASE
    cid = make()
    iid = add_candidate(cid)
    assert f"/images/{iid}/use" in client(R).get(f"/review/{cid}").text
    for _ in range(MAX_PER_CASE):
        add_diagram(cid)
    page = client(R).get(f"/review/{cid}").text
    assert f"/images/{iid}/dismiss" in page and f"/images/{iid}/use" not in page


def test_a_withdrawn_documents_images_are_not_served(make, acme):
    new, member = _merged(make)
    with db.connect() as c:
        other = c.execute("select id from cases where merged_into=%s and id<>%s", (new, member)).fetchone()[0]
    live_doc, gone_doc = _data_and_doc(new, other)[1], _data_and_doc(new, member)[1]
    a, b = add_candidate(new, doc=live_doc), add_candidate(new, doc=gone_doc)
    with db.connect() as c:
        c.execute("update documents set deleted_at=now() where id=%s", (gone_doc,))
    assert client(R).get(f"/review/{new}/images/{a}.png").status_code == 200
    assert client(R).get(f"/review/{new}/images/{b}.png").status_code == 404


def test_remerge_keeps_the_dismissed_images(make, acme):
    a, b, d = (eng(make, TEXT_A, ["x"], []), eng(make, TEXT_B, ["y"], []), eng(make, TEXT_B, ["z"], []))
    c = client(R)
    new = int(merge(c, [a, b, d]).headers["location"].rsplit("/", 1)[1])
    iid = add_candidate(new, doc=_data_and_doc(new, a)[1])
    img_post(new, iid, "dismiss")
    with db.connect() as x:
        x.execute("update documents set deleted_at=now() where id=(select document_id from cases where id=%s)", (d,))
    assert c.post(f"/review/{new}/remerge", data={"v": ver(new)}, follow_redirects=False).status_code == 303
    assert row(new)[1]["dismissed_images"] == [iid]
