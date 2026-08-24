import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import List
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .database import get_db
from .observability import record_exception, record_request
from .ops_static import (
    OpsStaticBundle,
    OpsStaticError,
    load_fixed_ops_static_bundle,
)
from .readiness import OPS_API_READINESS_QUERIES, assert_database_ready
from .crawler_control_database import assert_required_crawler_control_ready
from .routers import (
    auth,
    bug_reports,
    courses,
    crawler_analytics,
    crawler_releases,
    crawler_studio,
    locations,
    ops_auth,
    ops_v2,
    seo_pages,
    server_monitor,
    user_courses,
    visitor_analytics,
)

logger = logging.getLogger(__name__)
auth.validate_auth_configuration()

_production = os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}
_api_profile = os.getenv("MOONCEN_API_PROFILE", "combined").strip().lower()
if _api_profile not in {"combined", "public", "ops"}:
    raise RuntimeError("MOONCEN_API_PROFILE must be combined, public, or ops")
_ops_static_bundle: OpsStaticBundle | None = None


def load_ops_static_at_startup() -> None:
    """Fail startup when the trusted Ops origin cannot serve its reviewed SPA."""

    global _ops_static_bundle
    if _api_profile != "ops":
        return
    try:
        _ops_static_bundle = load_fixed_ops_static_bundle()
    except OpsStaticError as exc:
        raise RuntimeError("reviewed Ops static bundle is unavailable") from exc


@asynccontextmanager
async def _lifespan(_application: FastAPI):
    load_ops_static_at_startup()
    yield


app = FastAPI(
    title="MoonCen Ops Control API" if _api_profile == "ops" else "MoonCen API",
    version="0.2.0",
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    openapi_url=None if _production else "/openapi.json",
    lifespan=_lifespan,
)

# The default remains combined for the existing production package.  an2p
# explicitly runs separate public-development and Ops-control processes so a
# development browser cannot accidentally enqueue a production operation.
if _api_profile in {"combined", "public"}:
    # Canonical JSON API routes. Public app/SEO paths stay outside this namespace.
    app.include_router(courses.router, prefix="/api")
    app.include_router(locations.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(bug_reports.router, prefix="/api")
    app.include_router(user_courses.router, prefix="/api")
    app.include_router(user_courses.api_router)
    app.include_router(seo_pages.router)
    app.include_router(server_monitor.router)
if _api_profile in {"combined", "ops"}:
    if _api_profile == "ops":
        app.include_router(ops_auth.router, prefix="/api")
    app.include_router(ops_v2.router)
    app.include_router(crawler_analytics.router)
    app.include_router(crawler_releases.router)
    app.include_router(crawler_studio.router)
    app.include_router(visitor_analytics.router)

def _cors_origins() -> List[str]:
    configured = os.getenv("MOONCEN_CORS_ORIGINS")
    if configured:
        origins = []
        production = os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}
        for raw_origin in configured.split(","):
            origin = raw_origin.strip().rstrip("/")
            if not origin:
                continue
            try:
                parsed = urlsplit(origin)
                _ = parsed.port
            except ValueError:
                continue
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                continue
            if production and parsed.scheme != "https":
                continue
            origins.append(origin)
        return list(dict.fromkeys(origins))
    if os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}:
        return []
    return [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:8080",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:4174",
    ]


def _cors_origin_regex() -> str | None:
    if os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}:
        return None
    return os.getenv(
        "MOONCEN_CORS_ORIGIN_REGEX",
        r"^http://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+):\d+$",
    )


def _trusted_hosts() -> list[str]:
    configured = os.getenv("MOONCEN_TRUSTED_HOSTS", "")
    candidates = [item.strip().lower().rstrip(".") for item in configured.split(",")]
    if not any(candidates):
        site = os.getenv("VITE_SITE_URL") or os.getenv("SITE_URL") or "https://mooncen.kr"
        try:
            hostname = urlsplit(site).hostname or ""
        except ValueError:
            hostname = ""
        candidates = [hostname.lower().rstrip(".")]
    candidates.extend(["localhost", "127.0.0.1", "[::1]"])
    if not _production:
        candidates.append("testserver")

    allowed: list[str] = []
    for host in candidates:
        if not host or len(host) > 253:
            continue
        if host.startswith("*."):
            suffix = host[2:]
            if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", suffix):
                allowed.append(host)
            continue
        if host == "[::1]" or re.fullmatch(r"[a-z0-9](?:[a-z0-9.:-]*[a-z0-9])?", host):
            allowed.append(host)
    return list(dict.fromkeys(allowed))


@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", uuid4().hex)
    logger.exception(
        "Unhandled API exception request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if supplied.isascii() and supplied.replace("-", "").isalnum() and len(supplied) <= 64 else uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration = time.perf_counter() - started
        record_request(request.url.path, 500, duration)
        record_exception(request.url.path, exc)
        raise
    duration = time.perf_counter() - started
    record_request(request.url.path, response.status_code, duration)
    if request.url.path not in {"/health", "/live"}:
        logger.info(
            "API request request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration * 1_000,
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{duration * 1_000:.1f}"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_origin_regex(),
    # The OAuth state endpoint uses an HttpOnly SameSite cookie. Origins are
    # exact allowlisted in production, so credentialed CORS remains bounded.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts())


def _ops_static_response(path: str, request: Request):
    if _ops_static_bundle is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Ops interface is not ready"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        return _ops_static_bundle.response(path, head=request.method == "HEAD")
    except OpsStaticError:
        logger.error("Reviewed Ops static bundle changed after startup", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Ops interface integrity check failed"},
            headers={"Cache-Control": "no-store"},
        )


@app.api_route("/", methods=["GET", "HEAD"])
def read_root(request: Request):
    if _api_profile == "ops":
        return _ops_static_response("", request)
    return {
        "message": "Welcome to MoonCen API",
        "profile": _api_profile,
    }


@app.get("/health")
@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        queries = (
            OPS_API_READINESS_QUERIES
            if _api_profile == "ops"
            else None
        )
        if queries is None:
            assert_database_ready(db)
        else:
            assert_database_ready(db, queries=queries)
        if _api_profile in {"combined", "ops"}:
            assert_required_crawler_control_ready()
    except Exception:
        logger.warning("Readiness check failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service not ready") from None
    return {"status": "ready"}


@app.get("/live")
def liveness_check():
    return {"status": "alive"}


@app.api_route("/{static_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def read_ops_static(static_path: str, request: Request):
    if _api_profile != "ops":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return _ops_static_response(static_path, request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8001")),
        reload=not _production and os.getenv("API_RELOAD", "false").strip().lower() == "true",
    )
