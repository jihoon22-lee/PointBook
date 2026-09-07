"""실제 HTTP 및 합성 DB로 R10–R12/S07–S08을 검증한다."""

import json
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from app import db as db_module
from app.config import DEFAULT_ADMIN_PASSWORD, DEFAULT_SECRET_KEY, Settings, get_settings
from app.main import create_app
from app.models import AdminUser
from app.services.rate_limit import LoginRateLimiter
from scripts.init_db import ensure_admin


def _credentials():
    settings = get_settings()
    return {"username": settings.admin_username, "password": settings.admin_password}


@pytest.mark.parametrize(
    "path", ["/login", "/logout", "/settings", "/teams", "/people/new", "/monthly/confirm"]
)
def test_all_write_routes_reject_missing_csrf(client, path):
    response = client.raw_request("POST", path, data={})
    assert response.status_code == 403


def test_wrong_csrf_and_old_token_after_login(client):
    page = client.get("/login")
    import re

    token = re.search(r'name="csrf_token" value="([^\"]+)"', page.text).group(1)
    assert (
        client.raw_request(
            "POST", "/login", data={**_credentials(), "csrf_token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.raw_request(
            "POST", "/login", data={**_credentials(), "csrf_token": token}
        ).status_code
        == 200
    )
    assert client.raw_request("POST", "/logout", data={"csrf_token": token}).status_code == 403


def test_password_change_revokes_old_cookie_and_allows_new_login(auth_client):
    old_cookie = auth_client.cookies.get("session")
    settings = get_settings()
    response = auth_client.post(
        "/settings",
        data={
            "current_password": settings.admin_password,
            "new_password": "changed-password",
            "confirm_password": "changed-password",
        },
    )
    assert response.status_code == 200
    with TestClient(auth_client.app) as old:
        old.cookies.set("session", old_cookie)
        assert old.get("/", follow_redirects=False).status_code == 303
    assert auth_client.get("/", follow_redirects=False).status_code == 303
    assert (
        auth_client.post(
            "/login", data={"username": settings.admin_username, "password": "changed-password"}
        ).status_code
        == 200
    )


def test_deleted_admin_is_not_authenticated(auth_client, db):
    user = db.scalar(select(AdminUser))
    db.delete(user)
    db.commit()
    assert auth_client.get("/", follow_redirects=False).status_code == 303


def test_forged_username_only_session_is_not_authenticated(client):
    payload = b64encode(json.dumps({"admin_username": get_settings().admin_username}).encode())
    cookie = TimestampSigner(get_settings().secret_key).sign(payload).decode()
    client.cookies.set("session", cookie)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_expired_cookie_is_rejected(auth_client, monkeypatch):
    cookie = auth_client.cookies.get("session")
    from itsdangerous import timed

    original = timed.time.time
    monkeypatch.setattr(timed.time, "time", lambda: original() + 8 * 24 * 3600)
    auth_client.cookies.set("session", cookie)
    assert auth_client.get("/", follow_redirects=False).status_code == 303


def test_forwarded_for_does_not_bypass_login_limit(client):
    for index in range(get_settings().login_max_attempts):
        assert (
            client.post(
                "/login",
                data={"username": "admin", "password": "bad"},
                headers={"X-Forwarded-For": f"10.0.0.{index}"},
            ).status_code
            == 400
        )
    assert (
        client.post(
            "/login",
            data={"username": "admin", "password": "bad"},
            headers={"X-Forwarded-For": "8.8.8.8"},
        ).status_code
        == 429
    )


def test_ip_limit_covers_rotating_usernames_and_is_bounded():
    limiter = LoginRateLimiter(max_attempts=3, max_keys=10)
    for i in range(3):
        limiter.record_failure(f"name{i}", "same-ip")
    assert limiter.locked_for("another", "same-ip") > 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: limiter.record_failure("admin", str(i)), range(100)))
    assert len(limiter._failures) <= 10


def test_rate_limit_expired_keys_removed(monkeypatch):
    from app.services import rate_limit

    limiter = LoginRateLimiter(window_seconds=10)
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 100)
    limiter.record_failure("a", "ip")
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 111)
    assert limiter.locked_for("a", "ip") == 0
    assert not limiter._failures


@pytest.mark.parametrize("secret", ["", " ", DEFAULT_SECRET_KEY])
def test_production_rejects_empty_and_default_secret(secret):
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(
            app_env="production",
            secret_key=secret,
            ai_provider="gemini",
            gemini_api_key="synthetic",
        ).validate_runtime()


@pytest.mark.parametrize("provider", ["", "typo", "openai"])
def test_unknown_provider_is_not_mock(provider):
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        Settings(ai_provider=provider).validate_runtime()


def test_production_mock_rejected():
    with pytest.raises(ValueError, match="Mock"):
        Settings(
            app_env="production", secret_key="synthetic-secret", ai_provider="mock"
        ).validate_runtime()


