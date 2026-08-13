from sqlalchemy import select
from werkzeug.security import check_password_hash

from app.config import get_settings
from app.models import AdminUser


def test_settings_requires_login(client):
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 303


def test_settings_page_renders(auth_client):
    resp = auth_client.get("/settings")
    assert resp.status_code == 200
    assert "비밀번호 변경" in resp.text


def test_change_password_wrong_current(auth_client):
    resp = auth_client.post(
        "/settings",
        data={
            "current_password": "wrong",
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
    )
    assert resp.status_code == 400
    assert "현재 비밀번호" in resp.text


def test_change_password_too_short(auth_client):
    settings = get_settings()
    resp = auth_client.post(
        "/settings",
        data={
            "current_password": settings.admin_password,
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert resp.status_code == 400
    assert "8자 이상" in resp.text


def test_change_password_mismatch(auth_client):
    settings = get_settings()
    resp = auth_client.post(
        "/settings",
        data={
            "current_password": settings.admin_password,
            "new_password": "newpass123",
            "confirm_password": "different123",
        },
    )
    assert resp.status_code == 400
    assert "일치하지 않습니다" in resp.text


def test_change_password_success(auth_client, db):
    settings = get_settings()
    resp = auth_client.post(
        "/settings",
        data={
            "current_password": settings.admin_password,
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
    )
    assert resp.status_code == 200
    assert "변경되었습니다" in resp.text
    user = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
    assert user is not None
    assert check_password_hash(user.password_hash, "newpass123")
