"""MCP server mounted at /mcp (#249). Phase 0: Entra bearer auth and a `ping` tool."""
import asyncio
import base64
import json
import os
import time
from urllib.parse import urlsplit

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

JWKS_TTL = 3600
REFETCH_UNKNOWN_KID = 300  # an unknown kid is unauthenticated input: it may not trigger a fetch per request
REFETCH_BACKOFF = 60  # after a failed or missing fetch
LEEWAY = 60


async def fetch_jwks(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


class EntraVerifier:
    """Checks an Entra v2 access token: RS256 signature, iss, aud, exp, iat, tid, delegated scp."""

    def __init__(self, issuer: str, audience: str, jwks_url: str, tenant: str = "", scope: str = "refs.access"):
        self.issuer, self.audience, self.jwks_url, self.tenant, self.scope = issuer, audience, jwks_url, tenant, scope
        self._keys: KeySet | None = None
        self._fetched = self._attempt = 0.0
        self._lock = asyncio.Lock()

    def _needs_fetch(self, kid) -> bool:
        if self._keys is None or time.time() - self._fetched > JWKS_TTL:
            return time.time() - self._attempt >= REFETCH_BACKOFF
        unknown = bool(kid) and not any(k.kid == kid for k in self._keys.keys)  # key rotation
        return unknown and time.time() - self._attempt >= REFETCH_UNKNOWN_KID

    async def _keyset(self, kid) -> KeySet:
        if self._needs_fetch(kid):
            async with self._lock:  # concurrent callers share one fetch
                if self._needs_fetch(kid):
                    self._attempt = time.time()
                    try:
                        self._keys = KeySet.import_key_set(await fetch_jwks(self.jwks_url))
                        self._fetched = time.time()
                    except Exception:  # keep verifying with the cached keys; the next try waits out the backoff
                        if self._keys is None:
                            raise
        return self._keys

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
            if not isinstance(header, dict):
                return None
            decoded = jwt.decode(token, await self._keyset(header.get("kid")), algorithms=["RS256"])
            claims = decoded.claims
            registry = jwt.JWTClaimsRegistry(
                leeway=LEEWAY, exp={"essential": True}, iat={"essential": True},
                iss={"essential": True, "value": self.issuer}, aud={"essential": True, "value": self.audience},
                **({"tid": {"essential": True, "value": self.tenant}} if self.tenant else {}))
            registry.validate(claims)
        except Exception:  # any malformed or unverifiable token is a plain 401
            return None
        scp = claims.get("scp")
        if not isinstance(scp, str) or not scp:  # app-only tokens carry roles, not scp: refused
            return None
        return AccessToken(
            token=token, client_id=str(claims.get("azp") or claims.get("client_id") or ""), scopes=scp.split(),
            expires_at=int(claims["exp"]) + LEEWAY, resource=self.audience,
            subject=str(claims.get("oid") or claims.get("sub") or ""), claims=claims)


class Refused(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def user_from_token(claims: dict):
    """The same `User` /auth builds, from a validated access token's claims; Refused carries the HTTP status."""
    from app import db, main  # here: main imports this module
    key = claims.get("oid") or claims.get("sub")
    if not isinstance(key, str) or not key:
        raise Refused(401, "token has no subject")
    claim = os.environ.get("OIDC_GROUPS_CLAIM", "groups")
    if claim in (claims.get("_claim_names") or {}) or claims.get("hasgroups"):  # Entra groups overage
        raise Refused(403, "too many groups in the token; assign groups to the application in the IdP")
    groups = claims.get(claim, [])
    groups = {groups} if isinstance(groups, str) else set(groups)
    with db.connect() as conn:
        row = conn.execute("select valid_after from session_cutoffs where sub = %s", (key,)).fetchone()
    if row and claims["iat"] <= row[0].timestamp():
        raise Refused(401, "session ended; get a new token")
    groups = sorted(groups & main.known_groups())
    roles = main.roles_for(groups)
    if "user" not in roles:
        raise Refused(403, "no role for this application")
    name = claims.get("name") or claims.get("preferred_username") or key
    return main.User(key, name, frozenset(groups), roles)


def mcp_user():
    """The signed-in user of the current tool call. Never the portal session: /mcp skips the CSRF check."""
    return get_access_token().claims["_user"]


class UserGate:
    """Runs after the bearer check: builds the User once per request, or refuses with its status."""

    def __init__(self, app, resource_metadata: str):
        self.app, self.challenge = app, f'Bearer error="invalid_token", resource_metadata="{resource_metadata}"'

    async def __call__(self, scope, receive, send):
        who = scope.get("user")
        if isinstance(who, AuthenticatedUser):  # anyone else goes on to the SDK's 401
            try:
                who.access_token.claims["_user"] = await run_in_threadpool(user_from_token, who.access_token.claims)
            except Refused as e:
                hdr = {"WWW-Authenticate": self.challenge} if e.status == 401 else {}
                return await JSONResponse({"error": "access_denied", "error_description": e.detail},
                                          status_code=e.status, headers=hdr)(scope, receive, send)
        await self.app(scope, receive, send)


def enabled() -> bool:
    return os.environ.get("MCP_ENABLED") == "1"


def build(origin: str):
    """The Starlette app for /mcp, its session manager and the root PRM routes. Stateless: any replica serves any call."""
    env = os.environ
    issuer, audience = env.get("MCP_ISSUER", ""), env.get("MCP_AUDIENCE", "")
    if not issuer or not audience:
        raise RuntimeError("MCP_ENABLED needs MCP_ISSUER (Entra v2 issuer) and MCP_AUDIENCE")
    scope = env.get("MCP_SCOPE", "refs.access")
    jwks = env.get("MCP_JWKS_URL") or issuer.removesuffix("/v2.0").rstrip("/") + "/discovery/v2.0/keys"
    resource = env.get("MCP_RESOURCE_URL") or origin + "/mcp"
    verifier = EntraVerifier(issuer, audience, jwks, env.get("MCP_TENANT_ID", ""), scope)
    auth = AuthSettings(issuer_url=issuer, resource_server_url=resource, required_scopes=[scope],
                        validate_token_resource=False)
    server = MCPServer("reference-engine", auth=auth, token_verifier=verifier)

    @server.tool()
    def ping() -> str:
        """Check that the server is up."""
        mcp_user()
        return "pong"

    # Host and Origin checks (DNS rebinding); a request with no Origin is allowed
    u = urlsplit(origin.lower())
    port = u.port or {"http": 80, "https": 443}.get(u.scheme)
    hosts = sorted({u.netloc, u.hostname, f"{u.hostname}:{port}"} if port in (80, 443) else {u.netloc})
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[origin.lower()])
    app = server.streamable_http_app(streamable_http_path="/", stateless_http=True, transport_security=security)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/")
    route.app = UserGate(route.app, str(build_resource_metadata_url(resource)))
    # the SDK registers the PRM inside the mount (/mcp/.well-known/...); the challenge names the root path
    prm = create_protected_resource_routes(resource, [issuer], [f"{audience}/{scope}"])  # full Entra form, as a client requests it
    return app, server.session_manager, prm
