import os
from pathlib import Path

import psycopg

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"])


def init():
    # idempotent: every statement is `if not exists`
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
