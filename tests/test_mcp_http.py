"""MCP endpoint over HTTP (#249)."""
import asyncio
import base64
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

from app import db, main, mcp_server

ORIGIN = "http://testserver"
OK_CLIENTS = frozenset({"client-1"})
ISS, AUD, TID = "https://login.example/tenant/v2.0", "api-app-id", "tenant"
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # one signing key for the test session


@pytest.fixture
def env(monkeypatch):
    for k, v in dict(SESSION_SECRET="test-secret", SESSION_HTTPS_ONLY="false", APP_ORIGIN=ORIGIN,
                     AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1",
                     ROLE_USER_GROUPS="g-user", ROLE_ADMIN_GROUPS="g-admin", MCP_CLIENT_IDS="client-1", ROLE_REVIEWER_GROUPS="g-rev", MCP_ENABLED="1", MCP_ISSUER=ISS, MCP_AUDIENCE=AUD, MCP_TENANT_ID=TID).items():
        monkeypatch.setenv(k, v)

    async def jwks(url):
        return {"keys": [KEY.as_dict(private=False)]}
    monkeypatch.setattr(mcp_server, "fetch_jwks", jwks)


def token(**over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "tid": TID, "exp": now + 600, "iat": now, "scp": "refs.access", "oid": "o1", "groups": ["g-user", "noise"],
              "azp": "client-1", **over}
    return jwt.encode({"alg": "RS256", "kid": "k1"}, {k: v for k, v in claims.items() if v is not None}, KEY)


def rpc(c, method, version, params=None, tok=None, **extra):
    h = {**HDR, "MCP-Protocol-Version": version, "Mcp-Method": method, **extra}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=h)


@pytest.mark.parametrize("version", ["2025-06-18", "2026-07-28"])
def test_tools_list_both_protocol_versions(env, version):
    with TestClient(main.create_app(), follow_redirects=False) as c:  # `with` runs the lifespan, which starts the session manager
        meta = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}
        r = rpc(c, "tools/list", version, {"_meta": meta} if version == "2026-07-28" else {}, token())
        assert r.status_code == 200, r.text
        assert "search_references" in r.text and "outputSchema" in r.text


def test_no_token_gets_challenge_and_prm_resolves(env):
    with TestClient(main.create_app()) as c:
        r = rpc(c, "tools/list", "2025-06-18")
        assert r.status_code == 401
        challenge = r.headers["www-authenticate"]
        url = challenge.split('resource_metadata="')[1].split('"')[0]
        assert url == f"{ORIGIN}/.well-known/oauth-protected-resource/mcp"
        prm = c.get(url.removeprefix(ORIGIN))
        assert prm.status_code == 200
        assert prm.json()["authorization_servers"] == [ISS]
        assert prm.json()["scopes_supported"] == [f"{AUD}/refs.access"]


@pytest.mark.parametrize("bad,status", [
    ({"aud": "other"}, 401), ({"iss": "https://evil/v2.0"}, 401), ({"tid": "other"}, 401),
    ({"exp": int(time.time()) - 600}, 401), ({"iat": None}, 401),
    ({"scp": None, "roles": ["refs.access"]}, 401),  # app-only token
    ({"scp": "other.scope"}, 403)])
def test_bad_tokens_refused(env, bad, status):
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(**bad)).status_code == status


def test_token_signed_by_unknown_key_refused(env):
    other = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    forged = jwt.encode({"alg": "RS256", "kid": "k1"}, {"iss": ISS, "aud": AUD, "tid": TID, "scp": "refs.access",
                        "exp": int(time.time()) + 600, "iat": int(time.time())}, other)
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=forged).status_code == 401


def test_mcp_is_404_when_disabled(env, monkeypatch):
    monkeypatch.delenv("MCP_ENABLED")
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18").status_code == 404


def b64(o):
    return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize("header", [[], None, 5, "x"])
def test_malformed_token_header_is_401_not_500(env, header):
    with TestClient(main.create_app(), raise_server_exceptions=False) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=f"{b64(header)}.{b64({})}.x").status_code == 401


