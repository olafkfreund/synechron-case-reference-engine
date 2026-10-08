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


def test_data_class_defaults_to_confidential_and_is_checked():
    db.init()
    with db.connect() as c:
        try:
            got = c.execute(
                "insert into sources(kind,name) values ('s3','dc') returning data_class"
            ).fetchone()[0]
            assert got == "confidential"
            c.execute("select 1 from model_approvals limit 1")  # the table exists
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute("update sources set data_class='secret' where name='dc'")
        finally:
            c.rollback()


def test_case_basis_defaults_to_delivered_and_is_checked():
    db.init()
    with db.connect() as c:
        try:
            sid = c.execute("insert into sources(kind,name) values ('s3','bs') returning id").fetchone()[0]
            did = c.execute("insert into documents(source_id,external_id,checksum) values (%s,'b','x') returning id", (sid,)).fetchone()[0]
            assert c.execute("insert into cases(document_id) values (%s) returning basis", (did,)).fetchone()[0] == "delivered"
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute("update cases set basis='promised' where document_id=%s", (did,))
        finally:
            c.rollback()


def test_url_from_parts_when_database_url_is_unset(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k, v in dict(DB_HOST="db.example", DB_USER="refs", DB_PASSWORD="p@ss w'rd=\\x").items():
        monkeypatch.setenv(k, v)
    got = conninfo_to_dict(db.url())
    assert got == {"host": "db.example", "port": "5432", "dbname": "refs", "user": "refs",
                   "password": "p@ss w'rd=\\x", "sslmode": "require"}  # odd characters survive
    monkeypatch.setenv("DATABASE_URL", "postgresql://a@b/c")
    assert db.url() == "postgresql://a@b/c"  # an explicit URL wins


def test_rotated_secret_is_fetched_again(monkeypatch):
    calls = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k, v in dict(DB_HOST="h", DB_USER="u", DB_SECRET_ARN="arn:x").items():
        monkeypatch.setenv(k, v)
    passwords = iter(["old", "new"])
    monkeypatch.setattr(db, "_secret_password", lru(lambda: next(passwords)))

    def fake_connect(conninfo, autocommit=False):
        calls.append(conninfo)
        if "password=old" in conninfo:
            raise psycopg.OperationalError('FATAL:  password authentication failed for user "u"')
        return "conn"
    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    assert db.connect() == "conn" and len(calls) == 2 and "password=new" in calls[1]


def lru(f):
    from functools import lru_cache
    return lru_cache(maxsize=1)(f)
