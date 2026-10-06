import os
from pathlib import Path

import psycopg

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect(autocommit=False):
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=autocommit)


def init():
    # idempotent: every statement is `if not exists`
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
