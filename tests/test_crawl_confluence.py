import base64
import json
import re
import uuid
from datetime import datetime

import boto3
import httpx
import pytest
from moto import mock_aws

from app import crawl, db, ingest as ing, worker

API = "/wiki/rest/api/content"
TOKEN = "tok-s3cr3t"
BASE = "https://c.example"


class Conf:
    """A fake Confluence: pages, restrictions, attachments, per-path status queues."""

    def __init__(self):
        self.pages, self.restricted, self.atts, self.files, self.queue = {}, set(), {}, {}, {}
        self.calls, self.auth, self.cqls = [], [], []

    def page(self, pid, html="<p>hello</p>", when="2026-01-01T00:00:00.000Z", ancestors=(), title=None):
        # ancestors: ids, or (id, type) for non-page ancestors such as folders
        anc = [a if isinstance(a, tuple) else (a, "page") for a in ancestors]
        self.pages[pid] = {"id": pid, "type": "page", "title": title or f"Page {pid}", "status": "current",
                           "version": {"when": when}, "body": {"storage": {"value": html}},
                           "ancestors": [{"id": i} for i, t in anc if t == "page"], "_v2": anc}

    def attach(self, pid, aid, title="spec.docx", data=b"att", size=None, when="2026-01-01T00:00:00.000Z"):
        self.atts.setdefault(pid, []).append({
            "id": aid, "type": "attachment", "title": title, "status": "current", "container": {"id": pid},
            "version": {"when": when}, "extensions": {"fileSize": size or len(data)},
            "_links": {"download": f"/download/attachments/{pid}/{title}"}})
        self.files[f"/wiki/download/attachments/{pid}/{title}"] = data

    def __call__(self, request):
        path, q = request.url.path, dict(request.url.params)
        self.calls.append(path)
        self.auth.append(request.headers.get("authorization"))
        if self.queue.get(path):
            code, headers = self.queue[path][0] if len(self.queue[path]) == 1 and self.queue[path][0][0] >= 500 else self.queue[path].pop(0)
            if code != 200:
                return httpx.Response(code, headers=headers, text="error about Secret Space / Secret Page")
        if path in self.files:
            return httpx.Response(200, content=self.files[path])
        if m := re.fullmatch(r"/wiki/api/v2/pages/(\w+)/ancestors", path):
            if m[1] not in self.pages:
                return httpx.Response(404)
            return httpx.Response(200, json={"results": [{"id": i, "type": t} for i, t in self.pages[m[1]]["_v2"]]})
        assert path.startswith(API), path
        rest, links = path[len(API):], {"base": f"{BASE}/wiki"}
        if rest == "/search":
            self.cqls.append(q["cql"])
            since = re.search(r'lastmodified > "([^"]+)"', q["cql"])
            floor = datetime.strptime(since[1], "%Y/%m/%d %H:%M") if since else None
            items = [p for _, p in sorted(self.pages.items())] + [a for _, al in sorted(self.atts.items()) for a in al]
            hits = [p for p in items if p["status"] == "current"  # CQL: current content only
                    and (not floor or datetime.fromisoformat(p["version"]["when"]).replace(tzinfo=None) > floor)]
            start = int(q.get("start", 0))
            if start + 2 < len(hits):
                links["next"] = f"/rest/api/content/search?cql={q['cql']}&start={start + 2}"
            return httpx.Response(200, json={"results": hits[start:start + 2], "_links": links})
        if m := re.fullmatch(r"/(\w+)/restriction/byOperation/read", rest):
            who = [{"id": "u"}] if m[1] in self.restricted else []
            return httpx.Response(200, json={"restrictions": {"user": {"results": who, "size": len(who)},
                                                              "group": {"results": [], "size": 0}}})
        if m := re.fullmatch(r"/(\w+)/child/attachment", rest):
            return httpx.Response(200, json={"results": self.atts.get(m[1], []), "_links": links})
        if m := re.fullmatch(r"/(\w+)", rest):
            if m[1] in self.pages:
                return httpx.Response(200, json=self.pages[m[1]])
            for atts in self.atts.values():
                for a in atts:
                    if a["id"] == m[1]:
                        return httpx.Response(200, json=a)
        return httpx.Response(404, text="Secret Page not found")


@pytest.fixture
def cf(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", TOKEN)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "orig")
    monkeypatch.setattr(ing, "to_markdown", lambda data, name: data.decode())
    monkeypatch.setattr(ing, "complete_json", lambda *a, **k: ing.Triage(kind="case", describes_delivered_work=True))
    site = Conf()
    monkeypatch.setattr(crawl, "TRANSPORT", httpx.MockTransport(site))
    site.sleeps = []
    monkeypatch.setattr(crawl.time, "sleep", site.sleeps.append)
    db.init()
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="orig")
        with db.connect() as c:
            site.sid = c.execute("insert into sources(kind,name,config,acl_groups) values ('confluence',%s,%s,%s) returning id",
                                 (uuid.uuid4().hex, json.dumps({"base_url": BASE, "spaces": ["ENG", 'A"B']}), ["g-conf"])).fetchone()[0]
        yield site
        with db.connect() as c:
            c.execute("delete from jobs where (payload->>'document_id')::bigint in (select id from documents where source_id=%s)", (site.sid,))
            c.execute("delete from sources where id=%s", (site.sid,))


