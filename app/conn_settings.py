"""Connection settings an admin edits on the Settings page (#288). get() is the only reader of these names.

Secrets live in Secrets Manager (AWSCURRENT of the id in SETTINGS_SECRET_IDS) and are never read from the
environment once managed; plain values live in connection_settings, else the environment. Without
SETTINGS_SECRET_IDS (tests, a bare docker run) everything comes from the environment and editing is off."""
import json
import os
import re
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
        value = _client().get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT").get("SecretString")
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


# ---- writes (Settings page). A secret value never reaches Postgres, a log or an error message. ----

PAIRS = {"GRAPH_CLIENT_ID": "GRAPH_CLIENT_SECRET", "CONFLUENCE_EMAIL": "CONFLUENCE_TOKEN"}  # a new id needs its secret
PENDING = "REFS_PENDING"
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Refused(Exception):
    """A change the server will not make: status is the HTTP code, the message names a setting, never a value."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def model_choices() -> list[str]:
    return [m.strip() for m in os.environ.get("MODEL_CHOICES", "").split(",") if m.strip()]


def check(name: str, value: str) -> str:
    """The value to store, or Refused naming the field."""
    value = value.strip()
    ok = {"GRAPH_CLIENT_ID": lambda v: UUID.fullmatch(v),
          "CONFLUENCE_EMAIL": lambda v: len(v) <= 254 and EMAIL.fullmatch(v),
          "EXTRACT_MODEL": lambda v: v in model_choices(), "DRAFT_MODEL": lambda v: v in model_choices()}.get(
        name, lambda v: 0 < len(v) <= 4096 and v != CLEARED and not any(ord(c) < 32 or ord(c) == 127 for c in v))
    if not ok(value):
        raise Refused(400, f"{name} is not valid")
    return value


def _stages(secret_id: str) -> dict[str, str]:
    """{stage: version id}"""
    vs = _client().describe_secret(SecretId=secret_id).get("VersionIdsToStages", {})
    return {stage: v for v, stages in vs.items() for stage in stages}


def _make_current(secret_id: str, version: str) -> None:
    current = _stages(secret_id).get("AWSCURRENT")
    if current != version:  # AWSPREVIOUS follows the old current
        _client().update_secret_version_stage(SecretId=secret_id, VersionStage="AWSCURRENT", MoveToVersionId=version,
                                              **({"RemoveFromVersionId": current} if current else {}))


def _drop_pending(secret_id: str, version: str) -> None:
    _client().update_secret_version_stage(SecretId=secret_id, VersionStage=PENDING, RemoveFromVersionId=version)


def _put(secret_id: str, value: str, pending: bool) -> str:
    if pending and "AWSCURRENT" not in _stages(secret_id):
        # the first version of a secret always becomes AWSCURRENT: give it a cleared one so the proposal stays pending
        _client().put_secret_value(SecretId=secret_id, SecretString=CLEARED)
    version = _client().put_secret_value(SecretId=secret_id, SecretString=value, VersionStages=[PENDING])["VersionId"]
    if not pending:  # moved explicitly, not by put's implicit AWSCURRENT, so AWSPREVIOUS is the value that was in force
        _make_current(secret_id, version)
        _drop_pending(secret_id, version)
    return version


def _log(conn, name, action, actor, old=None, new=None, version=None):
    conn.execute("insert into connection_setting_changes(name, action, old, new, secret_version, actor) "
                 "values (%s,%s,%s,%s,%s,%s)", (name, action, old, new, version, actor))


def _lock(conn, name):
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", ("conn_settings:" + name,))


def proposal(conn, name):
    """(actor, new, secret_version) of the open proposal: the name's latest row, when that is a proposal."""
    row = conn.execute("select action, actor, new, secret_version from connection_setting_changes "
                       "where name=%s order by id desc limit 1", (name,)).fetchone()
    return row[1:] if row and row[0] == "proposed" else None


def _plain_now(conn, name):
    row = conn.execute("select value from connection_settings where name=%s", (name,)).fetchone()
    return row[0] if row else os.environ.get(name) or None


def _set_plain(conn, name, value, actor):
    conn.execute("insert into connection_settings(name, value, changed_by) values (%s,%s,%s) on conflict (name) "
                 "do update set value=excluded.value, changed_by=excluded.changed_by, changed_at=now()", (name, value, actor))


def _ids():
    ids = secret_ids()
    if not ids:
        raise Refused(409, "editing needs Secrets Manager (SETTINGS_SECRET_IDS)")
    return ids


