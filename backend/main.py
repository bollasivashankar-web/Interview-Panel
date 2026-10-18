import hashlib
import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.api import router
from backend.config import get_settings
from backend.database import Base, engine


logger = logging.getLogger("interview_platform.security")
settings = get_settings()
redis = Redis.from_url(settings.redis_url, decode_responses=True)
request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def rate_policy(path: str) -> tuple[str, int]:
    if path.startswith("/api/v1/results/") and path.endswith("/evaluate"):
        return "ai-evaluation", 10
    if path.endswith("/questions/generate"):
        return "ai-generation", 10
    if path.endswith("/questions/extract-pdf"):
        return "pdf-extraction", 10
    if path == "/api/v1/invitations":
        return "invitations", 20
    return "api", settings.rate_limit_per_minute


async def rate_limited(request: Request) -> bool:
    if settings.app_env == "test" or not request.url.path.startswith("/api/"):
        return False
    group, limit = rate_policy(request.url.path)
    client = request.client.host if request.client else "unknown"
    identity = hashlib.sha256(client.encode()).hexdigest()[:24]
    key = f"rate:{group}:{identity}"
    try:
        count = await redis.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return n",
            1,
            key,
            60,
        )
    except RedisError:
        logger.error("rate_limit_store_unavailable")
        if settings.app_env == "production":
            raise
        return False
    return int(count) > limit


def apply_security_headers(response, request_id: str, api_request: bool, path: str = ""):
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if path in {"/docs", "/docs/oauth2-redirect", "/redoc"}:
        # FastAPI's documentation uses CDN assets and inline initialization scripts.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            "script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
            "connect-src 'self'"
        )
    if api_request:
        response.headers["Cache-Control"] = "no-store"
    return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().app_env == "test":
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Interview Platform API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
)


@app.middleware("http")
async def security_controls(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_request_id if request_id_pattern.fullmatch(supplied_request_id) else str(uuid.uuid4())
    api_request = request.url.path.startswith("/api/")
    try:
        if await rate_limited(request):
            logger.warning("request_throttled request_id=%s", request_id)
            response = JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
            return apply_security_headers(response, request_id, api_request, request.url.path)
    except RedisError:
        response = JSONResponse({"detail": "Request protection unavailable"}, status_code=503)
        return apply_security_headers(response, request_id, api_request, request.url.path)
    request_limit = (
        settings.max_pdf_request_bytes
        if request.url.path.endswith("/questions/extract-pdf")
        else settings.max_request_bytes
    )
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
            too_large = declared_size < 0 or declared_size > request_limit
        except ValueError:
            too_large = True
        if too_large:
            response = JSONResponse({"detail": "Request body too large"}, status_code=413)
            return apply_security_headers(response, request_id, api_request, request.url.path)
    if request.method in {"POST", "PUT", "PATCH"}:
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > request_limit:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                return apply_security_headers(response, request_id, api_request, request.url.path)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("request_failed request_id=%s exception_type=%s", request_id, type(exc).__name__)
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    if response.status_code in {401, 403, 429}:
        logger.warning(
            "request_denied request_id=%s method=%s path=%s status=%s client=%s",
            request_id,
            request.method,
            request.scope.get("route").path if request.scope.get("route") else "unmatched",
            response.status_code,
            hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()[:24],
        )
    return apply_security_headers(response, request_id, api_request, request.url.path)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    return {"status": "ready"}


app.include_router(router)
