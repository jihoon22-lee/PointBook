import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app import db as db_module
from app.config import get_settings
from app.main import app
from app.models import AdminUser


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.configure_database(f"sqlite:///{db_path}")
    db_module.Base.metadata.create_all(db_module.engine)
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
        yield c


@pytest.fixture()
def auth_client(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
    )
    return client
