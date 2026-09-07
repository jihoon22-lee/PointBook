"""SQLite 일관된 사본, 검증·보관 및 복구 상태.

확정 호출자는 write transaction을 확보/재검증한 뒤 첫 DML 이전에 호출한다.
이 서비스는 별도 읽기 연결을 사용하므로 미커밋 변경을 백업하지 않는다.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import current_database_path


class BackupError(RuntimeError):
    """민감 경로/DB 값을 포함하지 않는 백업 오류."""


def _source_path() -> Path:
    return current_database_path()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _fsync(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".metadata-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def recovery_status(source: Path | None = None) -> dict[str, Any]:
    """운영 화면용 마지막 결과. 경로·장부 수치·예외 원문은 제공하지 않는다."""
    path = (source or _source_path()).parent / "recovery-status.json"
    try:
        value = json.loads(path.read_text())
        return {
            key: {field: item[field] for field in ("at", "status") if field in item}
            for key, item in value.items()
            if key in {"backup", "restore"} and isinstance(item, dict)
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def record_recovery_status(source: Path, operation: str, status: str) -> None:
    state = recovery_status(source)
    state[operation] = {"at": datetime.now(UTC).isoformat(), "status": status}
    _atomic_json(source.parent / "recovery-status.json", state)


def validate_database(path: Path) -> dict[str, Any]:
    """취득한 단일 사본에서 구조·무결성·합계를 검증한다. 원본은 재조회하지 않는다."""
    try:
        with closing(_connect_readonly(path)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("DB 무결성 검사에 실패했습니다.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BackupError("DB 외래키 검사에 실패했습니다.")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            required = {"people", "teams", "monthly_snapshots", "balance_records", "admin_users"}
            if not required <= tables:
                raise BackupError("PointBook 최소 DB 구조가 없습니다.")
            invalid = connection.execute(
                "SELECT COUNT(*) FROM balance_records "
                "WHERE total != carry_balance + amount OR total IS NULL "
                "OR carry_balance IS NULL OR amount IS NULL"
            ).fetchone()[0]
            if invalid:
                raise BackupError("장부 총잔액 공식 검증에 실패했습니다.")
            counts = {
                table: connection.execute(
                    'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
                ).fetchone()[0]
                for table in sorted(tables)
            }
            sums = connection.execute(
                "SELECT COALESCE(SUM(carry_balance),0), COALESCE(SUM(amount),0), "
                "COALESCE(SUM(usage),0), COALESCE(SUM(total),0) FROM balance_records"
            ).fetchone()
            current = connection.execute(
                "SELECT COALESCE(SUM(current_carry_balance + current_amount),0) FROM people"
            ).fetchone()[0]
            revisions = (
                [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")]
                if "alembic_version" in tables
                else []
            )
            schema = connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
            ).fetchall()
            return {
                "counts": counts,
                "ledger_sums": list(sums),
                "current_total": current,
                "revisions": sorted(revisions),
                "schema_sha256": hashlib.sha256(json.dumps(schema).encode()).hexdigest(),
            }
    except sqlite3.Error as exc:
        raise BackupError("DB를 열거나 구조를 검증할 수 없습니다.") from exc


def copy_database(source: Path, destination: Path, *, timeout: float = 30) -> None:
    """WAL을 포함한 일관된 복사. destination은 호출자가 소유한 새 임시 경로여야 한다."""
    deadline = time.monotonic() + timeout

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise BackupError("DB 사본 취득 시간이 초과됐습니다.")

    try:
        with (
            closing(_connect_readonly(source)) as original,
            closing(sqlite3.connect(destination)) as target,
        ):
            original.backup(target, pages=256, progress=progress, sleep=0.05)
            # 백업은 독립 단일 파일이어야 하며 읽기 검증이 WAL sidecar를 만들지 않는다.
            target.execute("PRAGMA journal_mode=DELETE")
        destination.chmod(0o600)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError("DB 사본 취득에 실패했습니다.") from exc


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_backup(path: Path) -> dict[str, Any]:
    """manifest와 실제 DB가 일치하는 검증 완료 사본만 복원 대상으로 받는다."""
    try:
        metadata = json.loads(path.with_suffix(".json").read_text())
        if metadata["format"] != 1 or metadata["sha256"] != _digest(path):
            raise BackupError("백업 metadata 또는 파일 해시가 일치하지 않습니다.")
        if metadata["database"] != validate_database(path):
            raise BackupError("백업 구조·revision·합계가 metadata와 일치하지 않습니다.")
        return dict(metadata)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError("검증 가능한 백업 metadata가 없습니다.") from exc


def _backup_owner(source: Path) -> str:
    """호스트와 컨테이너에서 공유하는 장부 ID. 동일 컨테이너 경로의 다른 DB와 구분한다.

    호출자는 이 DB의 backup.lock을 보유한다. 복원·마이그레이션 후에도 같은 장부 ID를 쓴다.
    """
    identity = source.with_name("." + source.name + ".backup-owner")
    if not identity.exists():
        value = uuid.uuid4().hex
        descriptor = os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(identity.parent)
    try:
        value = identity.read_text().strip()
        if uuid.UUID(hex=value).hex != value:
            raise ValueError
        return value
    except ValueError as exc:
        raise BackupError("백업 소유 식별자가 손상되었습니다. 보존 후 확인해야 합니다.") from exc


def _backup_database(
    *, source: Path | None = None, directory: Path | None = None, keep: int | None = None
) -> Path | None:
    source = (source or _source_path()).resolve()
    settings = get_settings()
    keep = settings.backup_keep if keep is None else keep
    if not 1 <= keep <= 10000:
        raise BackupError("백업 보관 개수는 1~10000이어야 합니다.")
    if not source.is_file():
        return None
    configured = getattr(settings, "backup_dir", "")
    backup_dir = (
        directory or (Path(configured) if configured else source.parent / "backups")
    ).resolve()
    owner = _backup_owner(source)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(backup_dir).free < max(source.stat().st_size * 2, 1024 * 1024):
        raise BackupError("검증 백업을 위한 여유 공간이 부족합니다.")
    descriptor, name = tempfile.mkstemp(prefix=".backup-", dir=backup_dir)
    os.close(descriptor)
    temporary = Path(name)
    final = backup_dir / f"{owner}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex}.db"
    try:
        copy_database(source, temporary)
        database = validate_database(temporary)
        metadata = {
            "format": 1,
            "owner": owner,
            "created_at": datetime.now(UTC).isoformat(),
            "database": database,
            "sha256": _digest(temporary),
        }
        _fsync(temporary)
        # DB를 마지막에 공개: .db가 보이면 이미 manifest가 존재한다.
        _atomic_json(final.with_suffix(".json"), metadata)
        os.replace(temporary, final)
        _sync_directory(backup_dir)
        record_recovery_status(source, "backup", "verified")
        _prune(backup_dir, keep, owner)
        return final
    except Exception:
        if not final.exists():
            final.with_suffix(".json").unlink(missing_ok=True)
        try:
            record_recovery_status(source, "backup", "failed")
        except OSError:
            pass
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _prune(backup_dir: Path, keep: int, owner: str) -> None:
    backups = sorted(backup_dir.glob(f"{owner}-*.db"), reverse=True)
    # 기존 손상/불완전 사본을 성공 목록에 넣거나 다른 DB의 파일을 지우지 않는다.
    verified = []
    for candidate in backups:
        try:
            if validate_backup(candidate)["owner"] == owner:
                verified.append(candidate)
        except BackupError:
            continue
    for old in verified[keep:]:
        old.unlink()
        old.with_suffix(".json").unlink(missing_ok=True)


def backup_database(
    *, source: Path | None = None, directory: Path | None = None, keep: int | None = None
) -> Path | None:
    source = (source or _source_path()).resolve()
    if not source.parent.exists():
        if keep is not None and not 1 <= keep <= 10000:
            raise BackupError("백업 보관 개수는 1~10000이어야 합니다.")
        return None
    # 앱 확정과 호스트 스케줄 간 사본 생성·보관 정리 경합도 직렬화한다.
    with (source.parent / ("." + source.name + ".backup.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _backup_database(source=source, directory=directory, keep=keep)
        except Exception:
            try:
                record_recovery_status(source, "backup", "failed")
            except OSError:
                pass
            raise


def compare_ledger(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """스키마 revision 변화와 분리해 복원/이전의 기존 장부 행 수·합계를 대사한다."""
    for field in ("ledger_sums", "current_total"):
        if actual[field] != expected[field]:
            raise BackupError("복원 장부 합계 대사에 실패했습니다.")
    for table in ("people", "teams", "monthly_snapshots", "balance_records"):
        if actual["counts"].get(table) != expected["counts"].get(table):
            raise BackupError("복원 장부 행 수 대사에 실패했습니다.")
