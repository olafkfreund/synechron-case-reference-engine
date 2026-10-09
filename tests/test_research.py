import ipaddress
import json
import multiprocessing
import socket
import threading
import time
import uuid
from base64 import b64encode

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from itsdangerous import TimestampSigner

from app import anonymise as an, db, ingest, main, research as rs, worker
from tests.test_auth import ORIGIN, SECRET, client, env  # noqa: F401

KEY = "brave-s3cr3t-key"
REG = [dict(name="Zorp", aliases=[], anonymised_label="a retailer", referenceable=False),
       dict(name="Globex", aliases=[], anonymised_label="a maker", referenceable=True)]
U = ["g-user"]


# --- the query --------------------------------------------------------------------------------------

def test_query_strips_registry_names_even_when_the_llm_keeps_them(monkeypatch):
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Query(query="Zörp and Globex cloud migration kubernetes"))
    q, note = rs.build_query("how did Zorp do it", REG)
    assert q == "and cloud migration kubernetes" and note is None


def test_query_falls_back_to_scrubbed_question_and_says_so(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(rs, "complete_json", boom)
    q, note = rs.build_query("How does Zorp do KYC?" + " x" * 300, REG)
    assert "zorp" not in q and len(q) <= 200 and "unavailable" in note


def test_query_refused_when_a_name_would_remain_or_nothing_is_left(monkeypatch):
    monkeypatch.setattr(rs.anonymise, "blocked", lambda *a: ["Zorp"])
    with pytest.raises(ValueError):
        rs.finalize("cloud migration", REG)
    monkeypatch.undo()
    with pytest.raises(ValueError):
        rs.finalize("Zorp", REG)


# --- the pages --------------------------------------------------------------------------------------

@pytest.fixture
def reg(env, monkeypatch):  # noqa: F811
    db.init()
    name = f"Zorp{uuid.uuid4().hex[:6]}"
    with db.connect() as c:
        cid = c.execute("insert into clients(name, anonymised_label) values (%s,'a retailer') returning id", (name,)).fetchone()[0]
    monkeypatch.setattr(rs, "complete_json", lambda *a, **k: rs.Query(query=f"{name} cloud kyc"))
    yield name
    with db.connect() as c:
        c.execute("delete from research where created_by like 'u%%'")
        c.execute("delete from jobs where kind='research'")
        c.execute("delete from clients where id=%s", (cid,))


def as_user(sub, groups=U):
    c = TestClient(main.create_app(), headers={"Origin": ORIGIN})
    data = b64encode(json.dumps({"user": {"sub": sub, "name": sub, "groups": groups}}).encode())
    c.cookies.set("session", TimestampSigner(SECRET).sign(data).decode())
    return c


def test_preview_send_and_tampered_field(reg):
    c = as_user("u1")
    p = c.post("/research", data={"question": f"how did {reg} do it"})
    assert p.status_code == 200 and "cloud kyc" in p.text and reg.lower() not in p.text.lower()
    r = c.post("/research/send", data={"query": f"{reg} secret deal terms"}, follow_redirects=False)  # tampered
    assert r.status_code == 303
    with db.connect() as conn:
        q, by, status = conn.execute("select query, created_by, status from research order by id desc limit 1").fetchone()
        assert (q, by, status) == ("secret deal terms", "u1", "queued")
        assert conn.execute("select count(*) from jobs where kind='research' and status='queued'").fetchone()[0] >= 1
    assert "Running" in c.get(r.headers["location"]).text
    assert c.post("/research/send", data={"query": reg}).status_code == 400  # nothing left after scrub


def test_only_the_creator_sees_a_row(reg):
    c = as_user("u1")
    loc = c.post("/research/send", data={"query": "cloud kyc"}, follow_redirects=False).headers["location"]
    assert c.get(loc).status_code == 200
    assert as_user("u2").get(loc).status_code == 404
    assert client().get(loc).status_code == 401


# --- the fetcher ------------------------------------------------------------------------------------

class Web:
    """Fake DNS plus a fake internet: handler routes on the Host header, since we connect to the IP."""

    def __init__(self, monkeypatch):
        self.dns, self.real = {}, socket.getaddrinfo
        self.db_host = conninfo_to_dict(db.url()).get("host")  # read once: with DB_SECRET_ARN, url() calls AWS
        self.pages = {}   # (host, path) -> (status, headers, body)
        self.seen = []    # (host, path)
        self.brave_calls = []
        self.brave_replies = []  # popped per call: an exception to raise or an httpx.Response
        monkeypatch.setattr(rs.socket, "getaddrinfo", self.getaddrinfo)
        monkeypatch.setattr(rs, "TRANSPORT", httpx.MockTransport(self))
        self.sleeps = []
        monkeypatch.setattr(rs.time, "sleep", self.sleeps.append)

    def getaddrinfo(self, host, port, *args, **kw):
        if host == self.db_host:  # psycopg resolves the database through the same function
            return self.real(host, port, *args, **kw)
        ip = self.dns.get(host, host if host.replace(".", "").isdigit() or ":" in host else "93.184.216.34")
        if host.isdigit():  # "2130706433": the resolver turns it into 127.0.0.1
            ip = str(ipaddress.IPv4Address(int(host)))
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def __call__(self, request):
        if request.url.host == "api.search.brave.com":
            assert request.headers["x-subscription-token"] == KEY
            self.brave_calls.append(dict(request.url.params))
            if self.brave_replies:
                reply = self.brave_replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            return httpx.Response(200, json={"web": {"results": [{"url": u, "title": "T"} for u in self.results]}})
        host = request.headers["host"].split(":")[0]
        self.seen.append((host, request.url.path))
        status, headers, body = self.pages.get((host, request.url.path), (404, {}, b""))
        return httpx.Response(status, headers=headers, content=body)

    def html(self, host, path, text="<p>hello</p>"):
        self.pages[(host, path)] = (200, {"content-type": "text/html; charset=utf-8"}, text.encode())


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", KEY)
    monkeypatch.setattr(ingest, "to_markdown", lambda data, name, **kw: data.decode())
    monkeypatch.setattr(ingest, "warm", lambda: None)
    return Web(monkeypatch)


@pytest.mark.parametrize("host,ip", [("a.example", "127.0.0.1"), ("b.example", "10.1.2.3"), ("meta.example", "169.254.169.254"),
                                     ("ecs.example", "169.254.170.2"), ("v6.example", "::1"), ("ula.example", "fd00::1"),
                                     ("cgnat.example", "100.64.0.1"), ("mc.example", "224.0.0.1"), ("mapped.example", "::ffff:10.0.0.1")])
def test_private_addresses_refused(web, host, ip):
    web.dns[host] = ip
    web.html(host, "/robots.txt", "")
    with pytest.raises(rs.Blocked):
        rs.Fetcher().fetch(f"https://{host}/page")
    assert web.seen == []  # nothing was sent


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
                                 "http://2130706433/", "file:///etc/passwd", "ftp://a.example/x", "gopher://a.example/",
                                 "https://user:pw@a.example/", "http://a.example:8080/"])