def save(conn, actor: str, values: dict[str, str]) -> None:
    """One save of one connector's form. Blank fields are unchanged. A one-admin setting takes effect now;
    a two-admin one becomes a proposal."""
    ids = _ids()
    changes = {}
    for name, raw in values.items():
        if name not in MANAGED or not raw.strip():
            continue
        value = check(name, raw)
        if name in PLAIN_NAMES and value == get(name):
            continue
        changes[name] = value
    for id_name, secret_name in PAIRS.items():
        if id_name in changes and secret_name not in changes:
            raise Refused(400, f"{secret_name} is required with a new {id_name}")
    for name in sorted(changes):
        _lock(conn, name)
        if proposal(conn, name):
            raise Refused(409, f"{name} has a change waiting for approval")
    moved = []  # (secret id, version that was current): undone if a later write fails
    try:
        for name in sorted(changes, key=lambda n: n in PLAIN_NAMES):  # secrets first, then the ids
            value = changes[name]
            if name in TWO_ADMIN:
                if name in SECRET_NAMES:
                    _log(conn, name, "proposed", actor, version=_put(ids[name], value, pending=True))
                else:
                    _log(conn, name, "proposed", actor, old=_plain_now(conn, name), new=value)
            elif name in SECRET_NAMES:
                before = _stages(ids[name]).get("AWSCURRENT")
                version = _put(ids[name], value, pending=False)
                moved.append((ids[name], before))
                _log(conn, name, "applied", actor, version=version)
            else:
                _log(conn, name, "applied", actor, old=_plain_now(conn, name), new=value)
                _set_plain(conn, name, value, actor)
    except Exception:
        for secret_id, before in moved:
            if before:
                _make_current(secret_id, before)
        raise
    finally:
        clear_cache()


def decide(conn, actor: str, name: str, action: str) -> None:
    """approve / reject (another admin) or withdraw (the proposer) the open proposal."""
    ids = _ids()
    _lock(conn, name)
    open_ = proposal(conn, name)
    if not open_:
        raise Refused(409, f"{name} has no change waiting for approval")
    proposer, new, version = open_
    if action in ("approve", "reject") and actor == proposer:
        raise Refused(403, "a second admin must decide this change")
    if action == "withdraw" and actor != proposer:
        raise Refused(403, "only the admin who proposed it can withdraw it")
    done = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
    if name in SECRET_NAMES:
        if action == "approve":
            _make_current(ids[name], version)
        _drop_pending(ids[name], version)
        _log(conn, name, done, actor, version=version)
    else:
        old = _plain_now(conn, name)
        if action == "approve":
            _set_plain(conn, name, new, actor)
        _log(conn, name, done, actor, old=old, new=new)
    clear_cache()


def restore(conn, actor: str, name: str) -> None:
    """Back to the previous value; a two-admin setting proposes it instead."""
    ids = _ids()
    if name in PAIRS or name in PAIRS.values():  # half a pair back would mix two identities
        raise Refused(400, f"{name} is part of a pair: enter the id and secret again")
    _lock(conn, name)
    if proposal(conn, name):
        raise Refused(409, f"{name} has a change waiting for approval")
    if name in SECRET_NAMES:
        previous = _stages(ids[name]).get("AWSPREVIOUS")
        if not previous:
            raise Refused(409, f"{name} has no previous value")
        if name in TWO_ADMIN:
            _client().update_secret_version_stage(SecretId=ids[name], VersionStage=PENDING, MoveToVersionId=previous)
            _log(conn, name, "proposed", actor, version=previous)
        else:
            _make_current(ids[name], previous)
            _log(conn, name, "restored", actor, version=previous)
    else:
        last = conn.execute("select old from connection_setting_changes where name=%s and action in "
                            "('applied','approved','restored') order by id desc limit 1", (name,)).fetchone()
        if not last:
            raise Refused(409, f"{name} has no previous value")
        target = last[0] or os.environ.get(name)  # null: it came from Terraform
        if not target:
            raise Refused(409, f"{name} has no previous value")
        _log(conn, name, "proposed", actor, old=_plain_now(conn, name), new=target)  # only models get here: two admins
    clear_cache()


def clear(conn, actor: str, name: str) -> None:
    """A secret becomes "not configured"; a two-admin one proposes that."""
    ids = _ids()
    if name not in SECRET_NAMES:
        raise Refused(400, f"{name} cannot be cleared")
    _lock(conn, name)
    if proposal(conn, name):
        raise Refused(409, f"{name} has a change waiting for approval")
    if name in TWO_ADMIN:
        _log(conn, name, "proposed", actor, version=_put(ids[name], CLEARED, pending=True))
    else:
        _log(conn, name, "cleared", actor, version=_put(ids[name], CLEARED, pending=False))
    clear_cache()