def src(sid):
    with db.connect() as c:
        return c.execute("select cursor, last_counts from sources where id=%s", (sid,)).fetchone()


def live(sid):
    with db.connect() as c:
        return {r[0]: r[1] for r in c.execute(
            "select external_id, acl_groups from documents where source_id=%s and deleted_at is null", (sid,))}


def seed(cf):
    cf.page("p1", "<p>one</p>", "2026-01-01T00:00:00.000Z")
    cf.page("p2", "<p>two</p>", "2026-03-01T00:00:00.000Z")
    cf.page("p3", "<p>three</p>", "2026-02-01T00:00:00.000Z")
    cf.attach("p1", "a1", "spec.docx", b"spec bytes")


def test_first_run_pages_attachments_cursor_and_acl(cf):
    seed(cf)
    c = crawl.crawl_confluence(cf.sid)
    assert c["new"] == 4 and c["failed"] == 0
    assert live(cf.sid) == {k: ["g-conf"] for k in ("page:p1", "page:p2", "page:p3", "att:p1:a1")}
    assert src(cf.sid)[0] == "2026-03-01T00:00:00+00:00"
    assert 'lastmodified' not in cf.cqls[0] and 'space in ("ENG", "A\\"B") and type in (page, attachment)' in cf.cqls[0]
    assert cf.calls.count(f"{API}/search") == 2  # followed _links.next


def test_second_run_sends_lastmodified_and_fetches_only_changes(cf):
    seed(cf)
    crawl.crawl_confluence(cf.sid)
    cf.pages["p2"]["body"]["storage"]["value"] = "<p>two edited</p>"
    cf.pages["p2"]["version"]["when"] = "2026-03-05T00:00:00.000Z"
    cf.calls.clear()
    c = crawl.crawl_confluence(cf.sid)
    assert 'lastmodified > "2026/02/28 00:00"' in cf.cqls[-1]
    assert c["updated"] == 1 and c["new"] == 0
    assert f"{API}/p1/child/attachment" not in cf.calls  # p1 was not offered again
    assert src(cf.sid)[0] == "2026-03-05T00:00:00+00:00"


def test_restricted_page_and_restricted_ancestor_skipped(cf):
    cf.page("own")
    cf.page("child", ancestors=["top", "mid"])
    cf.page("open", ancestors=["top2"])
    cf.attach("own", "a1")
    cf.attach("child", "a2")
    cf.restricted |= {"own", "mid"}  # the ancestor "mid" is restricted, "child" itself is not
    c = crawl.crawl_confluence(cf.sid)
    assert c["skipped_restricted"] == 2 and set(live(cf.sid)) == {"page:open"}
    assert f"{API}/own/child/attachment" not in cf.calls and f"{API}/child/child/attachment" not in cf.calls


def test_restriction_added_after_ingest_withdraws_page_and_attachments(cf):
    seed(cf)
    crawl.crawl_confluence(cf.sid)
    cf.restricted.add("p1")  # unchanged page: CQL will not report it, only the re-check can
    c = crawl.crawl_confluence(cf.sid)
    assert c["withdrawn_on_recheck"] == 2 and set(live(cf.sid)) == {"page:p2", "page:p3"}


def test_ancestor_restricted_after_ingest_is_withdrawn(cf):
    cf.page("kid", ancestors=["parent"], when="2026-01-01T00:00:00.000Z")
    cf.page("newer", when="2026-06-01T00:00:00.000Z")  # moves the cursor past kid + slack: CQL will not offer kid again
    crawl.crawl_confluence(cf.sid)
    assert "page:kid" in live(cf.sid)
    cf.restricted.add("parent")
    assert crawl.crawl_confluence(cf.sid)["withdrawn_on_recheck"] == 1
    assert set(live(cf.sid)) == {"page:newer"}


def test_deleted_or_trashed_page_and_attachment_withdrawn(cf):
    seed(cf)
    crawl.crawl_confluence(cf.sid)
    del cf.pages["p3"]  # 404
    cf.pages["p2"]["status"] = "trashed"
    cf.atts["p1"][0]["status"] = "trashed"
    c = crawl.crawl_confluence(cf.sid)
    assert c["withdrawn_on_recheck"] == 3 and set(live(cf.sid)) == {"page:p1"}


