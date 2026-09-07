import secrets
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.db import get_db
from app.models import AdminUser

SESSION_KEY = "admin_username"


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def verify_csrf(request: Request) -> AsyncIterator[None]:
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = request.session.get("csrf_token")
            supplied: object = request.headers.get("x-csrf-token")
            if supplied is None:
                form = await request.form(
                    max_files=1, max_fields=30000, max_part_size=2 * 1024 * 1024
                )
                supplied = form.get("csrf_token")
            if (
                not isinstance(expected, str)
                or not isinstance(supplied, str)
                or not expected.isascii()
                or not supplied.isascii()
                or not secrets.compare_digest(expected, supplied)
            ):
                raise HTTPException(
                    403, "요청 보안 토큰이 만료되었거나 일치하지 않습니다. 다시 로그인하세요."
                )
        yield
    finally:
        await request.close()


def login_user(request: Request, user: AdminUser) -> None:
    request.session.clear()
    request.session.update(
        {SESSION_KEY: user.username, "admin_id": user.id, "auth_version": user.auth_version}
    )
    csrf_token(request)


def logout_user(request: Request) -> None:
    request.session.clear()


def _validated_user(request: Request, db: Session) -> str | None:
    admin_id = request.session.get("admin_id")
    version = request.session.get("auth_version")
    if not isinstance(admin_id, int) or not isinstance(version, int):
        return None
    user = db.scalar(select(AdminUser).where(AdminUser.id == admin_id))
    if user is None or user.auth_version != version:
        request.session.clear()
        return None
    return user.username


def current_user(request: Request) -> str | None:
    with db_module.SessionLocal() as db:
        return _validated_user(request, db)


def require_login(request: Request, db: Session = Depends(get_db)) -> None:
    if _validated_user(request, db) is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
