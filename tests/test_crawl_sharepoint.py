import json
import uuid
from urllib.parse import parse_qs

import boto3
import httpx
import pytest
from moto import mock_aws

from app import crawl, db, ingest as ing, worker

G = "https://graph.microsoft.com/v1.0/drives/D1"
ROOT = f"{G}/root/delta"
SECRET = "s3cr3t-value"
# the drive root's grants (site groups); a normal file inherits exactly these
ROOT_PERMS = [{"id": "o", "roles": ["owner"], "grantedToV2": {"siteGroup": {"id": "3", "displayName": "Owners"}}},
              {"id": "m", "roles": ["write"], "grantedToV2": {"siteGroup": {"id": "5", "displayName": "Members"}}}]


def inherited(frm="root"):
    return [dict(p, inheritedFrom={"id": frm}) for p in ROOT_PERMS]


def f(iid, name="a.docx", size=10):
    return {"id": iid, "name": name, "size": size, "file": {}}


def page(items, nxt=None, delta=None):
    return {"value": items, **({"@odata.nextLink": nxt} if nxt else {}), **({"@odata.deltaLink": delta} if delta else {})}


class Site:
    """A fake Graph tenant: delta pages by URL, file bytes, permissions, per-file status queues."""

    def __init__(self):
        self.delta, self.files, self.perms, self.statuses, self.calls, self.forms = {}, {}, {}, {}, [], []
        self.root_perms, self.perm_errors = ROOT_PERMS, {}

    def __call__(self, request):
        url = str(request.url).split("?")[0] if "token=" not in str(request.url) else str(request.url)
        self.calls.append(url)
        if request.url.host == "login.microsoftonline.com":
            self.forms.append(parse_qs(request.content.decode()))
            return httpx.Response(self.forms[-1].get("client_secret") == [SECRET] and 200 or 400,
                                  json={"access_token": "tok"})
        assert request.headers["authorization"] == "Bearer tok"
        path = request.url.path
        if path.endswith("/root/permissions"):
            return httpx.Response(200, json={"value": self.root_perms})
        if "/items/" in path:
            iid = path.split("/items/")[1].split("/")[0]
            if path.endswith("/permissions"):
                if iid in self.perm_errors:
                    return httpx.Response(self.perm_errors[iid])
                return httpx.Response(200, json={"value": self.perms.get(iid, inherited())})
            if not path.endswith("/content"):  # item metadata (retry by id)
                return httpx.Response(200, json=f(iid)) if iid in self.files else httpx.Response(404)
            queue = self.statuses.get(iid, [])
            if queue:
                code, headers = queue.pop(0) if len(queue) > 1 or queue[0][0] != 503 else queue[0]
                if code != 200:
                    return httpx.Response(code, headers=headers, text="error mentioning /sites/Secret Site/file.docx")
            return httpx.Response(200, content=self.files[iid])
        if url not in self.delta:
            return httpx.Response(404)
        code, body = self.delta[url]
        return httpx.Response(code, json=body)

    def content_calls(self):
        return [c.split("/items/")[1].split("/")[0] for c in self.calls if c.endswith("/content")]


@pytest.fixture
def sp(monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", SECRET)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "orig")
    monkeypatch.setattr(ing, "to_markdown", lambda data, name: data.decode())
    monkeypatch.setattr(ing, "complete_json", lambda *a, **k: ing.Triage(kind="case", describes_delivered_work=True))
    site = Site()
    monkeypatch.setattr(crawl, "TRANSPORT", httpx.MockTransport(site))
    sleeps = []
    monkeypatch.setattr(crawl.time, "sleep", sleeps.append)
    site.sleeps = sleeps
    db.init()
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="orig")
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,config,acl_groups) values ('sharepoint',%s,%s,%s) returning id",
                            (uuid.uuid4().hex, json.dumps({"tenant_id": "T", "drive_id": "D1"}), ["g-site"])).fetchone()[0]
        site.sid = sid
        yield site
        with db.connect() as c:
            c.execute("delete from jobs where (payload->>'document_id')::bigint in (select id from documents where source_id=%s)", (sid,))
            c.execute("delete from sources where id=%s", (sid,))


def source(sid):
    with db.connect() as c:
        return c.execute("select cursor, last_counts, acl_groups from sources where id=%s", (sid,)).fetchone()


def docs(sid):
    with db.connect() as c:
        return {r[0]: (r[1], r[2]) for r in c.execute(
            "select external_id, deleted_at is not null, acl_groups from documents where source_id=%s", (sid,))}


def first_run(sp):
    sp.files = {"f1": b"one", "f2": b"two"}
    sp.delta[ROOT] = (200, page([f("f1")], nxt=f"{ROOT}?token=p2"))
    sp.delta[f"{ROOT}?token=p2"] = (200, page([{"id": "dir", "name": "x", "folder": {}}, f("f2", "b.pdf")], delta=f"{ROOT}?token=d1"))
    return crawl.crawl_sharepoint(sp.sid)


