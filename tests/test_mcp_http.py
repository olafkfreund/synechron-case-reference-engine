"""MCP endpoint over HTTP (#249)."""
import pytest
from fastapi.testclient import TestClient

from app import main

ORIGIN = "http://testserver"
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def env(monkeypatch):
    for k, v in dict(SESSION_SECRET="test-secret", SESSION_HTTPS_ONLY="false", APP_ORIGIN=ORIGIN,
                     AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1").items():
        monkeypatch.setenv(k, v)


def rpc(c, method, version, params=None, **hdr):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                  headers={**HDR, "MCP-Protocol-Version": version, "Mcp-Method": method, **hdr})


@pytest.mark.parametrize("version", ["2025-06-18", "2026-07-28"])
def test_tools_list_both_protocol_versions(env, monkeypatch, version):
    monkeypatch.setenv("MCP_ENABLED", "1")
    with TestClient(main.create_app()) as c:  # `with` runs the lifespan, which starts the session manager
        params = {"_meta": {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}} if version == "2026-07-28" else {}
        r = rpc(c, "tools/list", version, params)
        assert r.status_code == 200, r.text
        assert "ping" in r.text


def test_mcp_is_404_when_disabled(env, monkeypatch):
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    with TestClient(main.create_app()) as c:
        assert rpc(c, "tools/list", "2025-06-18").status_code == 404
