import json
import os
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


@lru_cache(maxsize=1)
def _secret_password() -> str:
    """The RDS-managed master secret, read at runtime: RDS rotates it (every 7 days), so a value
    injected once at task start would stop working."""
    import boto3
    return json.loads(boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["DB_SECRET_ARN"])["SecretString"])["password"]


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


def init():
    # idempotent: every statement is `if not exists`
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
