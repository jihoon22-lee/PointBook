from app.config import DEFAULT_ADMIN_PASSWORD, DEFAULT_SECRET_KEY, Settings, get_settings
from app.services.rate_limit import LoginRateLimiter


def test_security_warnings_flag_defaults():
    settings = Settings(secret_key=DEFAULT_SECRET_KEY, admin_password=DEFAULT_ADMIN_PASSWORD)
    warnings = settings.security_warnings()
    assert any("SECRET_KEY" in w for w in warnings)
    assert any("ADMIN_PASSWORD" in w for w in warnings)


def test_security_warnings_empty_when_customized():
    settings = Settings(secret_key="custom-secret", admin_password="custom-pass")
    assert settings.security_warnings() == []


def test_security_warnings_gemini_without_key():
    settings = Settings(secret_key="x", admin_password="y", ai_provider="gemini", gemini_api_key="")
    assert any("GEMINI_API_KEY" in w for w in settings.security_warnings())


def test_rate_limiter_locks_after_max_attempts():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("admin", "1.2.3.4")
    assert limiter.locked_for("admin", "1.2.3.4") > 0


def test_rate_limiter_reset_on_success():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("admin", "1.2.3.4")
    limiter.reset("admin", "1.2.3.4")
    assert limiter.locked_for("admin", "1.2.3.4") == 0


def test_rate_limiter_keys_by_username_and_ip():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("admin", "1.1.1.1")
    limiter.record_failure("admin", "2.2.2.2")
    assert limiter.locked_for("admin", "1.1.1.1") == 0


def test_login_lockout_after_repeated_failures(client):
    for _ in range(get_settings().login_max_attempts):
        resp = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert resp.status_code == 400
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 429
    assert "너무 많습니다" in resp.text


def test_login_success_after_failures_not_locked(client):
    settings = get_settings()
    for _ in range(settings.login_max_attempts - 1):
        client.post("/login", data={"username": settings.admin_username, "password": "wrong"})
    resp = client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_upload_unsupported_ext_rejected(auth_client):
    resp = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.txt", b"fake", "text/plain")},
    )
    assert resp.status_code == 400
    assert "지원하지 않는 이미지 형식" in resp.text


def test_upload_oversized_file_rejected(auth_client, monkeypatch):
    from app.routers import monthly as monthly_router

    monkeypatch.setattr(monthly_router, "get_settings", lambda: Settings(max_upload_mb=1))
    resp = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.png", b"\x00" * (1024 * 1024 + 1), "image/png")},
    )
    assert resp.status_code == 400
    assert "너무 큽니다" in resp.text


def test_session_cookie_flags(client):
    settings = get_settings()
    resp = client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False,
    )
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert "samesite=lax" in set_cookie
    assert "httponly" in set_cookie
