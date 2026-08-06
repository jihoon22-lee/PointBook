from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from app.auth import current_user, login_user, logout_user
from app.db import get_db
from app.models import AdminUser
from app.template_utils import render

router = APIRouter(tags=["auth"])


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
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is not None and check_password_hash(user.password_hash, password):
        login_user(request, user.username)
        return RedirectResponse("/", status_code=303)
    return render(
        request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, 400
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse("/login", status_code=303)
