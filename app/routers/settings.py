from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth import current_user, require_login
from app.db import get_db
from app.logging import get_logger
from app.models import AdminUser
from app.template_utils import render

router = APIRouter(prefix="/settings", dependencies=[Depends(require_login)], tags=["settings"])


@router.get("")
def settings_page(request: Request) -> Response:
    return render(request, "settings.html")


@router.post("")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    username = current_user(request)
    if username is None:
        return RedirectResponse("/login", status_code=303)
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not check_password_hash(user.password_hash, current_password):
        return render(
            request,
            "settings.html",
            {"error": "현재 비밀번호가 올바르지 않습니다."},
            400,
        )
    if len(new_password) < 8:
        return render(
            request, "settings.html", {"error": "새 비밀번호는 8자 이상이어야 합니다."}, 400
        )
    if new_password != confirm_password:
        return render(
            request, "settings.html", {"error": "새 비밀번호 확인이 일치하지 않습니다."}, 400
        )
    user.password_hash = generate_password_hash(new_password)
    db.commit()
    get_logger().info("관리자 비밀번호 변경: %s", username)
    return render(request, "settings.html", {"message": "비밀번호가 변경되었습니다."})
