import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath

import boto3
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app import db

ROLES = ("user", "reviewer", "admin")  # each implies the ones before it
SESSION_MAX_AGE = 8 * 3600
# magic bytes: the extension alone is not trusted. No .html: it would be stored XSS if ever served
UPLOAD_MAGIC = {".docx": b"PK\x03\x04", ".pptx": b"PK\x03\x04", ".pdf": b"%PDF"}
BODY_CAP = 1024 * 1024  # non-upload POSTs (forms)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class User:
    sub: str
    name: str
    groups: frozenset[str]
    roles: frozenset[str]


def _env_groups(name: str) -> set[str]:
    return {g.strip() for g in os.environ.get(name, "").split(",") if g.strip()}


def roles_for(groups) -> frozenset[str]:
    got = {r for r in ROLES if _env_groups(f"ROLE_{r.upper()}_GROUPS") & set(groups)}
    # admin implies reviewer implies user
    return frozenset(r for i, r in enumerate(ROLES) if got & set(ROLES[i:]))


def current_user(request: Request) -> User:
    """Identity comes only from the signed session, which is filled from the validated ID token."""
    s = request.session.get("user")
    if not s:
        raise HTTPException(401, "login required")
    return User(s["sub"], s["name"], frozenset(s["groups"]), roles_for(s["groups"]))


