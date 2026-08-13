"""DB 백업 — 월간 확정 전 자동 백업 및 보관 개수 제한.

SQLite는 단일 파일이므로 파일 복사로 백업된다. 확정(트랜잭션) 전에 호출해
직전 상태를 보존한다.
"""

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import current_database_path
from app.logging import get_logger

logger = get_logger("pointbook.backup")


def _source_path() -> Path:
    return current_database_path()


def backup_database() -> Path | None:
    """현재 DB 파일을 data/backups/에 타임스탬프로 복사하고 오래된 백업을 정리한다.

    DB 파일이 없으면 None을 반환한다 (아무것도 하지 않음).
    """
    source = _source_path()
    if not source.is_file():
        return None
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{source.stem}-{timestamp}{source.suffix}"
    shutil.copy2(source, dest)
    _prune(backup_dir, get_settings().backup_keep)
    logger.info("DB 백업 생성: %s", dest)
    return dest


def _prune(backup_dir: Path, keep: int) -> None:
    backups = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
