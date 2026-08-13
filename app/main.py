from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import ensure_default_database, init_db
from app.logging import get_logger, log_security_warnings
from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import home as home_router
from app.routers import monthly as monthly_router
from app.routers import people as people_router
from app.routers import settings as settings_router
from app.routers import teams as teams_router
from app.services.rate_limit import login_limiter

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log_security_warnings(settings.security_warnings())
    if settings.enforce_secure_defaults and settings.security_warnings():
        raise RuntimeError(
            "보안 기본값이 사용 중입니다. .env에서 SECRET_KEY/ADMIN_PASSWORD를 변경하세요."
        )
    login_limiter.max_attempts = settings.login_max_attempts
    login_limiter.window_seconds = settings.login_lockout_seconds
    get_logger().info("PointBook 서버 시작")
    ensure_default_database()
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PointBook", lifespan=lifespan)

    @app.middleware("http")
    async def no_cache(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

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
    return app


app = create_app()
