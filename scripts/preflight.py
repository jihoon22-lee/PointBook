"""서비스 중지 전 선택 이미지에서 운영 설정과 현재 DB의 이전 가능성을 검증한다."""

import tempfile
from pathlib import Path

from sqlalchemy import Engine, create_engine, select
from werkzeug.security import check_password_hash

from app import db as db_module
from app.config import DEFAULT_ADMIN_PASSWORD, Settings, get_settings
from app.models import AdminUser
from app.services.backup import (
    compare_ledger,
    copy_database,
    record_recovery_status,
    validate_database,
)


def _validate_admin_for_start(settings: Settings, engine: Engine | None = None) -> None:
    if not (settings.app_env == "production" or settings.enforce_secure_defaults):
        return
    users = []
    if engine is not None:
        with engine.connect() as connection:
            users = list(connection.execute(select(AdminUser.username, AdminUser.password_hash)))
    if not any(user.username == settings.admin_username for user in users) and (
        not settings.admin_password.strip() or settings.admin_password == DEFAULT_ADMIN_PASSWORD
    ):
        raise RuntimeError("운영 관리자 초기 암호를 설정해야 합니다.")
    for user in users:
        if check_password_hash(user.password_hash, DEFAULT_ADMIN_PASSWORD) or check_password_hash(
            user.password_hash, ""
        ):
            raise RuntimeError("기존 운영 관리자 DB의 기본/빈 암호를 변경해야 합니다.")


def preflight_database(source: Path) -> None:
    settings = get_settings()
    if not source.exists():
        _validate_admin_for_start(settings)
        return
    with tempfile.TemporaryDirectory(prefix="pointbook-start-rehearsal-") as folder:
        staged = Path(folder) / "rehearsal.db"
        copy_database(source, staged)
        before = validate_database(staged)
        engine = create_engine(f"sqlite:///{staged}")
        try:
            db_module.run_migrations(engine)
            db_module.validate_schema(engine)
            _validate_admin_for_start(settings, engine)
            compare_ledger(before, validate_database(staged))
        finally:
            engine.dispose()


def main() -> int:
    source: Path | None = None
    try:
        settings = get_settings()
        settings.validate_runtime()
        source = Path(settings.database_path)
        preflight_database(source)
        if source.exists():
            record_recovery_status(source, "rehearsal", "verified")
        print("운영 설정·DB 이전 예행 검증 완료.")
        return 0
    except Exception:  # noqa: BLE001 - 경로·DB 행·설정의 민감 예외를 출력하지 않는다.
        if source is not None and source.exists():
            try:
                record_recovery_status(source, "rehearsal", "failed")
            except OSError:
                pass
        print(
            "기동 사전 검증 실패. 설정·DB 구조·이전 조건을 확인하세요. 서비스를 중지하지 않았습니다."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
