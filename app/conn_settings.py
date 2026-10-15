"""Connection settings an admin edits on the Settings page (#288). get() is the only reader of these names.

Secrets live in Secrets Manager (AWSCURRENT of the id in SETTINGS_SECRET_IDS) and are never read from the
environment once managed; plain values live in connection_settings, else the environment. Without
SETTINGS_SECRET_IDS (tests, a bare docker run) everything comes from the environment and editing is off."""
import json
import logging
import os
import re
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app import db

log = logging.getLogger(__name__)

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
    # a session per client: the default one is not thread-safe; short timeouts so an outage serves the stale value fast
    return boto3.session.Session().client("secretsmanager", config=Config(
        connect_timeout=2, read_timeout=5, retries={"max_attempts": 2}))


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


def _pending(secret_id: str, value: str) -> str:
    """Store value as a new version labelled REFS_PENDING; AWSCURRENT does not move."""
    if "AWSCURRENT" not in _stages(secret_id):
        # the first version of a secret always becomes AWSCURRENT: give it a cleared one so the new value waits
        _client().put_secret_value(SecretId=secret_id, SecretString=CLEARED)
    return _client().put_secret_value(SecretId=secret_id, SecretString=value, VersionStages=[PENDING])["VersionId"]


def _label_pending(secret_id: str, version: str) -> None:
    held = _stages(secret_id).get(PENDING)
    if held != version:
        _client().update_secret_version_stage(SecretId=secret_id, VersionStage=PENDING, MoveToVersionId=version,
                                              **({"RemoveFromVersionId": held} if held else {}))


def _tidy(secret_id: str, version: str) -> None:
    """Drop REFS_PENDING after the change is committed; a label left behind is harmless, so failures are only logged."""
    try:
        _client().update_secret_version_stage(SecretId=secret_id, VersionStage=PENDING, RemoveFromVersionId=version)
    except Exception as e:  # noqa: BLE001
        log.warning("could not drop %s from a version of %s: %s", PENDING, secret_id, type(e).__name__)


def _commit_and_activate(conn, moves: list[tuple[str, str]]) -> None:
    """The audit and id rows are written; now make each (secret id, version) current and commit. If anything
    fails, the secrets go back to the versions that were current, so no change is live without its audit row."""
    before = [(sid, _stages(sid).get("AWSCURRENT")) for sid, _ in moves]
    try:
        for sid, version in moves:
            _make_current(sid, version)
        conn.commit()
    except Exception:
        for sid, old in before:
            if old:
                _make_current(sid, old)
        raise
    for sid, version in moves:
        _tidy(sid, version)


def _log(conn, name, action, actor, old=None, new=None, version=None):
    conn.execute("insert into connection_setting_changes(name, action, old, new, secret_version, actor) "
                 "values (%s,%s,%s,%s,%s,%s)", (name, action, old, new, version, actor))


def _lock(conn, name):
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", ("conn_settings:" + name,))


def proposal(conn, name):
    """(row id, actor, new, secret_version) of the open proposal: the name's latest row, when that is a proposal."""
    row = conn.execute("select id, action, actor, new, secret_version from connection_setting_changes "
                       "where name=%s order by id desc limit 1", (name,)).fetchone()
    return (row[0], *row[2:]) if row and row[1] == "proposed" else None


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


def _open_check(conn, name):
    _lock(conn, name)
    if proposal(conn, name):
        raise Refused(409, f"{name} has a change waiting for approval")


