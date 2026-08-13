from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from app.auth import current_user, login_user, logout_user
from app.db import get_db
from app.models import AdminUser
from app.services.rate_limit import login_limiter
from app.template_utils import render

router = APIRouter(tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.get("/login")
def login_page(request: Request) -> Response:
    if current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    username = username.strip()
    ip = _client_ip(request)
    remaining = login_limiter.locked_for(username, ip)
    if remaining > 0:
        return render(
            request,
            "login.html",
            {"error": f"로그인 시도가 너무 많습니다. {remaining}초 후 다시 시도해 주세요."},
            429,
        )
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is not None and check_password_hash(user.password_hash, password):
        login_limiter.reset(username, ip)
        login_user(request, user.username)
        return RedirectResponse("/", status_code=303)
    login_limiter.record_failure(username, ip)
    return render(
        request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, 400
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse("/login", status_code=303)