def test_hs256_and_none_tokens_refused(env):
    claims = {"iss": ISS, "aud": AUD, "tid": TID, "scp": "refs.access", "exp": int(time.time()) + 600, "iat": int(time.time())}
    hs = jwt.encode({"alg": "HS256", "kid": "k1"}, claims, OctKey.import_key("s" * 32))
    none = f"{b64({'alg': 'none', 'kid': 'k1'})}.{b64(claims)}."
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=hs).status_code == 401
        assert rpc(c, "tools/list", "2025-06-18", tok=none).status_code == 401


def test_expiry_leeway_reaches_the_sdk(env):
    now = int(time.time())
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(exp=now - 30)).status_code == 200
        assert rpc(c, "tools/list", "2025-06-18", tok=token(exp=now - 90)).status_code == 401


def verifier(monkeypatch, fetch):
    monkeypatch.setattr(mcp_server, "fetch_jwks", fetch)
    return mcp_server.EntraVerifier(ISS, AUD, "https://jwks", TID)


def test_unknown_kids_make_at_most_one_fetch(monkeypatch):
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"keys": [KEY.as_dict(private=False)]}
    v = verifier(monkeypatch, fetch)
    for i in range(50):
        forged = jwt.encode({"alg": "RS256", "kid": f"nope{i}"}, {"iss": ISS}, KEY)
        assert asyncio.run(v.verify_token(forged)) is None
    assert len(calls) <= 1


def test_failed_refresh_keeps_cached_keys(monkeypatch):
    ok = True

    async def fetch(url):
        if not ok:
            raise RuntimeError("jwks down")
        return {"keys": [KEY.as_dict(private=False)]}
    v = verifier(monkeypatch, fetch)
    assert asyncio.run(v.verify_token(token()))
    ok = False
    v._fetched = v._attempt = 0.0  # cache is stale and the backoff has passed
    assert asyncio.run(v.verify_token(token()))
    assert v._attempt > 0  # it did try


def test_foreign_origin_403_and_wrong_host_421(env):
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(), Origin="https://evil.example").status_code == 403
        assert rpc(c, "tools/list", "2025-06-18", tok=token(), Origin=ORIGIN).status_code == 200
    with TestClient(main.create_app(), base_url="http://evil.example") as c:  # a Host header alone loses the Authorization header
        assert rpc(c, "tools/list", "2025-06-18", tok=token()).status_code == 421


def test_guard_still_applies_beside_mcp(env):
    with TestClient(main.create_app()) as c:
        assert c.post("/mcpx", json={}).status_code == 403  # CSRF: only /mcp and /mcp/... are exempt
        r = c.post("/mcp", content=iter([b"{}"]), headers={**HDR, "Authorization": f"Bearer {token()}"})
        assert r.status_code == 413  # no content-length


def test_enabled_without_issuer_raises(env, monkeypatch):
    monkeypatch.delenv("MCP_ISSUER")
    with pytest.raises(RuntimeError, match="MCP_ISSUER"):
        main.create_app()


def test_prm_is_404_when_disabled(env, monkeypatch):
    monkeypatch.delenv("MCP_ENABLED")
    with TestClient(main.create_app()) as c:
        assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 404


def fresh():
    return f"t-{uuid.uuid4()}"  # the test database persists: never reuse a key that may have a cutoff


def test_user_from_token_builds_the_portal_user(env):
    db.init()
    u = mcp_server.user_from_token({"oid": "o1", "sub": "s1", "preferred_username": "a@b", "groups": ["g-admin", "noise"],
                                    "iat": time.time(), "azp": "client-1"}, OK_CLIENTS)
    assert (u.sub, u.name, u.groups) == ("o1", "a@b", frozenset({"g-admin"}))
    assert u.roles == frozenset({"user", "reviewer", "admin"})
    assert mcp_server.user_from_token({"sub": "s1", "name": "N", "groups": "g-user", "iat": time.time(), "appid": "client-1"},
                                      OK_CLIENTS).sub == "s1"


