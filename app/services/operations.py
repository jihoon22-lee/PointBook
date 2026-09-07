"""인증된 운영 상태 화면에 필요한 비밀값 없는 읽기 전용 요약."""

from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import db as db_module
from app._version import __version__
from app.config import get_settings
from app.services.backup import recovery_status


def operation_status(db: Session) -> dict[str, Any]:
    settings = get_settings()
    try:
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        expected = ScriptDirectory.from_config(db_module._alembic_config()).get_current_head()
        db_module.validate_schema(db.connection(), full=False)
        ready = revision == expected
    except Exception:  # noqa: BLE001 - DB 원문 오류/경로를 화면에 노출하지 않는다.
        revision, ready = "확인 실패", False
    path_matches = (
        db_module.current_database_path().resolve() == Path(settings.database_path).resolve()
    )
    return {
        "app_version": __version__,
        "db_revision": revision,
        "db_ready": ready,
        "path_matches": path_matches,
        "ai_provider": settings.ai_provider
        if settings.ai_provider in {"mock", "gemini"}
        else "설정 오류",
        "app_env": settings.app_env,
        "recovery": recovery_status(db_module.current_database_path()),
        "draft_keep_days": settings.draft_keep_days,
        "draft_max_active": settings.draft_max_active,
    }
