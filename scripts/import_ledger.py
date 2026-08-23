"""누적 포인트 장부의 dry-run 우선 이관 CLI."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from app import db as db_module
from app.services.backup import backup_database
from app.services.ledger_import import (
    LedgerData,
    LedgerImportError,
    LedgerImportPlan,
    analyze_ledger_import,
    apply_ledger_import,
    month_summary,
    parse_ledger,
    validate_expected_totals,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="누적 포인트 장부를 안전하게 이관")
    parser.add_argument("--file", required=True, type=Path, help="누적 장부 .xlsx 경로")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="DB를 변경하지 않고 검증")
    mode.add_argument("--apply", action="store_true", help="백업 후 실제 DB에 적용")
    parser.add_argument(
        "--replace-empty-history-people",
        action="store_true",
        help="월별 이력이 전혀 없을 때 미매칭 테스트 계정 교체",
    )
    return parser


def _print_summary(mode: str, data: LedgerData, plan: LedgerImportPlan) -> None:
    first = month_summary(data, data.months[0])
    last = month_summary(data, data.months[-1])
    print(f"{mode}: 누적 장부 검증 통과")
    print(
        f"계정 {len(data.people) + len(data.shared_accounts)}개 "
        f"(일반 {len(data.people)}, 공용 {len(data.shared_accounts)})"
    )
    print(
        f"월 {len(data.months)}개 ({data.months[0]}~{data.months[-1]}), 기록 {len(data.records)}건"
    )
    print(
        f"계획: 생성 {plan.create_account_count}, 갱신 {plan.update_account_count}, "
        f"빈 이력 계정 삭제 {len(plan.delete_person_ids)}"
    )
    print(f"최초 월: {first.count}건, 지급 {first.amount:,}, 총잔액 {first.total:,}")
    print(
        f"최종 월: {last.count}건, 이월 {last.carry_balance:,}, "
        f"지급 {last.amount:,}, 총잔액 {last.total:,}"
    )
    if data.warnings:
        print("헤더 경고 위치: " + ", ".join(warning.cell for warning in data.warnings))


def _analyze_on_database(
    database_url: str,
    data: LedgerData,
    *,
    replace_empty_history_people: bool,
) -> LedgerImportPlan:
    db_module.configure_database(database_url)
    db_module.init_db()
    with db_module.SessionLocal() as db:
        return analyze_ledger_import(
            db,
            data,
            replace_empty_history_people=replace_empty_history_people,
        )


def _dry_run(data: LedgerData, *, replace_empty_history_people: bool) -> LedgerImportPlan:
    source = Path(db_module.default_database_url().removeprefix("sqlite:///"))
    with tempfile.TemporaryDirectory(prefix="pointbook-ledger-dry-run-") as temp_dir:
        temporary_database = Path(temp_dir) / "pointbook.db"
        if source.is_file():
            shutil.copy2(source, temporary_database)
        try:
            return _analyze_on_database(
                f"sqlite:///{temporary_database}",
                data,
                replace_empty_history_people=replace_empty_history_people,
            )
        finally:
            db_module.engine.dispose()


def _apply(data: LedgerData, *, replace_empty_history_people: bool) -> tuple[LedgerImportPlan, int]:
    database_url = db_module.default_database_url()
    database_path = Path(database_url.removeprefix("sqlite:///"))
    db_module.configure_database(database_url)
    if database_path.is_file():
        backup = backup_database()
        if backup is None or not backup.is_file() or backup.stat().st_size == 0:
            raise LedgerImportError("실제 DB 백업에 실패하여 적용을 중단했습니다.")
    db_module.init_db()
    with db_module.SessionLocal() as db:
        plan = analyze_ledger_import(
            db,
            data,
            replace_empty_history_people=replace_empty_history_people,
        )
        result = apply_ledger_import(db, plan)
        db.commit()
        return plan, result.deleted_accounts


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        data = parse_ledger(args.file)
        validate_expected_totals(data)
        if args.apply:
            plan, _ = _apply(
                data,
                replace_empty_history_people=args.replace_empty_history_people,
            )
            _print_summary("APPLY", data, plan)
        else:
            plan = _dry_run(
                data,
                replace_empty_history_people=args.replace_empty_history_people,
            )
            _print_summary("DRY-RUN", data, plan)
        return 0
    except LedgerImportError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception:  # noqa: BLE001 - 식별자가 포함될 수 있는 예상 밖 예외는 출력하지 않는다.
        print("ERROR: 예상하지 못한 오류로 이관을 중단했습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