def save(conn, actor: str, values: dict[str, str]) -> None:
    """One save of one connector's form. Blank fields are unchanged. A one-admin setting takes effect now;
    a two-admin one becomes a proposal. Commits."""
    ids = _ids()
    checked = {name: check(name, raw) for name, raw in values.items() if name in MANAGED and raw.strip()}
    for name in sorted(set(values) & set(MANAGED)):  # the whole form's names, so a pair is compared under its lock
        _open_check(conn, name)
    changes = {n: v for n, v in checked.items() if n in SECRET_NAMES or v != _plain_now(conn, n)}
    for id_name, secret_name in PAIRS.items():
        if id_name in changes and secret_name not in changes:
            raise Refused(400, f"{secret_name} is required with a new {id_name}")
    pending = []
    try:
        for name, value in changes.items():
            if name in SECRET_NAMES:
                version = _pending(ids[name], value)
                if name in TWO_ADMIN:
                    _log(conn, name, "proposed", actor, version=version)
                else:
                    pending.append((ids[name], version))
                    _log(conn, name, "applied", actor, version=version)
            elif name in TWO_ADMIN:
                _log(conn, name, "proposed", actor, old=_plain_now(conn, name), new=value)
            else:
                _log(conn, name, "applied", actor, old=_plain_now(conn, name), new=value)
                _set_plain(conn, name, value, actor)
        _commit_and_activate(conn, pending)
    finally:
        clear_cache()


def decide(conn, actor: str, name: str, action: str, proposal_id: int) -> None:
    """approve / reject (another admin) or withdraw (the proposer) the proposal the admin saw. Commits."""
    ids = _ids()
    _lock(conn, name)
    open_ = proposal(conn, name)
    if not open_ or open_[0] != proposal_id:  # withdrawn, decided or replaced since the page was loaded
        raise Refused(409, f"the change to {name} is no longer waiting; reload the page")
    _, proposer, new, version = open_
    if action in ("approve", "reject") and actor == proposer:
        raise Refused(403, "a second admin must decide this change")
    if action == "withdraw" and actor != proposer:
        raise Refused(403, "only the admin who proposed it can withdraw it")
    done = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
    try:
        if name in SECRET_NAMES:
            _log(conn, name, done, actor, version=version)
            _commit_and_activate(conn, [(ids[name], version)] if action == "approve" else [])
            if action != "approve":
                _tidy(ids[name], version)
        else:
            if action == "approve":
                check(name, new)  # still in MODEL_CHOICES
            _log(conn, name, done, actor, old=_plain_now(conn, name), new=new)
            if action == "approve":
                _set_plain(conn, name, new, actor)
            conn.commit()
    finally:
        clear_cache()


def restore(conn, actor: str, name: str) -> None:
    """Back to the previous value; a two-admin setting proposes it instead. Commits."""
    ids = _ids()
    if name in PAIRS or name in PAIRS.values():  # half a pair back would mix two identities
        raise Refused(400, f"{name} is part of a pair: enter the id and secret again")
    _open_check(conn, name)
    try:
        if name in SECRET_NAMES:
            previous = _stages(ids[name]).get("AWSPREVIOUS")
            if not previous:
                raise Refused(409, f"{name} has no previous value")
            if name in TWO_ADMIN:
                _label_pending(ids[name], previous)
                _log(conn, name, "proposed", actor, version=previous)
                conn.commit()
            else:
                _log(conn, name, "restored", actor, version=previous)
                _commit_and_activate(conn, [(ids[name], previous)])
        else:
            last = conn.execute("select old from connection_setting_changes where name=%s and action in "
                                "('applied','approved','restored') order by id desc limit 1", (name,)).fetchone()
            target = (last[0] if last else None) or os.environ.get(name)  # null: it came from Terraform
            if not last or not target:
                raise Refused(409, f"{name} has no previous value")
            _log(conn, name, "proposed", actor, old=_plain_now(conn, name), new=check(name, target))  # only models: two admins
            conn.commit()
    finally:
        clear_cache()


def clear(conn, actor: str, name: str) -> None:
    """A secret becomes "not configured"; a two-admin one proposes that. Commits."""
    ids = _ids()
    if name not in SECRET_NAMES:
        raise Refused(400, f"{name} cannot be cleared")
    _open_check(conn, name)
    if _load_secret(ids[name]) is None:  # clearing again would push the real previous value out of AWSPREVIOUS
        raise Refused(409, f"{name} is already not configured")
    try:
        version = _pending(ids[name], CLEARED)
        if name in TWO_ADMIN:
            _log(conn, name, "proposed", actor, version=version)
            conn.commit()
        else:
            _log(conn, name, "cleared", actor, version=version)
            _commit_and_activate(conn, [(ids[name], version)])
    finally:
        clear_cache()
