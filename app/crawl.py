import os
import time
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import urlencode

import boto3
import httpx
from psycopg.types.json import Jsonb

from app import db
from app.ingest import ingest

MAX_FAILED_KEYS = 20
# multipart uploads (DataSync) get LastModified = upload *start*, so they can land behind the cursor
CURSOR_SLACK = timedelta(days=1)


def crawl_s3(source_id: int) -> dict:
    """List config.bucket/config.prefix and ingest new or changed objects.

    Fetched: keys not yet known, plus keys modified since cursor - CURSOR_SLACK (repeats are a
    cheap checksum skip). One failing object is counted and skipped; the crawl and cursor go on.
    """
    # autocommit: the session lock must not keep a transaction open for an hours-long crawl
    with db.connect(autocommit=True) as lock:
        # one crawl per source: the schedule and a manual trigger must not race (released on close)
        if not lock.execute("select pg_try_advisory_lock(2, %s)", (source_id,)).fetchone()[0]:
            return {"status": "running"}
        config, acl, cursor = lock.execute(
            "select config, acl_groups, cursor from sources where id=%s", (source_id,)).fetchone()
        known = {r[0] for r in lock.execute(
            "select external_id from documents where source_id=%s", (source_id,))}
        since = datetime.fromisoformat(cursor) - CURSOR_SLACK if cursor else None
        s3 = boto3.client("s3")
        seen, newest = [], cursor
        counts = {"new": 0, "updated": 0, "skipped": 0, "deleted": 0, "failed": 0}
        failed = []
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=config["bucket"], Prefix=config.get("prefix", "")):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                seen.append(key)
                modified = obj["LastModified"]
                newest = max(newest or modified.isoformat(), modified.isoformat())
                if key in known and since and modified < since:
                    continue
                try:
                    body = s3.get_object(Bucket=config["bucket"], Key=key)["Body"].read()
                    counts[ingest(source_id, key, key.rsplit("/", 1)[-1], body, acl)] += 1
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the crawl
                    counts["failed"] += 1
                    if len(failed) < MAX_FAILED_KEYS:
                        failed.append({"key": key, "error": type(e).__name__})  # no message: may quote the file

        with db.connect() as conn:
            if seen or not known:
                counts["deleted"] = conn.execute(
                    "select count(*) from documents where source_id=%s and deleted_at is null "
                    "and not (external_id = any(%s))", (source_id, seen)).fetchone()[0]
                # one pass over the full listing: source ACL applies to every document, and a
                # reappeared key is live again even if it was not re-downloaded
                conn.execute(
                    "update documents set acl_groups=%s, deleted_at = case when external_id = any(%s) "
                    "then null else coalesce(deleted_at, now()) end where source_id=%s",
                    (acl, seen, source_id))
            else:
                # an empty listing with live documents is a prefix or permission mistake, not a mass delete
                counts["empty_listing"] = True
            conn.execute("update sources set cursor=%s, last_run_at=now(), last_counts=%s where id=%s",
                         (newest, Jsonb({**counts, "failed_keys": failed}), source_id))
    return counts


GRAPH = "https://graph.microsoft.com/v1.0"
TRANSPORT = None  # tests inject httpx.MockTransport
MAX_RETRY_AFTER = 60  # seconds
RETRIES = 3


class GraphError(RuntimeError):
    """Never carries Graph's error text: it contains site paths and file names."""

    def __init__(self, what, status):
        super().__init__(f"{what} ({status})")
        self.status = status