def test_full_crawl_follows_pages_and_stores_delta_link(sp):
    counts = first_run(sp)
    assert counts["new"] == 2 and counts["failed"] == 0
    assert source(sp.sid)[0] == f"{ROOT}?token=d1"
    assert docs(sp.sid) == {"f1": (False, ["g-site"]), "f2": (False, ["g-site"])}
    assert sp.forms[0]["grant_type"] == ["client_credentials"] and sp.forms[0]["scope"] == ["https://graph.microsoft.com/.default"]


def test_second_run_uses_delta_link_and_fetches_only_changes(sp):
    first_run(sp)
    sp.calls.clear()
    sp.files["f1"] = b"one edited"
    sp.delta[f"{ROOT}?token=d1"] = (200, page([f("f1")], delta=f"{ROOT}?token=d2"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["updated"] == 1 and counts["new"] == 0
    assert sp.content_calls() == ["f1"] and ROOT not in sp.calls
    assert source(sp.sid)[0] == f"{ROOT}?token=d2"
    sp.delta[f"{ROOT}?token=d2"] = (200, page([], delta=f"{ROOT}?token=d3"))
    sp.calls.clear()
    crawl.crawl_sharepoint(sp.sid)
    assert sp.content_calls() == []


def test_deleted_item_marked(sp):
    first_run(sp)
    sp.delta[f"{ROOT}?token=d1"] = (200, page([{"id": "f1", "deleted": {"state": "deleted"}}], delta=f"{ROOT}?token=d2"))
    assert crawl.crawl_sharepoint(sp.sid)["deleted"] == 1
    assert docs(sp.sid)["f1"][0] is True and docs(sp.sid)["f2"][0] is False


def test_unique_permissions_skipped_and_withdrawn(sp):
    sp.files = {"f1": b"one", "hr": b"salaries"}
    sp.perms["hr"] = inherited() + [{"id": "u", "roles": ["read"], "grantedToV2": {"user": {"id": "u9"}}}]  # its own grant
    sp.delta[ROOT] = (200, page([f("f1"), f("hr")], delta=f"{ROOT}?token=d1"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["new"] == 1 and counts["skipped_unique_permissions"] == 1
    assert set(docs(sp.sid)) == {"f1"} and "hr" not in sp.content_calls()
    # an earlier ingest of a file that later got its own permissions is withdrawn
    sp.perms["f1"] = []  # nothing readable: unknown, so fail closed
    sp.delta[f"{ROOT}?token=d1"] = (200, page([f("f1")], delta=f"{ROOT}?token=d2"))
    crawl.crawl_sharepoint(sp.sid)
    assert docs(sp.sid)["f1"][0] is True


def test_throttle_honours_retry_after_then_succeeds(sp):
    sp.files = {"f1": b"one"}
    sp.statuses["f1"] = [(429, {"Retry-After": "3"}), (200, {})]
    sp.delta[ROOT] = (200, page([f("f1")], delta=f"{ROOT}?token=d1"))
    assert crawl.crawl_sharepoint(sp.sid)["new"] == 1 and sp.sleeps == [3]


def test_persistent_503_counts_failed_and_crawl_continues(sp):
    sp.files = {"f1": b"one", "f2": b"two"}
    sp.statuses["f1"] = [(503, {"Retry-After": "999"})]
    sp.delta[ROOT] = (200, page([f("f1"), f("f2")], delta=f"{ROOT}?token=d1"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["failed"] == 1 and counts["new"] == 1
    assert sp.sleeps == [60, 60, 60]  # capped, then given up
    assert source(sp.sid)[1]["failed_keys"] == [{"key": "f1", "error": "GraphError"}]
    assert "Secret Site" not in json.dumps(source(sp.sid)[1])


def test_410_resyncs_from_scratch(sp):
    first_run(sp)
    sp.delta[f"{ROOT}?token=d1"] = (410, {})
    sp.delta[ROOT] = (200, page([f("f2", "b.pdf")], delta=f"{ROOT}?token=d9"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert source(sp.sid)[0] == f"{ROOT}?token=d9"
    assert counts["deleted"] == 1 and docs(sp.sid)["f1"][0] is True  # full listing: f1 is gone


def test_incomplete_enumeration_keeps_cursor(sp):
    first_run(sp)
    sp.delta[f"{ROOT}?token=d1"] = (200, page([f("f1")], nxt=f"{ROOT}?token=gone"))
    with pytest.raises(crawl.GraphError):
        crawl.crawl_sharepoint(sp.sid)
    assert source(sp.sid)[0] == f"{ROOT}?token=d1"


def test_secret_never_stored_or_in_errors(sp, monkeypatch):
    first_run(sp)
    assert SECRET not in json.dumps(source(sp.sid)[1])
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "wrong-secret-value")
    sp.delta[f"{ROOT}?token=d1"] = (200, page([], delta=f"{ROOT}?token=d2"))
    with pytest.raises(crawl.GraphError, match=r"login failed \(400\)") as e:
        crawl.crawl_sharepoint(sp.sid)
    assert "wrong-secret-value" not in str(e.value) and SECRET not in str(e.value)
    monkeypatch.delenv("GRAPH_CLIENT_SECRET")
    with pytest.raises(RuntimeError, match="GRAPH_CLIENT_SECRET"):
        crawl.crawl_sharepoint(sp.sid)


def test_source_acl_change_applies_to_existing_docs(sp):
    first_run(sp)
    with db.connect() as c:
        c.execute("update sources set acl_groups=%s where id=%s", (["g-new"], sp.sid))
    sp.delta[f"{ROOT}?token=d1"] = (200, page([], delta=f"{ROOT}?token=d2"))
    crawl.crawl_sharepoint(sp.sid)
    assert {v[1][0] for v in docs(sp.sid).values()} == {"g-new"}


def test_type_and_size_filters(sp):
    sp.files = {"ok": b"x", "t": b"x", "big": b"x"}
    sp.delta[ROOT] = (200, page([f("ok"), f("t", "notes.txt"), f("big", size=10**9)], delta=f"{ROOT}?token=d1"))
    c = crawl.crawl_sharepoint(sp.sid)
    assert (c["new"], c["skipped_type"], c["skipped_too_large"]) == (1, 1, 1)


def test_concurrent_crawl_reports_running(sp):
    with db.connect(autocommit=True) as other:
        other.execute("select pg_advisory_lock(2, %s)", (sp.sid,))
        assert crawl.crawl_sharepoint(sp.sid) == {"status": "running"}


def test_worker_dispatches_crawl_sharepoint():
    assert "crawl_sharepoint" in worker.HANDLERS


def test_file_under_restricted_folder_is_skipped(sp):
    """Every entry is inherited, but from a folder whose access differs from the root's."""
    sp.files = {"hr": b"secret"}
    sp.perms["hr"] = [{"id": "x", "roles": ["write"], "grantedToV2": {"group": {"id": "g-hr"}},
                       "inheritedFrom": {"id": "folderX"}}]
    sp.delta[ROOT] = (200, page([f("hr")], delta=f"{ROOT}?token=d1"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["skipped_unique_permissions"] == 1 and "hr" not in docs(sp.sid)


def test_sharing_link_on_file_is_skipped(sp):
    sp.files = {"l": b"x"}
    sp.perms["l"] = inherited() + [{"id": "k", "roles": ["read"], "link": {"scope": "organization"}}]
    sp.delta[ROOT] = (200, page([f("l")], delta=f"{ROOT}?token=d1"))
    assert crawl.crawl_sharepoint(sp.sid)["skipped_unique_permissions"] == 1


def test_permission_change_after_ingest_is_caught_by_recheck(sp):
    first_run(sp)
    sp.perms["f1"] = [{"id": "x", "roles": ["write"], "grantedToV2": {"group": {"id": "g-hr"}},
                       "inheritedFrom": {"id": "folderX"}}]  # folder restricted later; delta says nothing
    sp.delta[f"{ROOT}?token=d1"] = (200, page([], delta=f"{ROOT}?token=d2"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["withdrawn_on_recheck"] == 1 and docs(sp.sid)["f1"][0] is True and docs(sp.sid)["f2"][0] is False


def test_unverifiable_permissions_withdraw_on_recheck(sp):
    first_run(sp)
    sp.perm_errors["f2"] = 404
    sp.delta[f"{ROOT}?token=d1"] = (200, page([], delta=f"{ROOT}?token=d2"))
    assert crawl.crawl_sharepoint(sp.sid)["withdrawn_on_recheck"] == 1 and docs(sp.sid)["f2"][0] is True


def test_invisible_root_permissions_fail_the_crawl_loudly(sp):
    sp.root_perms = []  # read-only grant: the app cannot see the site's permissions
    with pytest.raises(crawl.GraphError, match="fullcontrol"):
        first_run(sp)


def test_failed_item_is_retried_next_run(sp):
    sp.files = {"f1": b"one"}
    sp.statuses["f1"] = [(503, {"Retry-After": "1"})]  # persistent this run
    sp.delta[ROOT] = (200, page([f("f1")], delta=f"{ROOT}?token=d1"))
    assert crawl.crawl_sharepoint(sp.sid)["failed"] == 1 and source(sp.sid)[1]["retry_ids"] == ["f1"]
    sp.statuses["f1"] = []  # recovered; delta has nothing new for it
    sp.delta[f"{ROOT}?token=d1"] = (200, page([], delta=f"{ROOT}?token=d2"))
    counts = crawl.crawl_sharepoint(sp.sid)
    assert counts["new"] == 1 and source(sp.sid)[1]["retry_ids"] == [] and docs(sp.sid)["f1"][0] is False
