from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from app import db

TABLES = {"sources", "documents", "cases", "clients", "jobs", "research", "generations"}


def test_init_idempotent_and_tables():
    db.init()
    db.init()
    with db.connect() as c:
        rows = c.execute(
            "select table_name from information_schema.tables where table_schema='public'"
        ).fetchall()
    assert TABLES <= {r[0] for r in rows}


def test_concurrent_init():
    # web and worker start together on an empty db; the advisory lock must serialise them
    with db.connect() as c:
        c.execute("drop schema public cascade; create schema public")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: db.init(), range(4)))


def test_document_identity_is_source_and_external_id():
    db.init()
    with db.connect() as c:
        sid = c.execute(
            "insert into sources(kind,name) values ('s3','t') "
            "on conflict(name) do update set name=excluded.name returning id"
        ).fetchone()[0]
        try:
            ins = "insert into documents(source_id,external_id,checksum) values (%s,%s,'x')"
            c.execute(ins, (sid, "a"))
            c.execute(ins, (sid, "b"))  # same content elsewhere is a separate document
            with pytest.raises(psycopg.errors.UniqueViolation):
                c.execute(ins, (sid, "a"))
        finally:
            c.rollback()
