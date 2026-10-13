"""MCP endpoint over HTTP (#249)."""
import time

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from app import main, mcp_server

ORIGIN = "http://testserver"
ISS, AUD, TID = "https://login.example/tenant/v2.0", "api-app-id", "tenant"
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # one signing key for the test session


@pytest.fixture
def env(monkeypatch):
    for k, v in dict(SESSION_SECRET="test-secret", SESSION_HTTPS_ONLY="false", APP_ORIGIN=ORIGIN,
                     AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1",
                     MCP_ENABLED="1", MCP_ISSUER=ISS, MCP_AUDIENCE=AUD, MCP_TENANT_ID=TID).items():
        monkeypatch.setenv(k, v)

    async def jwks(url):
        return {"keys": [KEY.as_dict(private=False)]}
    monkeypatch.setattr(mcp_server, "fetch_jwks", jwks)


def token(**over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "tid": TID, "exp": now + 600, "iat": now, "scp": "refs.access", "oid": "o1",
              "azp": "client-1", **over}
    return jwt.encode({"alg": "RS256", "kid": "k1"}, {k: v for k, v in claims.items() if v is not None}, KEY)


def rpc(c, method, version, params=None, tok=None):
    h = {**HDR, "MCP-Protocol-Version": version, "Mcp-Method": method}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=h)


@pytest.mark.parametrize("version", ["2025-06-18", "2026-07-28"])
def test_tools_list_both_protocol_versions(env, version):
    with TestClient(main.create_app()) as c:  # `with` runs the lifespan, which starts the session manager
        meta = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}
        r = rpc(c, "tools/list", version, {"_meta": meta} if version == "2026-07-28" else {}, token())
        assert r.status_code == 200, r.text
        assert "ping" in r.text


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
