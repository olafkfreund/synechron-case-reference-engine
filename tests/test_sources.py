import re
import uuid

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import db
from app.extract import EXTRACTION_VERSION
from app.sources import OLDER, parse_config
from app.schema import ReferenceCase, Sourced
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401


def test_sources_admin(env):  # noqa: F811
    db.init()
    name = f"sp-{uuid.uuid4().hex[:8]}"
    a = client([ADMIN])
    assert client([REV]).get("/admin/sources").status_code == 403
    bad = a.post("/admin/sources", data={"kind": "sharepoint", "name": name, "tenant_id": "9b1c6f0e-2f43-4c56-9a7b-0d1e2f3a4b5c",
                                          "acl_groups": "g1"})
    assert bad.status_code == 400 and "drive_id" in bad.text
    assert a.post("/admin/sources", data={"kind": "sharepoint", "name": name, "acl_groups": " ",
                                          "tenant_id": "9b1c6f0e-2f43-4c56-9a7b-0d1e2f3a4b5c", "drive_id": "d"}).status_code == 400
    ok = a.post("/admin/sources", follow_redirects=False, data={
        "kind": "sharepoint", "name": name, "tenant_id": "9b1c6f0e-2f43-4c56-9a7b-0d1e2f3a4b5c", "drive_id": "d", "acl_groups": "g1, g2"})
    assert ok.status_code == 303
    with db.connect() as c:
        sid, acl = c.execute("select id, acl_groups from sources where name=%s", (name,)).fetchone()
    try:
        assert acl == ["g1", "g2"]
        assert a.post(f"/admin/sources/{sid}/crawl", follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute("select kind from jobs where payload->>'source_id' = %s", (str(sid),)).fetchone()[0] == "crawl_sharepoint"
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g3"})  # unchecked "enabled" disables it
        assert a.post(f"/admin/sources/{sid}/crawl").status_code == 404
        assert name in a.get("/admin/sources").text
        flag = "select config->'executed_contracts' from sources where id=%s"
        with db.connect() as c:
            assert c.execute(flag, (sid,)).fetchone()[0] is False
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g3", "executed_contracts": "on"})
        with db.connect() as c:
            assert c.execute(flag, (sid,)).fetchone()[0] is True
        assert 'name="executed_contracts" checked' in a.get("/admin/sources").text
    finally:
        with db.connect() as c:
            c.execute("delete from jobs where payload->>'source_id' = %s", (str(sid),))
            c.execute("delete from sources where id=%s", (sid,))


REASON = "source marked executed"


def _src(c, executed):
    cfg = {"bucket": "b", **({"executed_contracts": True} if executed else {})}
    return c.execute("insert into sources(kind, name, config, acl_groups) values ('upload',%s,%s,'{g1}') returning id",
                     (f"up-{uuid.uuid4().hex[:8]}", Jsonb(cfg))).fetchone()[0]


def _doc(c, sid, kind, deleted=False):
    return c.execute("insert into documents(source_id, external_id, checksum, kind, acl_groups, deleted_at) "
                     "values (%s,%s,'x',%s,'{g1}',case when %s then now() end) returning id",
                     (sid, uuid.uuid4().hex, kind, deleted)).fetchone()[0]


def _case(c, did, status, basis, reason):
    data = ReferenceCase(title=Sourced[str](value="T"), basis=basis, basis_reason=reason).model_dump_json()
    return c.execute("insert into cases(document_id, status, basis, data) values (%s,%s,%s,%s) returning id",
                     (did, status, basis, data)).fetchone()[0]


def _job(c, did, reason=REASON, status="queued"):
    c.execute("insert into jobs(kind, payload, status) values ('extract', jsonb_build_object('document_id', %s::bigint, "
              "'basis', 'engagement', 'basis_reason', %s::text), %s)", (did, reason, status))


def _jobs(c, sid):
    return c.execute("select j.payload from jobs j join documents d on d.id=(j.payload->>'document_id')::bigint "
                     "where j.kind='extract' and d.source_id=%s", (sid,)).fetchall()


def _clean(sid):
    with db.connect() as c:
        c.execute("delete from jobs where kind='extract' and (payload->>'document_id')::bigint in "
                  "(select id from documents where source_id=%s)", (sid,))
        c.execute("delete from cases where document_id is null and id in (select merged_into from cases "
                  "where document_id in (select id from documents where source_id=%s))", (sid,))  # merged rows first
        c.execute("delete from sources where id=%s", (sid,))


def test_ticking_executed_queues_contracts_without_a_case(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, False)
        a, b, cc, d = _doc(c, sid, "contract"), _doc(c, sid, "contract"), _doc(c, sid, "contract", True), _doc(c, sid, "case")
        _case(c, b, "rejected", "engagement", "unsigned")
        _job(c, a)  # already queued: ticking must not add a second one
    try:
        form = {"acl_groups": "g1", "executed_contracts": "on"}
        for _ in range(2):  # the second save changes nothing and queues nothing more
            assert client([ADMIN]).post(f"/admin/sources/{sid}", data=form, follow_redirects=False).status_code == 303
            with db.connect() as c:
                rows = _jobs(c, sid)
            assert [r[0] for r in rows] == [{"document_id": a, "basis": "engagement", "basis_reason": REASON}]
    finally:
        _clean(sid)


def test_unticking_executed_retires_flagged_engagements(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, True)
        e1, e2, e3, e4, e5, e6 = (_doc(c, sid, "contract") for _ in range(6))
        other = _src(c, True)
        c4 = _case(c, _doc(c, other, "contract"), "approved", "engagement", REASON)
        c1 = _case(c, e1, "approved", "engagement", REASON)
        c2 = _case(c, e2, "approved", "engagement", "executed contract")
        c3 = _case(c, e3, "approved", "delivered", "")
        _job(c, e4)
        _job(c, e5, status="running")
        _job(c, e6, reason="executed contract")
        c7 = _case(c, _doc(c, sid, "contract"), "approved", "engagement", "executed contract")
        merged = c.execute(  # an approved merged engagement (#55) of c1 (flagged) and c7 (a real contract)
            "insert into cases(document_id, member_count, basis, status, data) values (null, 2, 'engagement', 'approved', %s) "
            "returning id", (ReferenceCase(title=Sourced[str](value="M", source_quote="m m m m")).model_dump_json(),)).fetchone()[0]
        c.execute("update cases set merged_into=%s where id in (%s,%s)", (merged, c1, c7))
    try:
        assert client([ADMIN]).post(f"/admin/sources/{sid}", data={"acl_groups": "g1"}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute("select status from cases where id=%s", (merged,)).fetchone()[0] == "extracted"
            assert c.execute("select status from cases where id=%s", (c7,)).fetchone()[0] == "approved"
            v = c.execute("select md5(data::text) from cases where id=%s", (merged,)).fetchone()[0]
        assert client([REV, "g1"]).post(f"/review/{merged}/approve", data={"v": v}).status_code == 409  # c1 is rejected
        # un-merging must not revive the retired member
        assert client([REV, "g1"]).post(f"/review/{merged}/unmerge", data={"v": v}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            st = dict(c.execute("select id, status from cases where id in (%s,%s,%s,%s)", (c1, c2, c3, c4)).fetchall())
            assert st == {c1: "rejected", c2: "approved", c3: "approved", c4: "approved"}
            left = c.execute("select (payload->>'document_id')::bigint from jobs where kind='extract' and "
                             "(payload->>'document_id')::bigint in (%s,%s,%s) order by 1", (e4, e5, e6)).fetchall()
            assert [r[0] for r in left] == [e5, e6]
    finally:
        _clean(sid)
        _clean(other)


def test_reticking_brings_back_untick_rejects(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, True)
        d = _doc(c, sid, "contract")
        cid = _case(c, d, "approved", "engagement", REASON)
    try:
        a = client([ADMIN])
        assert a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1"}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute("select status, retired_by from cases where id=%s", (cid,)).fetchone() == ("rejected", "untick")
        form = {"acl_groups": "g1", "executed_contracts": "on"}
        assert a.post(f"/admin/sources/{sid}", data=form, follow_redirects=False).status_code == 303
        with db.connect() as c:
            rows = _jobs(c, sid)
        assert [r[0] for r in rows] == [{"document_id": d, "basis": "engagement", "basis_reason": REASON, "checksum": "x"}]
    finally:
        _clean(sid)


def test_reticking_keeps_a_reviewers_reject(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, False)
        d = _doc(c, sid, "contract")
        cid = _case(c, d, "rejected", "engagement", REASON)  # no untick mark
    try:
        form = {"acl_groups": "g1", "executed_contracts": "on"}
        assert client([ADMIN]).post(f"/admin/sources/{sid}", data=form, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert _jobs(c, sid) == []
            assert c.execute("select status from cases where id=%s", (cid,)).fetchone() == ("rejected",)
    finally:
        _clean(sid)


def test_saving_without_flag_change_does_nothing(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        off, on = _src(c, False), _src(c, True)
        _doc(c, off, "contract")
        e1 = _case(c, _doc(c, on, "contract"), "approved", "engagement", REASON)
    try:
        for sid, data in ((off, {"acl_groups": "g1"}), (on, {"acl_groups": "g1", "executed_contracts": "on"})):
            assert client([ADMIN]).post(f"/admin/sources/{sid}", data=data, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert _jobs(c, off) == [] and _jobs(c, on) == []
            assert c.execute("select status from cases where id=%s", (e1,)).fetchone()[0] == "approved"
    finally:
        _clean(off)
        _clean(on)


def test_sources_page_upload_control_and_notices(env):  # noqa: F811
    from jinja2.filters import do_filesizeformat

    from app.main import upload_cap
    db.init()
    ids = {}
    with db.connect() as c:
        for key, kind, enabled in (("up", "upload", True), ("off", "upload", False), ("s3", "s3", True)):
            ids[key] = c.execute("insert into sources(kind,name,config,enabled) values (%s,%s,%s,%s) returning id",
                                 (kind, uuid.uuid4().hex, Jsonb({"bucket": "b"}), enabled)).fetchone()[0]
        name = c.execute("select name from sources where id=%s", (ids["up"],)).fetchone()[0]
    try:
        a = client([ADMIN])
        t = a.get("/admin/sources").text
        assert 'action="/admin/upload"' in t and 'enctype="multipart/form-data"' in t
        assert f'name="source_id" value="{ids["up"]}"' in t and do_filesizeformat(upload_cap()) in t
        assert f'name="source_id" value="{ids["off"]}"' not in t and f'name="source_id" value="{ids["s3"]}"' not in t
        t = a.get(f"/admin/sources?notice=uploaded&source={ids['up']}&job=7").text
        assert f"job 7 to {name}" in t
        t = a.get("/admin/sources?notice=<script>x</script>").text
        assert "<script>x" not in t and 'role="status"' not in t
        assert "allowed types are" in a.get("/admin/sources?notice=type").text
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id = any(%s)", (list(ids.values()),))


def test_crawl_now_shown_only_on_enabled_sources(env):  # noqa: F811
    """A disabled source's crawl route is a 404, so it offers no button (#142)."""
    db.init()
    with db.connect() as c:
        on, off = (c.execute("insert into sources(kind,name,config,enabled) values ('s3',%s,%s,%s) returning id",
                             (uuid.uuid4().hex, Jsonb({"bucket": "b"}), en)).fetchone()[0] for en in (True, False))
    try:
        t = client([ADMIN]).get("/admin/sources").text
        assert f'action="/admin/sources/{on}/crawl"' in t
        assert f'action="/admin/sources/{off}/crawl"' not in t
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id = any(%s)", ([on, off],))


def _old_case(c, did, status="extracted", version=None):
    data = ReferenceCase(title=Sourced[str](value="T"), **({"extraction_version": version} if version else {})).model_dump_json()
    if version is None:  # a row stored before the stamp existed
        import json
        data = json.dumps({k: v for k, v in json.loads(data).items() if k != "extraction_version"})
    return c.execute("insert into cases(document_id, status, data) values (%s,%s,%s) returning id", (did, status, data)).fetchone()[0]


def test_reextract_queues_only_older_live_unqueued_cases(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        n0 = c.execute("select count(*) " + OLDER, (EXTRACTION_VERSION,)).fetchone()[0]  # leftovers from elsewhere
        sid = _src(c, False)
        old, rejected, gone, queued, current = (_doc(c, sid, "contract") for _ in range(5))
        _old_case(c, old)
        _old_case(c, rejected, "rejected")
        c.execute("update documents set deleted_at=now() where id=%s", (gone,))
        _old_case(c, gone)
        _old_case(c, queued)
        _job(c, queued)
        _old_case(c, current, version=EXTRACTION_VERSION)
        c.execute("insert into cases(document_id, member_count, status, data) values (null, 2, 'extracted', %s)",
                  (ReferenceCase(title=Sourced[str](value="M")).model_dump_json(),))  # a merged parent, no version
    try:
        assert client([REV]).post("/admin/reextract").status_code == 403
        r = client([ADMIN]).post("/admin/reextract", follow_redirects=False)
        assert r.status_code == 303 and f"total={n0 + 1}" in r.headers["location"]
        if n0 < 25:  # ours come last by id, so a full batch of leftovers could crowd it out
            with db.connect() as c:
                assert [p["document_id"] for (p,) in _jobs(c, sid) if p.get("basis") == "delivered"] == [old]
            assert f"queued={n0 + 1}&" in r.headers["location"]
            assert f"{n0 + 1} of {n0 + 1}" in client([ADMIN]).get(r.headers["location"]).text
            again = client([ADMIN]).post("/admin/reextract", follow_redirects=False)
            assert "queued=0" in again.headers["location"]  # the jobs just queued block a duplicate
    finally:
        _clean(sid)
        with db.connect() as c:
            c.execute("delete from cases where document_id is null and member_count=2 and status='extracted'")


def test_reextract_skips_recent_failures_and_merged_members(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, False)
        failing = [_doc(c, sid, "contract") for _ in range(25)]
        for d in failing:
            _old_case(c, d)
            c.execute("insert into jobs(kind, payload, status) values ('extract', jsonb_build_object('document_id', %s::bigint), 'failed')", (d,))
        stale = _doc(c, sid, "contract")  # failed long ago: retried
        _old_case(c, stale)
        c.execute("insert into jobs(kind, payload, status, updated_at) values ('extract', "
                  "jsonb_build_object('document_id', %s::bigint), 'failed', now() - interval '3 days')", (stale,))
        good = _doc(c, sid, "contract")
        _old_case(c, good)
        member = _old_case(c, _doc(c, sid, "contract"))
        parent = c.execute("insert into cases(document_id, member_count, status, data) values (null, 2, 'extracted', %s) returning id",
                           (ReferenceCase(title=Sourced[str](value="M")).model_dump_json(),)).fetchone()[0]
        c.execute("update cases set merged_into=%s where id=%s", (parent, member))
        n0 = c.execute("select count(*) " + OLDER, (EXTRACTION_VERSION,)).fetchone()[0]
    try:
        assert n0 >= 2  # stale and good are counted, the 25 failures and the member are not
        client([ADMIN]).post("/admin/reextract", follow_redirects=False)
        with db.connect() as c:
            got = {p["document_id"] for (p,) in _jobs(c, sid) if p.get("basis") == "delivered"}
        assert (good in got and stale in got) or n0 > 25
        assert not got & set(failing) and len(got) <= 2
    finally:
        _clean(sid)
        with db.connect() as c:
            c.execute("delete from cases where id=%s", (parent,))


def test_reextract_is_capped_per_click(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, False)
        for _ in range(26):
            _old_case(c, _doc(c, sid, "contract"))
    try:
        r = client([ADMIN]).post("/admin/reextract", follow_redirects=False)
        assert "queued=25&total=26" in r.headers["location"]
    finally:
        _clean(sid)


def test_age_is_elapsed_whole_days_not_calendar_days(env):  # noqa: F811  plan #259 review
    db.init()
    names = {}
    with db.connect() as c:
        for h in (23, 25):
            n = f"el{h}-{uuid.uuid4().hex[:6]}"
            names[h] = n
            c.execute("insert into sources (kind, name, config, acl_groups, last_run_at) values ('upload', %s, '{}', %s, now() - make_interval(hours => %s))", (n, ["g"], h))
    try:
        html = client([ADMIN]).get("/admin/sources").text
        run = lambda h: html.split(names[h])[1].split("Last run:")[1][:160]
        assert "today" in run(23) and "1 day ago" in run(25)
    finally:
        with db.connect() as c:
            c.execute("delete from sources where name = any(%s)", (list(names.values()),))


def test_sources_css_keeps_44px_targets_and_the_opened_panel_on_row_two():
    from pathlib import Path
    css = (Path(__file__).parent.parent / "app" / "static" / "portal.css").read_text()
    assert re.search(r"@media \(any-pointer:coarse\)\{[^{]*details\.mini>summary[^{]*\{min-height:44px", css)  # plan #262 step 3: 44px on touch
    assert re.search(r"\.source-row\.src details\.mini>summary::before\{content:\"\";position:absolute;inset:-4px 0\}", css)
    assert ".src>*{grid-row:1}" in css and "grid-column:1 / -1;grid-row:2}" in css
    assert not re.search(r"\.head-panel>summary\{", css) and ".src-add>summary" in css


def test_last_run_shows_as_an_age_and_summaries_hide_the_marker(env):  # noqa: F811  plan #259 step 5
    from pathlib import Path
    db.init()
    name = f"age-{uuid.uuid4().hex[:8]}"
    with db.connect() as c:
        c.execute("insert into sources (kind, name, config, acl_groups, last_run_at) values ('upload', %s, '{}', %s, now() - interval '3 days')",
                  (name, ["g"]))
    try:
        html = client([ADMIN]).get("/admin/sources").text
        assert 'title="' in html.split(name)[1].split("Last run:")[1][:120] and "3 days ago" in html
    finally:
        with db.connect() as c:
            c.execute("delete from sources where name=%s", (name,))
    css = (Path(__file__).parent.parent / "app" / "static" / "portal.css").read_text()
    assert re.search(r"details\.mini>summary::-webkit-details-marker[^{]*\{display:none\}", css) and "details.mini>summary{list-style:none" in css


def test_source_grid_is_scoped_to_the_sources_rows_and_review_rows_keep_their_layout():  # plan #259 review
    from pathlib import Path
    root = Path(__file__).parent.parent / "app"
    css = (root / "static" / "portal.css").read_text()
    assert not re.search(r"\.source-row\{display:grid", css) and ".source-row.src{display:grid" in css
    assert 'class="source-row src"' in (root / "templates" / "sources.html").read_text()
    for page in ("review_list.html", "documents.html"):
        assert 'source-row src' not in (root / "templates" / page).read_text()
    block = css.split("@media (min-width:900px){\n.source-row.src")[1].split("\n}")[0]
    assert not re.search(r"^\.source-row[ >:{.]", block.split("\n", 1)[1], re.M) and ".field-row>:nth-child(3):last-child{grid-column:3 / -1}" in css
    assert re.search(r"\.field-row \.acts\{display:flex;flex-wrap:wrap;gap:6px\}\n@media \(min-width:900px\)\{\n\.field-row \.acts\{flex-direction:column", css)


def test_capability_drafts_queue_only_skipped_proposals_decks_and_contracts(env):  # noqa: F811  #213
    from app.sources import SKIPPED
    db.init()
    with db.connect() as c:
        n0 = c.execute("select count(*) " + SKIPPED).fetchone()[0]  # leftovers from elsewhere
        sid = _src(c, False)
        want = {_doc(c, sid, k) for k in ("proposal", "deck", "contract")}
        _doc(c, sid, "other")
        _doc(c, sid, "case")
        _doc(c, sid, "proposal", deleted=True)
        _case(c, _doc(c, sid, "proposal"), "extracted", "delivered", "")
        _job(c, _doc(c, sid, "deck"))
        _job(c, _doc(c, sid, "proposal"), status="failed")  # a failed extract of a delivered/engagement document is not "skipped"
    try:
        assert "Capability drafts from skipped documents (" + str(n0 + 3) + ")" in client([ADMIN]).get("/admin/sources").text
        assert client([REV]).post("/admin/capability-drafts").status_code == 403
        r = client([ADMIN]).post("/admin/capability-drafts", follow_redirects=False)
        assert r.status_code == 303 and f"total={n0 + 3}" in r.headers["location"]
        with db.connect() as c:
            got = [p for (p,) in _jobs(c, sid) if p["basis"] == "capability"]
        assert {p["document_id"] for p in got} == want and len(got) == 3
        again = client([ADMIN]).post("/admin/capability-drafts", follow_redirects=False)
        assert "queued=0" in again.headers["location"]  # the jobs just queued block a duplicate
    finally:
        _clean(sid)


def test_capability_drafts_are_capped_per_click_and_the_next_click_continues(env):  # noqa: F811  #213
    from app.sources import REEXTRACT_BATCH, SKIPPED
    db.init()
    with db.connect() as c:
        n0 = c.execute("select count(*) " + SKIPPED).fetchone()[0]
        sid = _src(c, False)
        for _ in range(REEXTRACT_BATCH + 2):
            _doc(c, sid, "deck")
    try:
        r = client([ADMIN]).post("/admin/capability-drafts", follow_redirects=False)
        assert f"queued={REEXTRACT_BATCH}&total={n0 + REEXTRACT_BATCH + 2}" in r.headers["location"]
        if n0 == 0:  # with leftovers from elsewhere ahead of ours (by id), the split between clicks is not ours to assert
            with db.connect() as c:
                assert len(_jobs(c, sid)) == REEXTRACT_BATCH
            r = client([ADMIN]).post("/admin/capability-drafts", follow_redirects=False)
            assert "queued=2&total=2" in r.headers["location"]
            with db.connect() as c:
                assert len(_jobs(c, sid)) == REEXTRACT_BATCH + 2
    finally:
        _clean(sid)


TENANT = "9b1c6f0e-2f43-4c56-9a7b-0d1e2f3a4b5c"


@pytest.mark.parametrize("kind, fields, want", [
    ("s3", {"bucket": "refs-bkt", "prefix": "a/b", "include_ext": ".PDF, docx"},
     {"bucket": "refs-bkt", "prefix": "a/b", "include_ext": ["pdf", "docx"]}),
    ("s3", {"bucket": "refs-bkt"}, {"bucket": "refs-bkt", "prefix": ""}),
    ("upload", {"bucket": "refs-bkt", "prefix": "uploads/"}, {"bucket": "refs-bkt", "prefix": "uploads/"}),
    ("sharepoint", {"tenant_id": TENANT.upper(), "drive_id": "b!x_y-z"}, {"tenant_id": TENANT, "drive_id": "b!x_y-z"}),
    ("confluence", {"base_url": "https://x.atlassian.net/", "spaces": "ENG, ~me", "api_prefix": "/wiki"},
     {"base_url": "https://x.atlassian.net", "spaces": ["ENG", "~me"], "api_prefix": "/wiki"}),
])
def test_parse_config_builds_only_the_named_keys(kind, fields, want):
    assert parse_config(kind, {**fields, "token": "secret", "password": "x"}) == want


@pytest.mark.parametrize("kind, fields", [
    ("ftp", {"bucket": "refs-bkt"}),
    ("s3", {"bucket": "B"}), ("s3", {"bucket": "UPPER-case"}),
    ("s3", {"bucket": "refs-bkt", "prefix": "/abs"}), ("s3", {"bucket": "refs-bkt", "prefix": "a/../b"}),
    ("s3", {"bucket": "refs-bkt", "prefix": "a" * 1025}),
    ("s3", {"bucket": "refs-bkt", "include_ext": "pdf, ex e"}), ("s3", {"bucket": "refs-bkt", "include_ext": "abcdefghijk"}),
    ("sharepoint", {"tenant_id": "t", "drive_id": "d"}), ("sharepoint", {"tenant_id": TENANT, "drive_id": "a/b"}),
    ("confluence", {"base_url": "http://x.atlassian.net", "spaces": "ENG"}),
    ("confluence", {"base_url": "https://u:p@x.atlassian.net", "spaces": "ENG"}),
    ("confluence", {"base_url": "https://x.atlassian.net?t=1", "spaces": "ENG"}),
    ("confluence", {"base_url": "https://x.atlassian.net#f", "spaces": "ENG"}),
    ("confluence", {"base_url": "https://x.atlassian.net:8443", "spaces": "ENG"}),
    ("confluence", {"base_url": "https://x.atlassian.net", "spaces": ""}),
    ("confluence", {"base_url": "https://x.atlassian.net", "spaces": "EN G"}),
    ("confluence", {"base_url": "https://x.atlassian.net", "spaces": "ENG", "api_prefix": "/rest"}),
])
def test_parse_config_refuses(kind, fields):
    with pytest.raises(HTTPException) as e:
        parse_config(kind, {"api_prefix": "/wiki", **fields})
    assert e.value.status_code == 400


def _source(c, name):
    return c.execute("select id, name, config, cursor, enabled from sources where name=%s", (name,)).fetchone()


def _changes(sid):
    with db.connect() as c:
        return c.execute("select action, old, new, changed_by from source_config_changes where source_id=%s order by id",
                         (sid,)).fetchall()


def test_edit_scope_keeps_root_resets_cursor_and_is_audited(env):  # noqa: F811
    db.init()
    name, a = f"cf-{uuid.uuid4().hex[:8]}", client([ADMIN])
    assert a.post("/admin/sources", follow_redirects=False, data={
        "kind": "confluence", "name": name, "acl_groups": "g1", "base_url": "https://x.atlassian.net",
        "spaces": "ENG"}).status_code == 303
    with db.connect() as c:
        sid, _, cfg, _, _ = _source(c, name)
        c.execute("update sources set cursor='2026-01-01T00:00:00+00:00' where id=%s", (sid,))
    try:
        assert cfg == {"base_url": "https://x.atlassian.net", "spaces": ["ENG"], "api_prefix": "/wiki"}
        (action, old, new, who), = _changes(sid)
        assert (action, old, new["config"], who) == ("create", None, cfg, "u1")
        # an ACL-only post: config and cursor unchanged, no config row
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "enabled": "on"})
        with db.connect() as c:
            assert _source(c, name)[2:4] == ({**cfg, "executed_contracts": False}, "2026-01-01T00:00:00+00:00")
        assert len(_changes(sid)) == 1
        # root fields posted are ignored; the scope changes and the cursor resets
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "enabled": "on", "spaces": "ENG, OPS",
                                              "base_url": "https://evil.example", "name": name + "-2"})
        with db.connect() as c:
            _, new_name, cfg2, cursor, _ = _source(c, name + "-2")
        assert cfg2["base_url"] == "https://x.atlassian.net" and cfg2["spaces"] == ["ENG", "OPS"] and cursor is None
        action, old, new, _ = _changes(sid)[-1]
        assert action == "config" and old["config"]["spaces"] == ["ENG"] and new == {"name": new_name, "config": cfg2}
        assert a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "spaces": "E G"}).status_code == 400
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1"})  # unchecked: disabled
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "enabled": "on"})
        assert [r[0] for r in _changes(sid)[-2:]] == ["disable", "enable"]
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (sid,))


