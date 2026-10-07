import os
import time
from datetime import datetime, timedelta
from pathlib import PurePosixPath

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