def test_production_existing_changed_admin_ignores_initial_password(client, monkeypatch):
    settings = Settings(
        app_env="production",
        secret_key="synthetic-secret",
        ai_provider="gemini",
        gemini_api_key="synthetic",
        admin_password=DEFAULT_ADMIN_PASSWORD,
    )
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with db_module.SessionLocal() as db:
        user = db.scalar(select(AdminUser))
        user.password_hash = generate_password_hash("changed-database-password")
        db.commit()
    with TestClient(create_app()) as secure_client:
        assert secure_client.get("/login").status_code == 200


def test_production_default_database_password_rejected(client, monkeypatch):
    from app import main

    settings = Settings(
        app_env="production",
        secret_key="synthetic-secret",
        ai_provider="gemini",
        gemini_api_key="synthetic",
        admin_password="custom-initial",
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with db_module.SessionLocal() as db:
        user = db.scalar(select(AdminUser))
        user.password_hash = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
        db.commit()
    with pytest.raises(RuntimeError, match="관리자 DB"), TestClient(create_app()):
        pass


def test_initial_production_password_validated_only_when_creating(client, monkeypatch):
    from scripts import init_db

    settings = Settings(app_env="production", admin_password=DEFAULT_ADMIN_PASSWORD)
    monkeypatch.setattr(init_db, "get_settings", lambda: settings)
    assert "이미 존재" in ensure_admin()
    with db_module.SessionLocal() as db:
        db.execute(text("DELETE FROM admin_users"))
        db.commit()
    with pytest.raises(ValueError, match="초기 암호"):
        ensure_admin()


def test_secure_cookie_policy(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: Settings(cookie_secure=True))
    with TestClient(create_app(), base_url="https://testserver") as secure:
        response = secure.get("/login")
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    with pytest.raises(ValueError, match="SameSite"):
        Settings(cookie_samesite="none", cookie_secure=False)


def test_request_limit_applies_to_streamed_body(client):
    response = client.raw_request(
        "POST", "/login", content=(b"x" * (1024 * 1024) for _ in range(21))
    )
    assert response.status_code == 413


def test_csrf_accepts_more_than_default_thousand_fields(client):
    page = client.get("/login")
    import re

    token = re.search(r'name="csrf_token" value="([^\"]+)"', page.text).group(1)
    fields = {f"field_{i}": (None, "value") for i in range(1500)}
    fields["csrf_token"] = (None, token)
    fields.update({key: (None, value) for key, value in _credentials().items()})
    assert (
        client.raw_request("POST", "/logout", files=fields, follow_redirects=False).status_code
        == 303
    )


def test_health_checks_database(client):
    assert client.get("/health").json() == {"status": "ready"}
    with db_module.engine.begin() as conn:
        conn.execute(text("DROP TABLE admin_users"))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_password_change_detects_concurrent_version_change(auth_client, monkeypatch):
    from app.routers import settings as settings_router

    original_hash = settings_router.generate_password_hash

    def concurrent_change(password):
        hashed = original_hash(password)
        with db_module.SessionLocal() as db:
            db.execute(text("UPDATE admin_users SET auth_version = auth_version + 1"))
            db.commit()
        return hashed

    monkeypatch.setattr(settings_router, "generate_password_hash", concurrent_change)
    response = auth_client.post(
        "/settings",
        data={
            "current_password": get_settings().admin_password,
            "new_password": "changed-password",
            "confirm_password": "changed-password",
        },
    )
    assert response.status_code == 409
    from werkzeug.security import check_password_hash

    with db_module.SessionLocal() as db:
        user = db.scalar(select(AdminUser))
        assert check_password_hash(user.password_hash, get_settings().admin_password)


def test_health_rejects_missing_admin(client):
    with db_module.engine.begin() as conn:
        conn.execute(text("DELETE FROM admin_users"))
    assert client.get("/health").status_code == 503


def test_missing_gemini_key_fails_closed():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(ai_provider="gemini", gemini_api_key=" ").validate_runtime()


def test_non_ascii_csrf_is_forbidden_not_server_error(client):
    client.get("/login")
    response = client.raw_request(
        "POST", "/login", data={**_credentials(), "csrf_token": "잘못된토큰"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize("table", ["people", "teams", "monthly_snapshots", "balance_records"])
def test_health_rejects_missing_core_table(client, table):
    with db_module.engine.begin() as conn:
        conn.execute(text(f'DROP TABLE "{table}"'))
    assert client.get("/health").status_code == 503


def test_health_rejects_missing_required_column(client):
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE people DROP COLUMN grade"))
    assert client.get("/health").status_code == 503


def test_startup_rejects_versioned_schema_drift(client):
    with db_module.engine.begin() as conn:
        conn.execute(text("DROP TABLE balance_records"))
    with pytest.raises(RuntimeError, match="테이블"), TestClient(create_app()):
        pass


def test_initializer_rejects_runtime_config_before_migration(client, monkeypatch):
    from scripts import init_db

    settings = Settings(
        app_env="production",
        secret_key=DEFAULT_SECRET_KEY,
        ai_provider="gemini",
        gemini_api_key="synthetic",
    )
    monkeypatch.setattr(init_db, "get_settings", lambda: settings)
    reached_migration = []
    monkeypatch.setattr(db_module, "configure_database", lambda url: reached_migration.append(url))
    with pytest.raises(ValueError, match="SECRET_KEY"):
        init_db.main()
    assert reached_migration == []