def test_upload_prefix_and_staff_survive_an_edit(env):  # noqa: F811
    db.init()
    name, a = f"up-{uuid.uuid4().hex[:8]}", client([ADMIN])
    a.post("/admin/sources", data={"kind": "upload", "name": name, "acl_groups": "g1", "bucket": "refs-bkt",
                                   "prefix": "uploads/", "staff": "on"})
    with db.connect() as c:
        sid, _, cfg, _, _ = _source(c, name)
    try:
        assert cfg == {"bucket": "refs-bkt", "prefix": "uploads/", "staff": True}
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "prefix": "elsewhere/", "include_ext": "pdf"})
        with db.connect() as c:
            cfg2 = _source(c, name)[2]
        assert cfg2["prefix"] == "uploads/" and cfg2["staff"] is True and cfg2["include_ext"] == ["pdf"]
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (sid,))


def test_edit_refused_while_the_source_is_crawling(env):  # noqa: F811
    db.init()
    name, a = f"s3-{uuid.uuid4().hex[:8]}", client([ADMIN])
    a.post("/admin/sources", data={"kind": "s3", "name": name, "acl_groups": "g1", "bucket": "refs-bkt"})
    with db.connect() as c:
        sid, _, cfg, _, enabled = _source(c, name)
    try:
        with db.connect(autocommit=True) as crawl:  # what a crawler holds for its whole run
            crawl.execute("select pg_advisory_lock(2, %s)", (sid,))
            r = a.post(f"/admin/sources/{sid}", data={"acl_groups": "g9", "prefix": "x/"})
            html = a.post(f"/admin/sources/{sid}", data={"acl_groups": "g9"}, headers={"accept": "text/html"},
                          follow_redirects=False)
            crawl.execute("select pg_advisory_unlock(2, %s)", (sid,))
        assert r.status_code == 409 and html.headers["location"] == "/admin/sources?notice=crawling"
        with db.connect() as c:
            assert _source(c, name)[2:] == (cfg, None, enabled)
            assert c.execute("select acl_groups from sources where id=%s", (sid,)).fetchone()[0] == ["g1"]
        assert [r[0] for r in _changes(sid)] == ["create"]
        assert "A crawl is running" in a.get("/admin/sources?notice=crawling").text
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (sid,))
