"""운영 상태는 실제 판정·미실행을 구분하고 환경 비밀값을 내보내지 않는다."""

import json
from pathlib import Path

from app import db as db_module
from app.config import get_settings
from app.services.backup import record_recovery_status
from app.services.operations import operation_status


def test_status_shows_unperformed_and_verified_without_secret_paths(auth_client, db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "database_path", str(db_module.current_database_path()))
    monkeypatch.setattr(settings, "gemini_api_key", "synthetic-api-private-value")
    status = operation_status(db)
    assert status["db_ready"] and status["path_matches"]
    assert status["recovery"] == {}
    initial = auth_client.get("/settings")
    assert "미실행 · 기록 없음" in initial.text
    source = db_module.current_database_path()
    record_recovery_status(source, "backup", "verified")
    record_recovery_status(source, "rehearsal", "failed")
    path = source.parent / "recovery-status.json"
    stored = json.loads(path.read_text())
    stored["backup"]["path"] = "/synthetic/private-path"
    stored["secret_key"] = "synthetic-api-private-value"
    path.write_text(json.dumps(stored))
    response = auth_client.get("/settings")
    assert "검증 완료" in response.text and "실패" in response.text
    assert str(source) not in response.text
    assert "/synthetic/private-path" not in response.text
    assert "synthetic-api-private-value" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_path_mismatch_is_not_reported_as_verified(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "database_path", str(Path("/different/synthetic.db")))
    assert not operation_status(db)["path_matches"]


def test_ie_user_agent_receives_guidance_without_login_or_writes(client):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko"}
    response = client.get("/login", headers=headers)
    assert response.status_code == 426
    assert "다른 브라우저로 열어 주세요" in response.text
    assert "<script" not in response.text and "<form" not in response.text
    assert client.raw_request("POST", "/monthly/confirm", headers=headers).status_code == 426
    assert client.get("/health", headers=headers).status_code == 200
