"""Connection settings an admin edits on the Settings page (#288). get() is the only reader of these names.

Secrets live in Secrets Manager (AWSCURRENT of the id in SETTINGS_SECRET_IDS) and are never read from the
environment once managed; plain values live in connection_settings, else the environment. Without
SETTINGS_SECRET_IDS (tests, a bare docker run) everything comes from the environment and editing is off."""
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

from app import db

SECRET_NAMES = ("GRAPH_CLIENT_SECRET", "CONFLUENCE_TOKEN", "BRAVE_API_KEY", "OLLAMA_CLOUD_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")
PLAIN_NAMES = ("GRAPH_CLIENT_ID", "CONFLUENCE_EMAIL", "EXTRACT_MODEL", "DRAFT_MODEL")
MANAGED = SECRET_NAMES + PLAIN_NAMES
TWO_ADMIN = ("OLLAMA_CLOUD_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "EXTRACT_MODEL", "DRAFT_MODEL")  # where data goes
TTL = 60  # web and worker agree within this
STALE_LIMIT = 600  # an outage serves the last value this long, then reads fail
CLEARED = "-"  # Secrets Manager refuses an empty SecretString, so a cleared secret holds this

_cache: dict[str, tuple[float, str | None]] = {}  # name -> (loaded at, value)


def secret_ids() -> dict[str, str]:
    raw = os.environ.get("SETTINGS_SECRET_IDS", "").strip()
    return json.loads(raw) if raw else {}


def editable() -> bool:
    return bool(secret_ids())


def clear_cache() -> None:
    _cache.clear()


def _client():
    return boto3.client("secretsmanager")


def _load_secret(secret_id: str) -> str | None:
    try:
        value = _client().get_secret_value(SecretId=secret_id).get("SecretString")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":  # also: created by Terraform, never set
            return None
        raise
    return None if value in (None, "", CLEARED) else value


def _load_plain(name: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute("select value from connection_settings where name = %s", (name,)).fetchone()
    return row[0] if row else os.environ.get(name) or None


def get(name: str) -> str | None:
    if name not in MANAGED:
        raise KeyError(name)
    ids = secret_ids()
    if not ids:
        return os.environ.get(name) or None
    now = time.monotonic()
    hit = _cache.get(name)
    if hit and now - hit[0] < TTL:
        return hit[1]
    try:
        if name in SECRET_NAMES:
            value = _load_secret(ids[name]) if name in ids else None  # a managed secret never reads the environment
        else:
            value = _load_plain(name)
    except Exception:
        if hit and now - hit[0] < STALE_LIMIT:
            return hit[1]
        raise
    _cache[name] = (now, value)
    return value