def require(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if role not in user.roles:
            raise HTTPException(403, f"{role} role required")
        return user
    return dep


def safe_name(filename: str | None) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name  # drop any client path
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")
    stem, suffix = PurePosixPath(name or "file").stem, PurePosixPath(name).suffix
    return (stem[:100] + suffix[:10]) or "file"  # S3 keys cap at 1,024 bytes


def upload_cap() -> int:
    return int(os.environ.get("UPLOAD_MAX_BYTES", 50 * 1024 * 1024))


def known_groups() -> set[str]:
    """Groups that matter here: role mappings plus every ACL group (keeps the session cookie small)."""
    groups = set().union(*(_env_groups(f"ROLE_{r.upper()}_GROUPS") for r in ROLES))
    with db.connect() as conn:
        groups |= {r[0] for r in conn.execute(
            "select unnest(acl_groups) from documents union select unnest(acl_groups) from sources")}
    return groups


def create_app() -> FastAPI:
    secret = os.environ.get("SESSION_SECRET")
    https_only = os.environ.get("SESSION_HTTPS_ONLY", "true").lower() != "false"
    if not secret or (https_only and len(secret) < 32):
        raise RuntimeError("SESSION_SECRET is required (at least 32 characters when SESSION_HTTPS_ONLY)")
    origin = os.environ.get("APP_ORIGIN", "").rstrip("/")
    if not origin:
        raise RuntimeError("APP_ORIGIN is required (e.g. https://references.example.com), for CSRF checks")

    @asynccontextmanager
    async def lifespan(_):
        db.init_if_requested()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    def healthz():  # load balancer check: no session, no database, nothing to leak
        return PlainTextResponse("ok")
    app.add_middleware(
        SessionMiddleware, secret_key=secret, max_age=SESSION_MAX_AGE, same_site="lax", https_only=https_only)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Runs before any body is read or any route/auth dependency runs."""
        if request.method not in SAFE_METHODS:
            # CSRF: SameSite=lax still sends the cookie from sibling subdomains, so check the origin
            o = request.headers.get("origin")
            if (o or "").rstrip("/") != origin and not (o is None and request.headers.get("sec-fetch-site") == "same-origin"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
            # size before parsing: FastAPI spools a multipart body before auth runs
            cap = upload_cap() if request.url.path == "/admin/upload" else BODY_CAP
            length = request.headers.get("content-length")
            if length is None or not length.isdigit() or int(length) > cap + 64 * 1024:
                return JSONResponse({"detail": f"body missing a length or larger than {cap} bytes"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(401)
    async def unauthorized(request: Request, exc):
        # browsers go to the login page; API clients keep the JSON 401. Not for /auth itself,
        # or a failing IdP would bounce the browser between /auth and /login forever.
        if "text/html" in request.headers.get("accept", "") and request.url.path != "/auth":
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=401)

    from app import review  # here: review imports require() from this module
    app.include_router(review.router)
    from app import clients
    app.include_router(clients.router)
    from app import search
    app.include_router(search.router)
    from app import render
    app.include_router(render.router)
    from app import sources
    app.include_router(sources.router)
    from app import research
    app.include_router(research.router)
    from app import audit
    app.include_router(audit.router)
    oauth = OAuth()
    app.state.oauth = oauth
    if all(os.environ.get(k) for k in ("OIDC_METADATA_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")):
        oauth.register(
            "oidc", server_metadata_url=os.environ["OIDC_METADATA_URL"],
            client_id=os.environ["OIDC_CLIENT_ID"], client_secret=os.environ["OIDC_CLIENT_SECRET"],
            client_kwargs={"scope": "openid profile"})

    def idp():
        if not hasattr(oauth, "oidc"):
            raise HTTPException(503, "OIDC is not configured (OIDC_METADATA_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET)")
        return oauth.oidc

    @app.get("/login")
    async def login(request: Request):
        # fixed callback in production: behind the ALB, url_for would build http:// unless
        # FORWARDED_ALLOW_IPS trusts the ALB
        redirect = os.environ.get("OIDC_REDIRECT_URI") or request.url_for("auth")
        return await idp().authorize_redirect(request, redirect)

    @app.get("/auth")
    async def auth(request: Request):
        try:
            token = await idp().authorize_access_token(request)  # validates signature, nonce, state, expiry
        except OAuthError:  # cancelled login, stale state
            raise HTTPException(401, "login failed; start again at /login")
        claims = token["userinfo"]
        claim = os.environ.get("OIDC_GROUPS_CLAIM", "groups")
        if claim in claims.get("_claim_names", {}):
            # Entra "groups overage" (>200 groups): no groups in the token, so no roles; fail loudly
            print(f"login refused for {claims['sub']}: groups overage", flush=True)
            raise HTTPException(403, "too many groups in the token; assign groups to the application in the IdP")
        groups = claims.get(claim, [])
        groups = {groups} if isinstance(groups, str) else set(groups)
        request.session.clear()  # fresh session on login
        request.session["user"] = {
            "sub": claims["sub"], "name": claims.get("name", claims["sub"]),
            "groups": sorted(groups & known_groups())}
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")  # POST: a cross-site link must not log people out
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/me")
    def me(user: User = Depends(current_user)):
        return {"sub": user.sub, "name": user.name, "roles": sorted(user.roles)}

    @app.post("/admin/upload", status_code=202)
    def upload(request: Request, file: UploadFile, user: User = Depends(require("admin"))):
        cap = upload_cap()  # the guard middleware already refused oversize bodies before parsing
        name = safe_name(file.filename)
        magic = UPLOAD_MAGIC.get(PurePosixPath(name).suffix.lower())
        if magic is None:
            raise HTTPException(400, f"allowed types: {', '.join(sorted(UPLOAD_MAGIC))}")
        data = file.file.read(cap + 1)
        if len(data) > cap:
            raise HTTPException(413, f"file larger than {cap} bytes")
        if not data.startswith(magic):
            raise HTTPException(400, "file content does not match its extension")
        with db.connect() as conn:
            src = conn.execute("select id, config from sources where kind='upload' and enabled "
                               "order by id limit 1").fetchone()
            if not src:
                raise HTTPException(400, "no enabled upload source is configured")
            prefix = src[1].get("prefix", "")
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            boto3.client("s3").put_object(
                Bucket=src[1]["bucket"], Key=f"{prefix}{uuid.uuid4().hex}-{name}", Body=data)
            job_id = conn.execute(
                "insert into jobs(kind, payload) values ('crawl_s3', jsonb_build_object('source_id', %s::bigint)) "
                "returning id", (src[0],)).fetchone()[0]
        return JSONResponse({"job_id": job_id}, status_code=202)

    return app
