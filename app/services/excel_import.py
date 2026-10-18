"""기존 엑셀 요청서 데이터를 DB로 이관한다.

헤더 이름(별칭 포함)으로 컬럼을 찾는다:
- 팀(부서/팀명), 이름(성명), 계급(직급), 금액(충전금액/포인트),
  개인번호(번호/개인번호), 비고(메모/특이사항), 잔액(이월잔액/전월잔액, 선택)
"""

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BalanceRecord, MonthlySnapshot, Person
from app.services.balance import compute_total, create_monthly_snapshot
from app.services.identifiers import normalize_point_no
from app.services.sync import RequestRow
from app.services.teams import get_or_create_team
from app.services.validation import parse_balance, parse_money

HEADER_SYNONYMS: dict[str, list[str]] = {
    "team": ["팀", "부서", "팀명"],
    "name": ["이름", "성명"],
    "grade": ["계급", "직급"],
    "amount": ["금액", "충전", "충전금액", "포인트"],
    "personal_no": ["개인번호", "번호"],
    "point_no": ["포인트번호", "포인트 번호"],
    "note": ["비고", "메모", "특이사항"],
    "balance": ["잔액", "이월", "이월잔액", "전월잔액"],
}


@dataclass
class ImportRow:
    team: str
    name: str
    grade: str
    amount: int
    personal_no: str
    point_no: str
    note: str
    balance: int = 0

    def to_request_row(self) -> RequestRow:
        return RequestRow(
            point_no=self.point_no,
            personal_no=self.personal_no,
            name=self.name,
            team=self.team,
            grade=self.grade,
            amount=self.amount,
            note=self.note,
        )


@dataclass
class ImportResult:
    month: str
    created_persons: int
    existing_persons: int
    records: int


def _cell_text(value: object) -> str:
    """셀 값을 문자열로 읽는다. 0을 빈칸으로 바꾸지 않고 정수형 소수만 정수로 표기한다."""
    if value is None:
        return ""
    if type(value) is float and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _find_columns(headers: list[str]) -> dict[str, int]:
    """정확히 같은 제목을 먼저 배정하고, 남은 열만 별칭 접두어로 찾는다. 한 열은 한 항목만 쓴다."""
    columns: dict[str, int] = {}
    for exact in (True, False):
        for key, synonyms in HEADER_SYNONYMS.items():
            if key in columns:
                continue
            for index, text in enumerate(headers):
                if index in columns.values():
                    continue
                if any(text == syn if exact else text.startswith(syn) for syn in synonyms):
                    columns[key] = index
                    break
    return columns


def _build_row(columns: dict[str, int], values: list[str], number: int) -> ImportRow | None:
    def value(key: str, default: str = "") -> str:
        idx = columns.get(key)
        return values[idx] if idx is not None and idx < len(values) else default

    name = value("name")
    personal_no = value("personal_no")
    point_no = value("point_no")
    if not name or not personal_no:
        return None
    if not point_no:
        raise ValueError(f"{number}행: 포인트번호가 없는 요청서 행이 있습니다.")
    try:
        normalized = normalize_point_no(point_no)
    except ValueError as exc:
        raise ValueError(f"{number}행: {exc}") from exc
    return ImportRow(
        team=value("team"),
        name=name,
        grade=value("grade"),
        # 빈칸·소수·부호·문자 섞인 금액을 0원(=그 달 비재직)으로 바꾸지 않는다.
        amount=parse_money(value("amount"), label=f"{number}행 금액"),
        personal_no=personal_no,
        point_no=normalized,
        note=value("note"),
        balance=(
            parse_balance(value("balance"), label=f"{number}행 잔액") if "balance" in columns else 0
        ),
    )


def read_rows(path: Path) -> list[ImportRow]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    if sheet is None:
        return []
    rows: list[ImportRow] = []
    columns: dict[str, int] = {}
    found_person_header = False
    seen: dict[str, int] = {}

    for number, cells in enumerate(sheet.iter_rows(), 1):
        values = [_cell_text(c.value) for c in cells]
        if not any(values):
            continue
        if not columns:
            columns = _find_columns(values)
            if "name" in columns and "personal_no" in columns:
                found_person_header = True
                if "point_no" not in columns:
                    raise ValueError("요청서에 포인트번호 열이 없습니다.")
                if "amount" not in columns:
                    raise ValueError("요청서에 금액 열이 없습니다.")
                continue
            columns = {}
        else:
            row = _build_row(columns, values, number)
            if row is not None:
                if row.point_no in seen:
                    raise ValueError(
                        f"{number}행: 포인트번호 {row.point_no}가 {seen[row.point_no]}행과 중복됩니다."
                    )
                seen[row.point_no] = number
                rows.append(row)
    if not found_person_header:
        return []
    return rows


def import_excel(db: Session, path: Path, month: str) -> ImportResult:
    """빈 DB에서 엑셀 행을 인원·팀으로 이관하고 해당 월 스냅샷을 생성한다.

    전체 행을 먼저 검증하므로 오류가 있으면 아무것도 쓰지 않는다. 커밋은 스냅샷 생성이 한다.
    """
    if db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month)) is not None:
        raise ValueError(f"{month} 월은 이미 처리되었습니다.")
    if db.scalar(select(Person).limit(1)) is not None:
        raise ValueError("DB에 이미 인원이 존재합니다. 빈 DB에서만 이관할 수 있습니다.")

    imported = read_rows(path)
    # CLI 세션은 autoflush=False다. 다시 조회하지 않고 만든 객체로 기록을 만든다.
    created: dict[str, Person] = {}
    for row in imported:
        team = get_or_create_team(db, row.team) if row.team else None
        person = Person(
            point_no=row.point_no,
            personal_no=row.personal_no,
            name=row.name,
            grade=row.grade,
            team_id=team.id if team else None,
            status="active",
            current_carry_balance=row.balance,
            current_amount=row.amount,
        )
        db.add(person)
        created[row.point_no] = person
    db.flush()
    records = [
        BalanceRecord(
            person_id=created[row.point_no].id,
            carry_balance=row.balance,
            amount=row.amount,
            usage=0,
            total=compute_total(row.amount, row.balance),
        )
        for row in imported
    ]
    if records:
        create_monthly_snapshot(db, month, records, source="legacy_import")
    return ImportResult(
        month=month, created_persons=len(created), existing_persons=0, records=len(records)
    )
