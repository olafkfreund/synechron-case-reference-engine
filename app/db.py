import json
import os
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
ROLES = SCHEMA.with_name("roles.sql")


def _read_password(arn: str) -> str:
    import boto3
    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])["password"]


@lru_cache(maxsize=1)
def _secret_password() -> str:
    """The database password, read at runtime: the master secret rotates (RDS, every 7 days), so a
    value injected once at task start would stop working."""
    return _read_password(os.environ["DB_SECRET_ARN"])


def url() -> str:
    """DATABASE_URL, or a connection string built from DB_HOST/DB_USER and DB_SECRET_ARN (or DB_PASSWORD),
    plus DB_PORT, DB_NAME, DB_SSLMODE and DB_SSLROOTCERT."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    password = _secret_password() if os.environ.get("DB_SECRET_ARN") else os.environ["DB_PASSWORD"]
    extra = {"sslrootcert": os.environ["DB_SSLROOTCERT"]} if os.environ.get("DB_SSLROOTCERT") else {}
    return make_conninfo(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                         dbname=os.environ.get("DB_NAME", "refs"), user=os.environ["DB_USER"],
                         password=password, sslmode=os.environ.get("DB_SSLMODE", "require"), **extra)


def connect(autocommit=False):
    try:
        return psycopg.connect(url(), autocommit=autocommit)
    except psycopg.OperationalError as e:
        if not os.environ.get("DB_SECRET_ARN") or "password authentication failed" not in str(e):
            raise
        _secret_password.cache_clear()  # rotated since we cached it: fetch the new one, retry once
        return psycopg.connect(url(), autocommit=autocommit)


def apply_app_role(conn, password: str) -> None:
    """Create/refresh the restricted role `refs_app` (sql/roles.sql) and set its password."""
    conn.execute(ROLES.read_text())
    # hashed on the client: the plaintext never reaches the server (error logs, pg_stat_statements)
    hashed = conn.pgconn.encrypt_password(password.encode(), b"refs_app", b"scram-sha-256").decode()
    conn.execute(sql.SQL("alter role refs_app password {}").format(sql.Literal(hashed)))


def init():
    """Schema (and the app role when DB_APP_ROLE_SECRET_ARN is set). Needs a schema owner: run it from
    app.migrate as the master user. Idempotent: every statement is `if not exists`."""
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
        if os.environ.get("DB_APP_ROLE_SECRET_ARN"):
            apply_app_role(conn, _read_password(os.environ["DB_APP_ROLE_SECRET_ARN"]))


def init_if_requested():
    """Local compose only (DB_INIT_ON_START=1): in AWS the processes connect as a role that cannot do DDL."""
    if os.environ.get("DB_INIT_ON_START") == "1":
        init()
