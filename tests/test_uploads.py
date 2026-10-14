import uuid

import boto3
import psycopg
import pytest

from app import db, ingest as ing, worker
from tests.test_hardening import app_role, as_app  # noqa: F401 - fixture
from tests.test_ingest import env  # noqa: F401 - fixture


def test_upload_job_ingests_the_object(env):
    s3, sid = env
    key = f"in/{uuid.uuid4().hex}/a.docx"
    s3.put_object(Bucket="src", Key=key, Body=b"hello upload")
    worker.HANDLERS["ingest_upload"]({"source_id": sid, "key": key})
    with db.connect() as c:
        assert c.execute("select title from documents where source_id=%s and external_id=%s", (sid, key)).fetchall() == [("a.docx",)]
        c.execute("delete from documents where source_id=%s", (sid,))


def test_uploads_are_append_only_for_the_app(app_role):  # noqa: F811
    with db.connect() as c:
        db.apply_app_role(c, "pw-1")
    for stmt in ("update uploads set bytes=1", "delete from uploads"):
        with as_app("pw-1") as a:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(stmt)
    with as_app("pw-1") as a:
        a.execute("insert into uploads (user_key, source_id, s3_key, bytes, job_id) values ('t-up', 1, 'k', 1, 1)")
    with db.connect() as c:
        c.execute("delete from uploads where user_key='t-up'")
