import uuid

from psycopg.types.json import Jsonb

from app import db
from tests.test_auth import ADMIN, REV, USER, client, env  # noqa: F401


def test_settings_is_admin_only(env):  # noqa: F811
    db.init()
    assert client([REV]).get("/admin/settings").status_code == 403
    assert client([USER]).get("/admin/settings").status_code == 403
    assert client([ADMIN]).get("/admin/settings").status_code == 200


def test_settings_redacts_secrets_and_urls(env, monkeypatch):  # noqa: F811
    db.init()
    secret_names = ("GRAPH_CLIENT_SECRET", "CONFLUENCE_TOKEN", "BRAVE_API_KEY", "OLLAMA_CLOUD_KEY", "OIDC_CLIENT_SECRET",
                    "OPENAI_API_KEY", "GEMINI_API_KEY")
    sentinels = []
    for i, name in enumerate(secret_names):
        value = f"SECRET-SENTINEL-{i}"
        monkeypatch.setenv(name, value)
        sentinels.append(value)
    monkeypatch.setenv("EXTRACT_MODEL_OPTIONS", "MODEL-SENTINEL-1")
    monkeypatch.setenv("DRAFT_MODEL_OPTIONS", "MODEL-SENTINEL-2")
    sentinels += ["MODEL-SENTINEL-1", "MODEL-SENTINEL-2"]
    monkeypatch.setenv("OIDC_METADATA_URL", "https://user:password@idp.example:8443/metadata?token=URL-SENTINEL-1#fragment")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://user:password@app.example/auth?token=URL-SENTINEL-2#fragment")
    monkeypatch.setenv("OLLAMA_API_BASE", "https://user:password@models.example:11434/api?token=URL-SENTINEL-3#fragment")
    sentinels += ["user:password", "URL-SENTINEL-1", "URL-SENTINEL-2", "URL-SENTINEL-3"]
    html = client([ADMIN]).get("/admin/settings").text
    assert all(value not in html for value in sentinels)
    assert "Configured" in html
    assert "https://idp.example:8443/metadata" in html
    assert "https://app.example/auth" in html
    assert "https://models.example:11434/api" in html
    for name in secret_names:
        monkeypatch.delenv(name, raising=False)
    assert "Not configured" in client([ADMIN]).get("/admin/settings").text


def test_settings_marks_own_group_and_shows_failed_crawl_time(env, monkeypatch):  # noqa: F811
    db.init()
    monkeypatch.setenv("ROLE_ADMIN_GROUPS", f"other,{ADMIN}")
    name = f"settings-{uuid.uuid4().hex}"
    with db.connect() as conn:
        sid = conn.execute("insert into sources(kind, name, config, last_counts) values ('s3',%s,%s,%s) returning id",
                           (name, Jsonb({"bucket": "bkt"}), Jsonb({"new": 2, "failed_keys": ["SECRET-KEY"]}))).fetchone()[0]
        jid = conn.execute("insert into jobs(kind, payload, status, error, updated_at) "
                           "values ('crawl_s3', %s, 'failed', 'SECRET-ERR', '2026-10-14 12:34+00') returning id",
                           (Jsonb({"source_id": sid}),)).fetchone()[0]
    try:
        html = client([ADMIN]).get("/admin/settings").text
        assert f"Source #{sid}" in html and name in html
        assert "2026-10-14 12:34" in html
        assert "SECRET-ERR" not in html and "SECRET-KEY" not in html
        assert f"{ADMIN} (your group)" in html
        assert html.index(f"{ADMIN} (your group)") < html.index("other</span>")
    finally:
        with db.connect() as conn:
            conn.execute("delete from jobs where id=%s", (jid,))
            conn.execute("delete from sources where id=%s", (sid,))


def test_config_change_is_visible_on_audit(env):  # noqa: F811
    db.init()
    name = f"audit-settings-{uuid.uuid4().hex}"
    with db.connect() as conn:
        sid = conn.execute("insert into sources(kind, name, config) values ('s3',%s,%s) returning id",
                           (name, Jsonb({"bucket": "bkt"}))).fetchone()[0]
        change_id = conn.execute("insert into source_config_changes(source_id, action, old, new, changed_by) "
                                 "values (%s,'config',%s,%s,'settings-tester') returning id",
                                 (sid, Jsonb({"name": name, "config": {"bucket": "bkt", "prefix": "old/"}}),
                                  Jsonb({"name": name, "config": {"bucket": "bkt", "prefix": "new/"}}))).fetchone()[0]
    try:
        html = client([ADMIN]).get("/admin/audit").text
        assert name in html and "prefix: old/ → new/" in html and "settings-tester" in html
        assert "bucket: bkt" not in html
    finally:
        with db.connect() as conn:
            conn.execute("delete from source_config_changes where id=%s", (change_id,))
            conn.execute("delete from sources where id=%s", (sid,))


def test_settings_shows_mcp_on_by_default(env, monkeypatch):  # noqa: F811
    db.init()
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    for k, v in dict(MCP_ISSUER="https://login.example/t/v2.0", MCP_AUDIENCE="api-aud", MCP_CLIENT_IDS="c-1").items():
        monkeypatch.setenv(k, v)
    html = client([ADMIN]).get("/admin/settings").text
    assert "<dt>Enabled</dt><dd>Yes" in html and "api-aud" in html
    monkeypatch.setenv("MCP_ENABLED", "0")
    assert "<dt>Enabled</dt><dd>No" in client([ADMIN]).get("/admin/settings").text
