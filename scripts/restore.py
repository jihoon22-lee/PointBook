"""선택 사본 검증 및 정지된 서비스의 DB 복원. 자동 rollback은 하지 않는다."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from app import db as db_module
from app.config import get_settings
from app.services.backup import (
    BackupError,
    _fsync,
    _sync_directory,
    backup_database,
    compare_ledger,
    record_recovery_status,
    validate_backup,
    validate_database,
)
from scripts.preflight import _validate_admin_for_start


def verify_restore_backup(backup: Path) -> dict[str, Any]:
    """선택 이미지가 사본을 실제로 이전할 수 있는지 정지/교체 전에 예행 검증한다.

    temp Engine을 전달하므로 운영 전역 engine/URL이나 원본 backup을 바꾸지 않는다.
    metadata 검증 역시 임시 사본에서 하여 선택 파일의 검사·복사 사이 변경을 거부한다.
    """
    with tempfile.TemporaryDirectory(prefix="pointbook-restore-rehearsal-") as folder:
        staged = Path(folder) / "rehearsal.db"
        shutil.copyfile(backup, staged)
        staged.chmod(0o600)
        shutil.copyfile(backup.with_suffix(".json"), staged.with_suffix(".json"))
        metadata = validate_backup(staged)
        engine = create_engine(f"sqlite:///{staged}")
        try:
            if (
                not metadata["database"]["revisions"]
                and db_module._known_unversioned_revision(engine) is None
            ):
                raise BackupError("무버전 사본의 DB 스키마를 확인할 수 없습니다.")
            db_module.run_migrations(engine)
            db_module.validate_schema(engine)
            _validate_admin_for_start(get_settings(), engine)
            compare_ledger(metadata["database"], validate_database(staged))
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(
                "선택 이미지의 DB revision·마이그레이션 예행 검증에 실패했습니다."
            ) from exc
        finally:
            engine.dispose()
        return metadata


def verify_restored_database(backup: Path, target: Path) -> None:
    """새 revision/테이블은 허용하되 기존 장부 행 수·합계가 유지되어야 한다."""
    expected = verify_restore_backup(backup)["database"]
    compare_ledger(expected, validate_database(target))


def restore_database(backup: Path, target: Path, *, service_stopped: bool = False) -> None:
    if not service_stopped:
        raise BackupError("복원 전에 대상 서비스와 다른 DB writer를 중지해야 합니다.")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # 임시 파일은 교체 대상과 같은 filesystem에 둬 원자 교체를 보장한다.
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=target.parent) as folder:
        staged = Path(folder) / "staged.db"
        shutil.copyfile(backup, staged)
        staged.chmod(0o600)
        shutil.copyfile(backup.with_suffix(".json"), staged.with_suffix(".json"))
        metadata = verify_restore_backup(staged)
        if target.exists():
            # 현 상태가 손상됐다면 덮지 않고 수동 구조 복구로 넘긴다.
            preserved = backup_database(
                source=target, directory=target.parent / "restore-preserved", keep=10000
            )
            if preserved is None:
                raise BackupError("기존 DB 보존에 실패했습니다.")
        _fsync(staged)
        # 정지된 DB의 WAL 내용을 위 검증 보존 사본에 포함한 뒤 sidecar를 격리한다.
        # 임시 디렉터리 삭제가 아닌 별도 보존 디렉터리를 사용한다.
        if target.exists():
            with closing(sqlite3.connect(target)) as connection:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint[0] != 0:
                    raise BackupError("기존 DB writer가 남아 있어 복원을 중단합니다.")
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
        sidecars = [Path(str(target) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        if any(path.exists() for path in sidecars):
            sidecar_dir = Path(tempfile.mkdtemp(prefix="restore-sidecars-", dir=target.parent))
            for path in sidecars:
                if path.exists():
                    os.replace(path, sidecar_dir / path.name)
        os.replace(staged, target)
        _sync_directory(target.parent)
        if validate_database(target) != metadata["database"]:
            raise BackupError("교체 후 대사에 실패했습니다. 보존 사본으로 명시적 복구하세요.")
        record_recovery_status(target, "restore", "verified")


def main() -> int:
    parser = argparse.ArgumentParser(description="검증된 PointBook 사본 복원")
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--verify-current", action="store_true", help="기동 후 장부 행 수·합계 대사"
    )
    parser.add_argument("--apply", action="store_true", help="기존 DB 보존 후 교체")
    parser.add_argument("--service-stopped", action="store_true", help="모든 DB writer 정지 확인")
    args = parser.parse_args()
    target: Path | None = None
    phase = "restore" if args.apply or args.verify_current else "rehearsal"
    try:
        target = Path(get_settings().database_path)
        if args.verify_current:
            verify_restored_database(args.backup, target)
            print("기동 후 복원 장부 행 수·합계 대사 완료.")
        elif args.apply:
            restore_database(args.backup, target, service_stopped=args.service_stopped)
            print("복원·DB 대사 완료. 앱 기동·읽기·재시작 검증이 필요합니다.")
        else:
            metadata = verify_restore_backup(args.backup)
            record_recovery_status(target, "rehearsal", "verified")
            print(f"복원 사본 검증 완료. DB revision: {metadata['database']['revisions']}")
        return 0
    except Exception:  # noqa: BLE001 - 민감 경로/행/예외 원문 출력 금지
        try:
            if target is not None:
                record_recovery_status(target, phase, "failed")
        except OSError:
            pass
        print("복원 실패. 서비스 상태와 기존 보존 사본을 확인하세요. 자동 rollback하지 않습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
