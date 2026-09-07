from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from werkzeug.security import check_password_hash

from app import db as db_module
from app.auth import verify_csrf
from app.config import DEFAULT_ADMIN_PASSWORD, get_settings
from app.db import ensure_default_database, init_db
from app.logging import get_logger, log_security_warnings
from app.models import AdminUser
from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import home as home_router
from app.routers import ledger as ledger_router
from app.routers import monthly as monthly_router
from app.routers import people as people_router
from app.routers import settings as settings_router
from app.routers import teams as teams_router
from app.services.rate_limit import login_limiter
from app.services.vision import shutdown_vision

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_runtime()
    log_security_warnings(
        [warning for warning in settings.security_warnings() if "ADMIN_PASSWORD" not in warning]
    )
    login_limiter.max_attempts = settings.login_max_attempts
    login_limiter.window_seconds = settings.login_lockout_seconds
    get_logger().info("PointBook 서버 시작")
    ensure_default_database()
    try:
        init_db()
        if settings.app_env == "production" or settings.enforce_secure_defaults:
            with db_module.SessionLocal() as db:
                for user in db.scalars(select(AdminUser)):
                    if check_password_hash(
                        user.password_hash, DEFAULT_ADMIN_PASSWORD
                    ) or check_password_hash(user.password_hash, ""):
                        raise RuntimeError("운영 관리자 DB의 기본 또는 빈 암호를 변경해야 합니다.")
        yield
    finally:
        shutdown_vision()
        db_module.engine.dispose()


class RequestSizeLimitMiddleware:
    """Bound the complete request before multipart/form parsing, including chunked uploads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > 20 * 1024 * 1024:
                await JSONResponse({"detail": "요청 본문이 너무 큽니다."}, status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, bounded_receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PointBook", lifespan=lifespan, dependencies=[Depends(verify_csrf)])
    expected_revision = ScriptDirectory.from_config(db_module._alembic_config()).get_current_head()

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        try:
            with db_module.SessionLocal() as db:
                db_module.validate_schema(db.connection(), full=False)
                revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                admin = db.execute(select(AdminUser.id, AdminUser.auth_version).limit(1)).first()
                if revision != expected_revision or admin is None:
                    return JSONResponse({"status": "unavailable"}, status_code=503)
            return JSONResponse({"status": "ready"})
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.middleware("http")
    async def no_cache(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=60 * 60 * 24 * 7,
        same_site=settings.cookie_samesite,
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(auth_router.router)
    app.include_router(home_router.router)
    app.include_router(people_router.router)
    app.include_router(teams_router.router)
    app.include_router(monthly_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(settings_router.router)
    app.include_router(ledger_router.router)
    return app


app = create_app()
