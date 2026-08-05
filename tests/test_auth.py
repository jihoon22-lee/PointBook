from app.config import get_settings


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "로그인" in resp.text


def test_login_wrong_password(client):
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 400
    assert "올바르지 않습니다" in resp.text


def test_login_unknown_user(client):
    resp = client.post("/login", data={"username": "nobody", "password": "x"})
    assert resp.status_code == 400


def test_login_success_redirects_home(client):
    settings = get_settings()
    resp = client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_home_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_home_after_login(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert "PointBook" in resp.text


def test_login_page_redirects_when_logged_in(auth_client):
    resp = auth_client.get("/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_logout(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
    )
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
