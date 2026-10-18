import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from werkzeug.security import check_password_hash

from app import db as db_module
from app.auth import UPLOAD_FORM_PATHS, verify_csrf
from app.config import DEFAULT_ADMIN_PASSWORD, get_settings
from app.db import ensure_default_database, init_db
from app.logging import get_logger, log_security_warnings
from app.models import AdminUser
from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import drafts as drafts_router
from app.routers import home as home_router
from app.routers import ledger as ledger_router
from app.routers import monthly as monthly_router
from app.routers import people as people_router
from app.routers import settings as settings_router
from app.routers import teams as teams_router
from app.services.drafts import clean_expired, cleanup_loop
from app.services.rate_limit import login_limiter
from app.services.vision import shutdown_vision

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cleanup_task: asyncio.Task[None] | None = None
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
        clean_expired()
        cleanup_task = asyncio.create_task(cleanup_loop())
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        shutdown_vision()
        db_module.engine.dispose()


MIB = 1024 * 1024
DEFAULT_BODY_LIMIT = 20 * MIB
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def _too_large_response(scope: Scope, limit: int) -> Response:
    """브라우저 폼에는 스크립트 없는 안내 화면, 스크립트 요청에는 JSON 오류를 준다."""
    headers = {"Cache-Control": "no-store", **SECURITY_HEADERS}
    accept = next(
        (value.decode("latin-1") for key, value in scope["headers"] if key == b"accept"), ""
    )
    if scope["path"] in UPLOAD_FORM_PATHS:
        each = get_settings().max_upload_mb
        message = f"요청서 사진과 개인번호 명단 사진은 각각 최대 {each}MB까지 올릴 수 있습니다."
        back = "/monthly"
    else:
        message = f"요청 본문이 너무 큽니다. (최대 {limit // MIB}MB)"
        back = "/"
    if "text/html" in accept:
        page = (
            '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>파일이 너무 큽니다 - PointBook</title></head>"
            '<body style="font-family:sans-serif;padding:24px;line-height:1.7">'
            f"<h1>파일이 너무 큽니다</h1><p>{message}</p>"
            "<p>사진 크기를 줄이거나 한 장씩 다시 선택해 주세요.</p>"
            f'<p><a href="{back}">돌아가기</a></p></body></html>'
        )
        return HTMLResponse(page, status_code=413, headers=headers)
    return JSONResponse({"detail": message, "error": message}, status_code=413, headers=headers)


class RequestSizeLimitMiddleware:
    """Bound the complete request before multipart/form parsing, including chunked uploads."""

    def __init__(
        self,
        app: ASGIApp,
        default_limit: int = DEFAULT_BODY_LIMIT,
        path_limits: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.path_limits = path_limits or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope["path"], self.default_limit)
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await _too_large_response(scope, limit)(scope, receive, send)
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
        user_agent = request.headers.get("user-agent", "")
        if ("Trident/" in user_agent or "MSIE " in user_agent) and request.url.path != "/health":
            return HTMLResponse(
                (BASE_DIR / "templates" / "legacy_browser.html").read_text(),
                status_code=426,
                headers={"Cache-Control": "no-store", **SECURITY_HEADERS},
            )
        response: Response = await call_next(request)
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        response.headers.update(SECURITY_HEADERS)
        return response

    # 월간 업로드는 두 파일을 받으므로 파일별 한도의 두 배와 폼 여유분을 허용한다.
    upload_limit = max(DEFAULT_BODY_LIMIT, (2 * settings.max_upload_mb + 1) * MIB)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        path_limits=dict.fromkeys(UPLOAD_FORM_PATHS, upload_limit),
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
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
    app.include_router(drafts_router.router)
    return app


app = create_app()