@pytest.mark.parametrize("claims,status", [
    ({"groups": ["noise"]}, 403),  # no role
    ({"groups": None}, 403),
    ({"_claim_names": {"groups": "src1"}}, 403),  # overage
    ({"hasgroups": True}, 403),
    ({"oid": None, "sub": None}, 401)])
def test_user_refusals_through_http(env, claims, status):
    db.init()
    with TestClient(main.create_app()) as c:
        r = rpc(c, "tools/list", "2025-06-18", tok=token(**{"oid": fresh(), **claims}))
        assert r.status_code == status, r.text
        assert ("www-authenticate" in r.headers) == (status == 401)


def test_session_cutoff_refuses_older_tokens_only(env):
    db.init()
    key = fresh()
    with db.connect() as conn:
        main.cut_sessions(conn, key)
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(oid=key, iat=int(time.time()) - 5)).status_code == 401
        time.sleep(1.1)
        assert rpc(c, "tools/list", "2025-06-18", tok=token(oid=key)).status_code == 200


def test_tool_call_runs_as_the_token_user(env):
    db.init()
    with TestClient(main.create_app()) as c:
        r = rpc(c, "tools/call", "2025-06-18", {"name": "list_latest", "arguments": {}}, tok=token(oid=fresh()))
        assert r.status_code == 200 and "isError\":true" not in r.text and "\"rows\"" in r.text


def test_session_cookie_alone_gets_no_access_to_mcp(env):
    from tests.test_auth import client as portal_client
    c = portal_client(["g-user"])
    assert c.cookies.get("session") and c.get("/me").status_code == 200  # a real, valid portal session
    assert rpc(c, "tools/list", "2025-06-18").status_code == 401


def test_post_to_mcp_is_served_without_a_redirect(env):
    with TestClient(main.create_app(), follow_redirects=False) as c:  # real clients may not follow a 307
        assert rpc(c, "tools/list", "2025-06-18").status_code == 401
        assert rpc(c, "tools/list", "2025-06-18", tok=token()).status_code == 200


def audit_rows(key):
    with db.connect() as conn:
        return conn.execute("select tool, outcome, client_id from tool_calls where user_key = %s order by id", (key,)).fetchall()


def test_only_approved_clients_are_accepted(env):
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(azp="client-1")).status_code == 200
        assert rpc(c, "tools/list", "2025-06-18", tok=token(azp=None, appid="client-1")).status_code == 200  # v1 name
        for tok in (token(azp="other-app"), token(azp=None)):
            r = rpc(c, "tools/list", "2025-06-18", tok=tok)
            assert r.status_code == 403 and "not approved" in r.text


def test_enabled_without_approved_clients_raises(env, monkeypatch):
    monkeypatch.delenv("MCP_CLIENT_IDS")
    with pytest.raises(RuntimeError, match="MCP_CLIENT_IDS"):
        main.create_app()


def test_refused_calls_are_audited_once_a_minute(env):
    db.init()
    key = fresh()
    with TestClient(main.create_app()) as c:
        for _ in range(4):
            assert rpc(c, "tools/list", "2025-06-18", tok=token(oid=key, azp="other-app")).status_code == 403
        assert rpc(c, "tools/list", "2025-06-18", tok=token(oid=key, groups=["noise"])).status_code == 403  # no role: its own outcome? same
    assert audit_rows(key) == [("-", "denied", "other-app")]  # the later no-role refusal is the same user and outcome


@pytest.mark.parametrize("groups", [5, {"a": 1}, [1, 2]])
def test_malformed_groups_claim_is_403_not_500(env, groups):
    db.init()
    with TestClient(main.create_app(), raise_server_exceptions=False) as c:
        assert rpc(c, "tools/list", "2025-06-18", tok=token(oid=fresh(), groups=groups)).status_code == 403


def test_exact_mcp_path_rewrites_only_slashless_mcp():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
    wrap = mcp_server.ExactMcpPath(app)
    for path in ("/mcp", "/mcpx", "/mcp/x", "/mcp/"):
        asyncio.run(wrap({"type": "http", "path": path}, None, None))
    assert seen == ["/mcp/", "/mcpx", "/mcp/x", "/mcp/"]