def test_literal_ips_schemes_userinfo_and_ports_refused(web, url):
    with pytest.raises(rs.Blocked):
        rs.Fetcher().fetch(url)
    assert web.seen == []


def test_redirect_to_a_private_host_is_refused_on_the_hop(web):
    web.dns["inner.example"] = "10.9.9.9"
    web.html("ok.example", "/robots.txt", "")
    web.pages[("ok.example", "/go")] = (302, {"location": "http://inner.example/admin"}, b"")
    with pytest.raises(rs.Blocked):
        rs.Fetcher().fetch("https://ok.example/go")
    assert ("inner.example", "/admin") not in web.seen and ("ok.example", "/go") in web.seen


def test_redirect_limit_and_normal_redirect(web):
    web.html("ok.example", "/robots.txt", "")
    for i in range(6):
        web.pages[("ok.example", f"/r{i}")] = (302, {"location": f"/r{i + 1}"}, b"")
    with pytest.raises(rs.Blocked, match="too many"):
        rs.Fetcher().fetch("https://ok.example/r0")
    web.pages[("ok.example", "/a")] = (301, {"location": "/b"}, b"")
    web.html("ok.example", "/b", "final")
    body, ctype, final = rs.Fetcher().fetch("https://ok.example/a")
    assert body == b"final" and final.endswith("/b")


