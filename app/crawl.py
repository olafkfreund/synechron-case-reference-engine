from datetime import datetime, timedelta

import boto3
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