def test_attachment_filters(cf):
    cf.page("p1")
    cf.attach("p1", "a1", "ok.pdf", b"x")
    cf.attach("p1", "a2", "notes.txt", b"x")
    cf.attach("p1", "a3", "big.docx", b"x", size=10**9)
    c = crawl.crawl_confluence(cf.sid)
    assert (c["skipped_type"], c["skipped_too_large"]) == (1, 1)
    assert set(live(cf.sid)) == {"page:p1", "att:p1:a1"}


@pytest.mark.parametrize("email,expect", [("bot@example.com", "Basic " + base64.b64encode(f"bot@example.com:{TOKEN}".encode()).decode()),
                                          (None, f"Bearer {TOKEN}")])
def test_auth_headers(cf, monkeypatch, email, expect):
    if email is None:
        monkeypatch.delenv("CONFLUENCE_EMAIL")
    cf.page("p1")
    crawl.crawl_confluence(cf.sid)
    assert set(cf.auth) == {expect}


def test_token_never_stored_or_in_errors(cf):
    seed(cf)
    cf.queue[f"{API}/search"] = [(403, {})]
    with pytest.raises(crawl.ConfluenceError) as e:
        crawl.crawl_confluence(cf.sid)
    assert str(e.value) == "Confluence request failed (403)" and TOKEN not in str(e.value)
    assert src(cf.sid)[0] is None  # an enumeration error keeps the cursor
    cf.queue.clear()
    crawl.crawl_confluence(cf.sid)
    assert TOKEN not in json.dumps(src(cf.sid)[1]) and "Secret" not in json.dumps(src(cf.sid)[1])


def test_throttle_retry_after(cf):
    cf.page("p1")
    cf.queue[f"{API}/p1/restriction/byOperation/read"] = [(429, {"Retry-After": "2"}), (200, {})]
    assert crawl.crawl_confluence(cf.sid)["new"] == 1 and cf.sleeps == [2]


def test_failure_withdraws_and_is_retried_next_run(cf):
    cf.page("p1", when="2026-01-01T00:00:00.000Z")
    crawl.crawl_confluence(cf.sid)
    cf.pages["p1"]["body"]["storage"]["value"] = "<p>edited</p>"
    cf.pages["p1"]["version"]["when"] = "2026-01-02T00:00:00.000Z"
    cf.queue[f"{API}/p1/restriction/byOperation/read"] = [(500, {})]
    c = crawl.crawl_confluence(cf.sid)
    assert c["failed"] == 1 and live(cf.sid) == {}  # fail closed: the old copy is withdrawn too
    last = src(cf.sid)[1]
    assert last["failed_keys"] == [{"key": "page:p1", "error": "ConfluenceError"}] and last["retry_ids"] == ["p1"]
    cf.queue.clear()
    cf.pages["p1"]["version"]["when"] = "2025-01-01T00:00:00.000Z"  # CQL (cursor + slack) will not offer it
    crawl.crawl_confluence(cf.sid)
    assert "page:p1" in live(cf.sid) and src(cf.sid)[1]["retry_ids"] == []


def test_concurrent_crawl_reports_running(cf):
    with db.connect(autocommit=True) as other:
        other.execute("select pg_advisory_lock(2, %s)", (cf.sid,))
        assert crawl.crawl_confluence(cf.sid) == {"status": "running"}


def test_worker_and_sources_know_confluence():
    from app import sources
    assert "crawl_confluence" in worker.HANDLERS
    assert sources.JOB["confluence"] == "crawl_confluence" and sources.REQUIRED["confluence"] == ("base_url", "spaces")


def test_page_under_folder_fails_closed(cf):
    """Cloud folders report no restrictions through the API, so a folder ancestor is never trusted."""
    cf.page("p1", ancestors=[("f9", "folder")])
    c = crawl.crawl_confluence(cf.sid)
    assert c["skipped_restricted"] == 1 and "page:p1" not in live(cf.sid)


def test_attachment_moved_to_another_page_is_withdrawn(cf):
    seed(cf)
    crawl.crawl_confluence(cf.sid)
    assert "att:p1:a1" in live(cf.sid)
    moved = cf.atts.pop("p1")
    moved[0]["container"] = {"id": "p9"}  # moved to a (restricted) page
    cf.atts["p9"] = moved
    cf.page("p9", when="2025-01-01T00:00:00.000Z")
    cf.restricted.add("p9")
    crawl.crawl_confluence(cf.sid)
    assert "att:p1:a1" not in live(cf.sid)


def test_new_attachment_on_unchanged_page_is_found(cf):
    seed(cf)
    crawl.crawl_confluence(cf.sid)
    cf.attach("p3", "a7", "late.pdf", b"late", when="2026-06-01T00:00:00.000Z")  # p3 itself unchanged
    crawl.crawl_confluence(cf.sid)
    assert "att:p3:a7" in live(cf.sid)