class Graph:
    """App-only Microsoft Graph client (client credentials). The token is never logged or stored."""

    def __init__(self, tenant):
        self.tenant = tenant
        self.http = httpx.Client(transport=TRANSPORT, timeout=60, follow_redirects=True)
        self.token = None

    def login(self):
        cid, secret = os.environ.get("GRAPH_CLIENT_ID"), os.environ.get("GRAPH_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError("GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET are required")
        r = self.http.post(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default"})
        if r.status_code != 200:
            raise GraphError("Graph login failed", r.status_code)
        self.token = r.json()["access_token"]

    def get(self, url):
        """GET with Retry-After on 429/503 (capped) and one re-login on 401."""
        relogged = False
        for attempt in range(RETRIES + 1):
            if not self.token:
                self.login()
            r = self.http.get(url, headers={"Authorization": f"Bearer {self.token}"})
            if r.status_code == 401 and not relogged:
                self.token, relogged = None, True  # token expired mid-crawl
                continue
            if r.status_code in (429, 503):
                if attempt == RETRIES:
                    raise GraphError("Graph throttled", r.status_code)
                retry = r.headers.get("Retry-After", "")
                time.sleep(min(int(retry) if retry.isdigit() else 5, MAX_RETRY_AFTER))
                continue
            if r.status_code >= 400:
                raise GraphError("Graph request failed", r.status_code)
            return r
        raise GraphError("Graph request failed", 401)

    def pages(self, url):
        while url:
            body = self.get(url).json()
            yield body
            url = body.get("@odata.nextLink")


def _signature(perms: list[dict]) -> frozenset:
    """Who can do what, ignoring where it was inherited from: (grantee or link scope, roles)."""
    sig = set()
    for p in perms:
        roles = tuple(sorted(p.get("roles", [])))
        if "link" in p:  # a sharing link only widens access: never equal to the root's grants
            sig.add(("link", p["link"].get("scope"), p.get("id"), roles))
            continue
        who = p.get("grantedToV2") or p.get("grantedTo") or {}
        ids = [v.get("id") or v.get("loginName") for v in who.values() if isinstance(v, dict)]
        ids += [i.get("user", {}).get("id") or i.get("group", {}).get("id")
                for i in p.get("grantedToIdentitiesV2", p.get("grantedToIdentities", []))]
        sig.add((tuple(sorted(filter(None, ids))) or ("unknown", p.get("id")), roles))
    return frozenset(sig)


def crawl_sharepoint(source_id: int) -> dict:
    """Crawl one drive with Graph delta; the stored deltaLink is the cursor.

    ACL is source-level and fails closed. A file is ingested only when its permissions are exactly
    the drive root's (the access the admin mapped to sources.acl_groups); a file under a restricted
    folder, with its own grants or with a sharing link is skipped, and withdrawn if ingested before.
    Delta does not report permission changes on descendants, so every crawl (daily) also re-checks
    the permissions of every live document. Failed items are retried by id on the next run.
    Needs the app's Sites.Selected grant with the fullcontrol role: a read-only caller sees only
    the sharing permissions that apply to itself, and the comparison would be meaningless.
    """
    with db.connect(autocommit=True) as lock:
        if not lock.execute("select pg_try_advisory_lock(2, %s)", (source_id,)).fetchone()[0]:
            return {"status": "running"}
        config, acl, cursor, last = lock.execute(
            "select config, acl_groups, cursor, last_counts from sources where id=%s", (source_id,)).fetchone()
        drive, exts = config["drive_id"], {e.lower().lstrip(".") for e in config.get("include_ext", ["docx", "pptx", "pdf"])}
        cap = int(os.environ.get("SHAREPOINT_MAX_BYTES", 50 * 1024 * 1024))
        g = Graph(config["tenant_id"])
        counts = {"new": 0, "updated": 0, "skipped": 0, "deleted": 0, "failed": 0, "skipped_unique_permissions": 0,
                  "skipped_type": 0, "skipped_too_large": 0, "withdrawn_on_recheck": 0}
        failed, retry, seen, checked = [], [], set(), set()

        def perms_of(path):
            return [p for page in g.pages(f"{GRAPH}/drives/{drive}/{path}/permissions") for p in page.get("value", [])]

        root_sig = _signature(perms_of("root"))
        if not root_sig:
            raise GraphError("drive root permissions are not visible; grant Sites.Selected with fullcontrol", 403)

        def withdraw(iid):
            return lock.execute("update documents set deleted_at=coalesce(deleted_at, now()) "
                                "where source_id=%s and external_id=%s and deleted_at is null", (source_id, iid)).rowcount

        def same_access(iid):
            checked.add(iid)
            return _signature(perms_of(f"items/{iid}")) == root_sig

        def handle(item):
            iid = item["id"]
            if "deleted" in item:
                counts["deleted"] += withdraw(iid)
                return
            if "file" not in item:  # folders
                return
            seen.add(iid)
            name = item.get("name", "")
            if PurePosixPath(name).suffix.lower().lstrip(".") not in exts:
                counts["skipped_type"] += 1
                return
            if item.get("size", 0) > cap:
                counts["skipped_too_large"] += 1
                return
            try:
                if not same_access(iid):
                    counts["skipped_unique_permissions"] += 1
                    withdraw(iid)
                    return
                data = g.get(f"{GRAPH}/drives/{drive}/items/{iid}/content").content
                if len(data) > cap:
                    counts["skipped_too_large"] += 1
                    return
                counts[ingest(source_id, iid, name, data, acl)] += 1
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the crawl
                withdraw(iid)  # fail closed: an unverified file must not stay searchable on its old ACL
                counts["failed"] += 1
                retry.append(iid)
                if len(failed) < MAX_FAILED_KEYS:
                    failed.append({"key": iid, "error": type(e).__name__})  # id and type only: messages hold paths

        root = f"{GRAPH}/drives/{drive}/root/delta"
        full = not cursor
        try:
            first = g.get(cursor or root)
        except GraphError as e:
            if e.status != 410 or not cursor:
                raise
            full, first = True, g.get(root)  # delta token expired: resync everything
        body, delta_link = first.json(), None
        while True:
            for item in body.get("value", []):
                handle(item)
            if nxt := body.get("@odata.nextLink"):
                body = g.get(nxt).json()
                continue
            delta_link = body["@odata.deltaLink"]  # missing: the enumeration did not finish, raise, keep the cursor
            break

        # last run's failures: delta will not offer them again until they change
        for iid in (last or {}).get("retry_ids", []):
            if iid in seen:
                continue
            try:
                handle(g.get(f"{GRAPH}/drives/{drive}/items/{iid}").json())
            except GraphError as e:
                if e.status == 404:
                    counts["deleted"] += withdraw(iid)
                else:
                    retry.append(iid)

        if full and seen:  # a full listing tells us what disappeared while we had no cursor
            counts["deleted"] += lock.execute(
                "update documents set deleted_at=now() where source_id=%s and deleted_at is null "
                "and not (external_id = any(%s))", (source_id, list(seen))).rowcount

        # permission changes on descendants are not in delta: re-check every live document (daily crawl)
        live = [r[0] for r in lock.execute(
            "select external_id from documents where source_id=%s and deleted_at is null", (source_id,))]
        for iid in live:
            if iid in checked:
                continue
            try:
                ok = same_access(iid)
            except Exception:  # noqa: BLE001 - unverifiable (404, throttled, gone): fail closed
                ok = False
            if not ok:
                counts["withdrawn_on_recheck"] += withdraw(iid)

        lock.execute("update documents set acl_groups=%s where source_id=%s and acl_groups is distinct from %s",
                     (acl, source_id, acl))  # admin ACL changes apply to everything already ingested
        lock.execute("update sources set cursor=%s, last_run_at=now(), last_counts=%s where id=%s",
                     (delta_link, Jsonb({**counts, "failed_keys": failed, "retry_ids": retry}), source_id))
    return counts


class ConfluenceError(RuntimeError):
    """Never carries Confluence's error text: it contains space and page names."""

    def __init__(self, what, status):
        super().__init__(f"{what} ({status})")
        self.status = status


class Confluence:
    """Confluence REST client: basic auth (Cloud: email + API token) or bearer (Data Center PAT)."""

    def __init__(self, base_url, prefix="/wiki"):
        self.root = base_url.rstrip("/")
        self.base = self.root + prefix  # Cloud serves everything under /wiki
        self.cloud = prefix == "/wiki"  # Data Center has no v2 API and no folders
        self.api = self.base + "/rest/api"
        email, token = os.environ.get("CONFLUENCE_EMAIL"), os.environ.get("CONFLUENCE_TOKEN")
        if not token:
            raise RuntimeError("CONFLUENCE_TOKEN is required (with CONFLUENCE_EMAIL for Cloud)")
        self.auth = httpx.BasicAuth(email, token) if email else None
        self.headers = {} if email else {"Authorization": f"Bearer {token}"}
        self.http = httpx.Client(transport=TRANSPORT, timeout=60, follow_redirects=True)

    def get(self, url):
        for attempt in range(RETRIES + 1):
            r = self.http.get(url, headers=self.headers, auth=self.auth)
            if r.status_code in (429, 503):
                if attempt == RETRIES:
                    raise ConfluenceError("Confluence throttled", r.status_code)
                retry = r.headers.get("Retry-After", "")
                time.sleep(min(int(retry) if retry.isdigit() else 5, MAX_RETRY_AFTER))
                continue
            if r.status_code >= 400:
                raise ConfluenceError("Confluence request failed", r.status_code)
            return r

    def paged(self, url):
        """Yield (results, links_base) per page, following _links.next."""
        while url:
            body = self.get(url).json()
            links = body.get("_links", {})
            yield body.get("results", []), links.get("base") or self.base
            url = (links.get("base") or self.base) + links["next"] if links.get("next") else None


def crawl_confluence(source_id: int) -> dict:
    """Crawl Confluence spaces by CQL lastmodified; cursor = newest version.when seen.

    ACL is source-level and fails closed, like SharePoint: a page is ingested only if neither it nor
    any ancestor has a read restriction (view restrictions inherit down the tree); its attachments
    follow the page. A restricted page is skipped and withdrawn if ingested before. CQL never reports
    deletions or restriction changes, so every crawl re-checks every live document and withdraws any
    that is gone, trashed, restricted or cannot be verified. A failure withdraws the item and is retried
    by page id on the next run. external_id: `page:{id}` and `att:{page_id}:{attachment_id}`: the
    parent page id lives in the key (no schema change), which is how the re-check finds it.
    """
    with db.connect(autocommit=True) as lock:
        if not lock.execute("select pg_try_advisory_lock(2, %s)", (source_id,)).fetchone()[0]:
            return {"status": "running"}
        config, acl, cursor, last = lock.execute(
            "select config, acl_groups, cursor, last_counts from sources where id=%s", (source_id,)).fetchone()
        spaces = config["spaces"]
        if not isinstance(spaces, list) or not spaces:
            raise ValueError("config.spaces must be a non-empty list of space keys")
        c = Confluence(config["base_url"], config.get("api_prefix", "/wiki"))
        cap = int(os.environ.get("CONFLUENCE_MAX_BYTES", 50 * 1024 * 1024))
        exts = {"docx", "pptx", "pdf"}
        counts = {"new": 0, "updated": 0, "skipped": 0, "failed": 0, "skipped_restricted": 0, "skipped_type": 0,
                  "skipped_too_large": 0, "withdrawn_on_recheck": 0}
        failed, retry, done, cache, visited = [], [], set(), {}, set()

        def restricted(cid):  # one lookup per content id per crawl
            if cid not in cache:
                r = c.get(f"{c.api}/content/{cid}/restriction/byOperation/read").json()["restrictions"]
                cache[cid] = any(r[k].get("results") or r[k].get("size") for k in ("user", "group"))
            return cache[cid]

        def allowed(pid, page):
            """Neither the page nor any ancestor restricts reading. Cloud: v2 ancestors (complete, paged,
            typed); a non-page ancestor (a folder) fails closed, because the API reports no restrictions
            for folders (CONFCLOUD-82920). Data Center: v1 expand=ancestors (pages only)."""
            if c.cloud:
                ids, url = [], f"{c.base}/api/v2/pages/{pid}/ancestors?limit=250"
                while url:
                    body = c.get(url).json()
                    for a in body.get("results", []):
                        if a.get("type") != "page":
                            return False
                        ids.append(a["id"])
                    nxt = body.get("_links", {}).get("next")
                    url = c.root + nxt if nxt else None
            else:
                ids = [a["id"] for a in page.get("ancestors", [])]
            return not any(restricted(i) for i in [*ids, pid])

        def withdraw(ext, prefix=False):
            return lock.execute(
                "update documents set deleted_at=coalesce(deleted_at, now()) where source_id=%s and deleted_at is null "
                f"and {'starts_with(external_id, %s)' if prefix else 'external_id = %s'}", (source_id, ext)).rowcount

        def do_page(page):
            pid = page["id"]
            visited.add(pid)  # once per run, whatever the outcome (its attachments may be hits too)
            try:
                if not allowed(pid, page):
                    counts["skipped_restricted"] += 1
                    withdraw(f"page:{pid}")
                    withdraw(f"att:{pid}:", prefix=True)
                    return
                html = f"<html><body>{page['body']['storage']['value']}</body></html>".encode()
                counts[ingest(source_id, f"page:{pid}", f"{page['title']}.html", html, acl)] += 1
                done.add(f"page:{pid}")
                for results, base in c.paged(f"{c.api}/content/{pid}/child/attachment?" + urlencode({"expand": "version", "limit": 50})):
                    for att in results:
                        ext = f"att:{pid}:{att['id']}"
                        title = att.get("title", "")
                        if PurePosixPath(title).suffix.lower().lstrip(".") not in exts:
                            counts["skipped_type"] += 1
                            continue
                        if att.get("extensions", {}).get("fileSize", 0) > cap:
                            counts["skipped_too_large"] += 1
                            continue
                        try:
                            data = c.get(base + att["_links"]["download"]).content
                            if len(data) > cap:
                                counts["skipped_too_large"] += 1
                                continue
                            counts[ingest(source_id, ext, title, data, acl)] += 1
                            done.add(ext)
                        except Exception as e:  # noqa: BLE001
                            fail(pid, ext, e, page_ok=True)
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the crawl
                fail(pid, f"page:{pid}", e)

        def fail(pid, ext, e, page_ok=False):
            withdraw(ext)  # fail closed: an unverified item must not stay searchable
            if not page_ok:
                withdraw(f"att:{pid}:", prefix=True)
            counts["failed"] += 1
            if pid not in retry:
                retry.append(pid)
            if len(failed) < MAX_FAILED_KEYS:
                failed.append({"key": ext, "error": type(e).__name__})  # id and type only: messages hold titles

        keys = ", ".join('"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"' for k in spaces)
        # attachments too: a new attachment does not change its page's lastmodified
        cql = f"space in ({keys}) and type in (page, attachment)"
        if cursor:  # slack: CQL compares in the caller's timezone; repeats are a cheap checksum skip
            cql += f' and lastmodified > "{datetime.fromisoformat(cursor) - CURSOR_SLACK:%Y/%m/%d %H:%M}"'
        newest = datetime.fromisoformat(cursor) if cursor else None
        search = f"{c.api}/content/search?" + urlencode(
            {"cql": cql, "expand": "body.storage,version,ancestors,container", "limit": 25})
        for results, _ in c.paged(search):  # an error here raises: the cursor stays
            for hit in results:
                if hit.get("type") == "attachment":  # crawl its page (and so all its attachments) once
                    pid = hit.get("container", {}).get("id")
                    if pid and pid not in visited:
                        try:
                            do_page(c.get(f"{c.api}/content/{pid}?expand=body.storage,version,ancestors").json())
                        except ConfluenceError as e:
                            fail(pid, f"page:{pid}", e)
                else:
                    do_page(hit)
                when = datetime.fromisoformat(hit["version"]["when"])
                newest = max(newest or when, when)

        for pid in (last or {}).get("retry_ids", []):  # last run's failures: CQL will not offer them again
            if f"page:{pid}" in done:
                continue
            try:
                do_page(c.get(f"{c.api}/content/{pid}?expand=body.storage,version,ancestors").json())
            except ConfluenceError as e:
                if e.status == 404:
                    withdraw(f"page:{pid}")
                    withdraw(f"att:{pid}:", prefix=True)
                else:
                    retry.append(pid)

        live = [r[0] for r in lock.execute(
            "select external_id from documents where source_id=%s and deleted_at is null", (source_id,))]
        for ext in live:
            if ext in done:
                continue
            _, pid, *att = ext.split(":")
            try:
                page = c.get(f"{c.api}/content/{pid}?expand=ancestors").json()
                ok = page.get("status") == "current" and allowed(pid, page)
                if ok and att:  # still current AND still on the same (allowed) page: it may have been moved
                    a = c.get(f"{c.api}/content/{att[0]}?expand=container").json()
                    ok = a.get("status") == "current" and str(a.get("container", {}).get("id")) == pid
            except Exception:  # noqa: BLE001 - 404, throttled, unreadable: fail closed
                ok = False
            if not ok:
                counts["withdrawn_on_recheck"] += withdraw(ext)

        lock.execute("update documents set acl_groups=%s where source_id=%s and acl_groups is distinct from %s",
                     (acl, source_id, acl))
        lock.execute("update sources set cursor=%s, last_run_at=now(), last_counts=%s where id=%s",
                     (newest.isoformat() if newest else None, Jsonb({**counts, "failed_keys": failed, "retry_ids": retry}), source_id))
    return counts
