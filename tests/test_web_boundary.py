import html
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from tests.factories import make_team

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


def test_team_name_cannot_escape_delete_confirmation(auth_client, db):
    name = "x'+(localStorage.pbx=1)+'"
    make_team(db, name)
    page = auth_client.get("/teams").text
    form = re.search(r'<form method="post" action="/teams/\d+/delete"[^>]*>', page).group(0)
    assert "onsubmit" not in form
    message = html.unescape(re.search(r'data-confirm="([^"]*)"', form).group(1))
    assert message.startswith(f"팀 '{name}' 을(를) 삭제할까요?")


def test_templates_do_not_interpolate_inside_inline_handlers():
    offenders = [
        path.name
        for path in TEMPLATES.glob("*.html")
        if re.search(r'\son[a-z]+="[^"]*\{\{', path.read_text())
    ]
    assert offenders == []


def test_confirmations_use_shared_script():
    assert "/static/js/confirm.js" in (TEMPLATES / "base.html").read_text()
    for name in ("teams.html", "drafts.html", "ledger_form.html", "ledger_integrity.html"):
        source = (TEMPLATES / name).read_text()
        assert "data-confirm=" in source, name
        assert "confirm(" not in source, name


def test_security_headers_on_pages_static_and_legacy_notice(client):
    responses = [
        client.get("/login"),
        client.get("/static/js/confirm.js"),
        client.get("/login", headers={"user-agent": "Mozilla/5.0 (Windows NT 6.1; Trident/7.0)"}),
    ]
    for response in responses:
        for key, value in SECURITY_HEADERS.items():
            assert response.headers.get(key) == value, (response.url, key)
    assert responses[1].status_code == 200
    assert responses[1].headers.get("cache-control") is None
    assert responses[2].status_code == 426


def test_two_file_upload_allowance_reaches_route_validation(auth_client):
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.txt", b"x" * (20 * 1024 * 1024 + 512 * 1024), "text/plain")},
    )
    assert response.status_code == 400
    assert "지원하지 않는 파일 형식" in response.text


def test_oversized_browser_upload_gets_korean_page(auth_client):
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.png", b"x" * (22 * 1024 * 1024), "image/png")},
        headers={"accept": "text/html,application/xhtml+xml"},
    )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("text/html")
    assert "최대 10MB" in response.text and 'href="/monthly"' in response.text
    assert "<script" not in response.text
    assert response.headers.get("cache-control") == "no-store"
    for key, value in SECURITY_HEADERS.items():
        assert response.headers.get(key) == value


def test_oversized_script_request_keeps_json_error(client):
    response = client.raw_request(
        "POST", "/drafts/save", content=(b"x" * (1024 * 1024) for _ in range(21))
    )
    assert response.status_code == 413
    assert response.json()["error"] == response.json()["detail"]


def test_upload_limit_follows_configured_file_size(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: Settings(max_upload_mb=20))
    with TestClient(main.create_app()) as configured:
        body = (b"x" * (1024 * 1024) for _ in range(30))
        accepted = configured.post("/monthly/upload", content=body)
        assert accepted.status_code != 413
        body = (b"x" * (1024 * 1024) for _ in range(42))
        assert configured.post("/monthly/upload", content=body).status_code == 413
        body = (b"x" * (1024 * 1024) for _ in range(21))
        assert configured.post("/login", content=body).status_code == 413