def test_size_content_type_and_robots(web, monkeypatch):
    web.pages[("ok.example", "/robots.txt")] = (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private\n")
    web.html("ok.example", "/big", "x" * 100)
    web.pages[("ok.example", "/img")] = (200, {"content-type": "image/png"}, b"\x89PNG")
    web.html("ok.example", "/private/doc")
    monkeypatch.setattr(rs, "MAX_BYTES", 50)
    f = rs.Fetcher()
    with pytest.raises(rs.TooLarge):
        f.fetch("https://ok.example/big")
    with pytest.raises(rs.BadContentType):
        f.fetch("https://ok.example/img")
    with pytest.raises(rs.RobotsDisallowed):
        f.fetch("https://ok.example/private/doc")
    assert ("ok.example", "/private/doc") not in web.seen


def test_robots_unreachable_means_disallowed_and_missing_means_allowed(web):
    web.pages[("down.example", "/robots.txt")] = (503, {}, b"")
    web.html("down.example", "/p")
    with pytest.raises(rs.RobotsDisallowed):
        rs.Fetcher().fetch("https://down.example/p")
    web.html("none.example", "/p")  # robots.txt -> 404
    assert rs.Fetcher().fetch("https://none.example/p")[0] == b"<p>hello</p>"


def test_one_request_per_domain_per_second(web):
    web.html("ok.example", "/robots.txt", "")
    web.html("ok.example", "/a")
    web.html("ok.example", "/b")
    f = rs.Fetcher()
    f.fetch("https://ok.example/a")
    f.fetch("https://ok.example/b")
    assert web.sleeps and all(0 < s <= 1 for s in web.sleeps)


# --- the job ----------------------------------------------------------------------------------------

def make_row(query, created_by="u1", status="queued"):
    db.init()
    with db.connect() as c:
        return c.execute("insert into research(query, created_by, status) values (%s,%s,%s) returning id",
                         (query, created_by, status)).fetchone()[0]


def row(rid):
    with db.connect() as c:
        return c.execute("select status, error, results from research where id=%s", (rid,)).fetchone()


@pytest.fixture(autouse=False)
def cleanup():
    yield
    with db.connect() as c:
        c.execute("delete from research where query like 'research test %%'")


def test_run_stores_pages_skips_bad_ones_and_never_stores_the_key(web, cleanup):
    q = f"research test {uuid.uuid4().hex}"
    web.results = ["https://docs.example/guide", "https://blog.example/post", "http://127.0.0.1/x", "https://docs.example/guide"]
    web.pages[("docs.example", "/robots.txt")] = (200, {"content-type": "text/plain"}, b"")
    web.html("docs.example", "/guide", "<h1>Guide</h1>")
    web.html("blog.example", "/robots.txt", "User-agent: *\nDisallow: /\n")
    web.html("blog.example", "/post")
    rid = make_row(q)
    rs.run(rid)
    status, error, results = row(rid)
    assert status == "done" and error is None
    assert [(p["publisher"], p["url"], p["markdown"]) for p in results["pages"]] == [("docs.example", "https://docs.example/guide", "<h1>Guide</h1>")]
    assert results["pages"][0]["retrieved_at"]
    assert results["skipped"] == [{"domain": "blog.example", "error": "RobotsDisallowed"}, {"domain": "127.0.0.1", "error": "Blocked"}]
    assert web.brave_calls == [{"q": q, "count": "10"}]
    assert KEY not in json.dumps(results) and KEY not in json.dumps(error)


def test_run_failure_is_recorded_without_the_key(web, cleanup, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY")
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    assert row(rid)[:2] == ("failed", "BRAVE_API_KEY is not set")


def test_cache_hit_within_30_days_skips_brave(web, cleanup):
    q = f"research test {uuid.uuid4().hex}"
    web.results = ["https://docs.example/guide"]
    web.html("docs.example", "/robots.txt", "")
    web.html("docs.example", "/guide")
    first = make_row(q)
    rs.run(first)
    with db.connect() as c:  # only the original fetch is old
        c.execute("update research set retrieved_at = now() - interval '29 days' where id=%s", (first,))
    second = make_row(q)
    rs.run(second)
    assert len(web.brave_calls) == 1 and row(second)[0] == "done" and row(second)[2] == row(first)[2]
    with db.connect() as c:  # the copy keeps the fetch time, so the window does not restart
        at = dict(c.execute("select id, retrieved_at from research where id in (%s,%s)", (first, second)).fetchall())
        assert at[second] == at[first]
        c.execute("update research set retrieved_at = retrieved_at - interval '2 days' where id in (%s,%s)", (first, second))
    third = make_row(q)
    rs.run(third)
    assert len(web.brave_calls) == 2


def test_worker_dispatches_research():
    assert "research" in worker.HANDLERS


def test_identifiers_stripped_from_query():
    q = rs.finalize("how did we run KYC? ask jane.doe@acme-bank.co.uk, see https://intranet.acme.local/bid/42 "
                    "or www.acme.com, call +44 20 7946 0958, deal 1234567", [])
    for bad in ("jane", "acme", "intranet", "7946", "1234567"):
        assert bad not in q, bad
    assert "kyc" in q


def test_nat64_metadata_address_refused(web):
    web.dns["n64.example"] = "64:ff9b::a9fe:a9fe"  # 169.254.169.254 via DNS64
    with pytest.raises(rs.Blocked):
        rs.Fetcher().fetch("https://n64.example/")


def test_robots_checked_on_redirect_to_another_host(web):
    web.pages[("a.example", "/go")] = (302, {"location": "https://b.example/private"}, b"")
    web.pages[("b.example", "/robots.txt")] = (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private\n")
    web.html("b.example", "/private")
    with pytest.raises(rs.RobotsDisallowed):
        rs.Fetcher().fetch("https://a.example/go")
    assert ("b.example", "/private") not in web.seen


def test_clients_ignore_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    assert rs.Fetcher().http._trust_env is False


def test_slow_conversion_is_killed_and_the_page_skipped(web, cleanup, monkeypatch):
    held = threading.Lock()  # held by a parent thread across the fork: the child can never take it

    def stub(data, name, **kw):
        if name.startswith("slow"):
            held.acquire()
        return data.decode()

    monkeypatch.setattr(ingest, "to_markdown", stub)
    monkeypatch.setattr(rs, "PAGE_CONVERT_TIMEOUT", 2)
    web.results = ["https://slow.example/slow", "https://fast.example/fast"]
    for h, p in (("slow.example", "/slow"), ("fast.example", "/fast")):
        web.html(h, "/robots.txt", "")
        web.html(h, p, "<p>ok</p>")
    rid = make_row(f"research test {uuid.uuid4().hex}")
    held.acquire()  # the test thread itself now holds it, so the forked stub blocks forever
    t = time.monotonic()
    try:
        rs.run(rid)
    finally:
        held.release()
    assert time.monotonic() - t < 10
    status, _, results = row(rid)
    assert status == "done"
    assert results["skipped"] == [{"domain": "slow.example", "error": "ConvertTimeout"}]
    assert [p["publisher"] for p in results["pages"]] == ["fast.example"]
    assert multiprocessing.active_children() == []


def test_conversion_error_and_large_output(web, monkeypatch):
    def boom(data, name, **kw):
        raise ValueError("x")

    monkeypatch.setattr(ingest, "to_markdown", boom)
    with pytest.raises(rs.ConvertFailed):
        rs.convert(b"a", "a.html")
    monkeypatch.setattr(ingest, "to_markdown", lambda data, name, **kw: "😀" * 2_000_000)  # over the pipe buffer: join-before-recv would deadlock
    assert rs.convert(b"a", "a.html") == "😀" * rs.MAX_MARKDOWN


def run_search(web, replies):
    web.html("docs.example", "/robots.txt", "")
    web.html("docs.example", "/guide")
    web.results = ["https://docs.example/guide"]
    web.brave_replies = replies
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    return row(rid)


def test_brave_429_retries_after_the_header_wait(web, cleanup):
    assert run_search(web, [httpx.Response(429, headers={"Retry-After": "3"})])[0] == "done"
    assert len(web.brave_calls) == 2 and 3 in web.sleeps


def test_brave_5xx_gives_up_after_three_calls(web, cleanup):
    status, error, _ = run_search(web, [httpx.Response(503)] * 3)
    assert (status, error) == ("failed", "search failed (503)") and len(web.brave_calls) == 3


def test_brave_other_4xx_is_not_retried(web, cleanup):
    assert run_search(web, [httpx.Response(401)])[0] == "failed" and len(web.brave_calls) == 1


def test_brave_transport_error_is_retried(web, cleanup):
    assert run_search(web, [httpx.ConnectError("x")])[0] == "done" and len(web.brave_calls) == 2


def test_brave_retry_after_is_capped(web, cleanup):
    run_search(web, [httpx.Response(429, headers={"Retry-After": "999"})])
    assert 10 in web.sleeps and 999 not in web.sleeps
