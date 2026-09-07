import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app import db as db_module
from app.config import get_settings
from app.main import app
from app.models import AdminUser
from app.services.rate_limit import login_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    login_limiter.clear_all()
    yield
    login_limiter.clear_all()


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.configure_database(f"sqlite:///{db_path}")
    db_module.run_migrations()
    settings = get_settings()
    with Session(db_module.engine) as db:
        db.add(
            AdminUser(
                username=settings.admin_username,
                password_hash=generate_password_hash(settings.admin_password),
            )
        )
        db.commit()
    with TestClient(app) as c:
        # Existing business tests submit valid browser requests. Security tests can
        # use raw_request explicitly to exercise absent/invalid tokens.
        c.raw_request = c.request

        def csrf_request(method, url, **kwargs):
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                page = c.raw_request("GET", "/login")
                match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
                headers = dict(kwargs.pop("headers", {}) or {})
                if match:
                    headers.setdefault("X-CSRF-Token", match.group(1))
                kwargs["headers"] = headers
            return c.raw_request(method, url, **kwargs)

        c.request = csrf_request
        yield c
    db_module.engine.dispose()


@pytest.fixture()
def auth_client(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
    )
    return client


@pytest.fixture()
def db():
    with Session(db_module.engine) as session:
        yield session
