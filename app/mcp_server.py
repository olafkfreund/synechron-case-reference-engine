"""MCP server mounted at /mcp (#249). Phase 0: a single unauthenticated `ping` tool."""
import os
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


def build(origin: str):
    """The Starlette app for /mcp. Stateless, so any replica serves any call."""
    server = MCPServer("reference-engine")

    @server.tool()
    def ping() -> str:
        """Check that the server is up."""
        return "pong"

    # Host and Origin checks (DNS rebinding); a request with no Origin is allowed
    security = TransportSecuritySettings(allowed_hosts=[urlsplit(origin).netloc], allowed_origins=[origin])
    app = server.streamable_http_app(streamable_http_path="/", stateless_http=True, transport_security=security)
    return app, server.session_manager


def enabled() -> bool:
    return os.environ.get("MCP_ENABLED") == "1"
