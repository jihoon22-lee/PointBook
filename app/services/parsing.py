"""요청서 원문과 검증된 행을 구분한다. 오류 행도 검수 화면에서 수정할 수 있다."""

import csv
import uuid
from dataclasses import dataclass, field

from app.services.identifiers import normalize_point_no
from app.services.sync import RequestRow
from app.services.validation import parse_money

MAX_REQUEST_ROWS = 2000
ROW_FIELDS = ("point_no", "personal_no", "name", "team", "grade", "amount", "note", "account_type")


@dataclass
class RawRequestRow:
    point_no: str = ""
    personal_no: str = ""
    name: str = ""
    team: str = ""
    grade: str = ""
    amount: str = ""
    note: str = ""
    account_type: str = "person"
    carry: str = ""
    row_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source_line: str = ""
    source_issue: str = ""

    def __post_init__(self) -> None:
        # Excel LF와 브라우저 폼 CRLF를 같은 비고로 검수한다. 빈 줄도 보존한다.
        self.note = self.note.replace("\r\n", "\n").replace("\r", "\n")

    def validated(self) -> RequestRow:
        if self.source_issue:
            raise ValueError(self.source_issue)
        for label, value, limit in (
            ("이름", self.name, 50),
            ("개인번호", self.personal_no, 50),
            ("팀", self.team, 50),
            ("계급", self.grade, 50),
            ("비고", self.note, 1000),
        ):
            if len(value) > limit:
                raise ValueError(f"{label}은 {limit}자 이하여야 합니다.")
        return RequestRow(
            point_no=self.point_no,
            personal_no=self.personal_no.strip(),
            name=self.name.strip(),
            team=self.team.strip(),
            grade=self.grade.strip(),
            amount=parse_money(self.amount),
            note=self.note,
            account_type=self.account_type,
        )


def _to_int(value: str) -> int:
    """기존 호출부의 호환 이름. 잘못된 값을 0으로 바꾸지 않는다."""
    return parse_money(value)


def parse_pasted_raw(text: str) -> list[RawRequestRow]:
    rows: list[RawRequestRow] = []
    has_sequence: bool | None = None
    request_headers: list[str] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        delimiter = "\t" if "\t" in line else ","
        try:
            cols = next(csv.reader([line], delimiter=delimiter, strict=True))
        except csv.Error:
            cols = [line]
        headers = [c.strip() for c in cols]
        if (
            "이름" in headers
            and "개인번호" in headers
            and ("충전액" in headers or "금액" in headers)
            and "포인트번호" not in headers
        ):
            if (
                len(set(headers)) != len(headers)
                or ("금액" in headers and "충전액" in headers)
                or any(
                    h
                    not in {
                        "순번",
                        "계정 구분",
                        "팀",
                        "이름",
                        "계급",
                        "금액",
                        "충전액",
                        "개인번호",
                        "비고",
                        "이월 잔액",
                    }
                    for h in headers
                )
            ):
                raise ValueError("붙여넣기 열 제목이 중복되었거나 지원하지 않는 열이 있습니다.")
            request_headers = headers
            continue
        if request_headers is not None:
            if len(cols) != len(request_headers):
                rows.append(
                    RawRequestRow(
                        source_line=line, source_issue="열 수가 제목과 다릅니다. 원문을 확인하세요."
                    )
                )
            else:
                values = dict(zip(request_headers, cols, strict=True))
                kind = values.get("계정 구분", "person").strip()
                rows.append(
                    RawRequestRow(
                        team=values.get("팀", ""),
                        name=values["이름"],
                        grade=values.get("계급", ""),
                        amount=values.get("충전액", values.get("금액", "")),
                        personal_no=values["개인번호"],
                        note=values.get("비고", ""),
                        carry=values.get("이월 잔액", ""),
                        account_type={"일반": "person", "공용": "shared"}.get(kind, kind),
                        source_line=line,
                    )
                )
            if len(rows) > MAX_REQUEST_ROWS:
                raise ValueError(f"요청서는 최대 {MAX_REQUEST_ROWS}행까지 처리할 수 있습니다.")
            continue
        if "포인트번호" in [c.strip() for c in cols] and "이름" in [c.strip() for c in cols]:
            has_sequence = cols[0].strip() == "순번"
            continue
        # 순번은 내용 열과 구분한다. 빈 마지막 비고를 제거해 다른 열로 밀지 않는다.
        sequence = has_sequence is True
        ambiguous = False
        if (
            has_sequence is None
            and len(cols) >= 7
            and cols[0].strip().isascii()
            and cols[0].strip().isdigit()
        ):
            candidates = []
            for candidate in (cols[5], cols[6]):
                try:
                    normalize_point_no(candidate)
                    candidates.append(True)
                except ValueError:
                    candidates.append(False)
            if candidates == [False, True]:
                sequence = True
            elif candidates != [True, False]:
                ambiguous = True
        if sequence:
            cols = cols[1:]
        if len(cols) > 8 or ambiguous:
            rows.append(
                RawRequestRow(
                    source_line=line,
                    source_issue="순번·팀·열 구분을 확인할 수 없습니다. 헤더를 포함해 붙여넣거나 원문을 보고 행을 수정하세요.",
                )
            )
        else:
            cols += [""] * (8 - len(cols))
            team, name, grade, amount, personal_no, point_no, note, account_type = cols
            rows.append(
                RawRequestRow(
                    point_no=point_no,
                    personal_no=personal_no,
                    name=name,
                    team=team,
                    grade=grade,
                    amount=amount,
                    note=note,
                    account_type=account_type.strip() or "person",
                    source_line=line,
                )
            )
        if len(rows) > MAX_REQUEST_ROWS:
            raise ValueError(f"요청서는 최대 {MAX_REQUEST_ROWS}행까지 처리할 수 있습니다.")
    return rows


def parse_pasted(text: str) -> list[RequestRow]:
    """기존 파서 API. 검증 오류는 행 위치와 함께 반환한다."""
    rows: list[RequestRow] = []
    for index, raw in enumerate(parse_pasted_raw(text), 1):
        try:
            rows.append(raw.validated())
        except ValueError as exc:
            raise ValueError(f"{index}행: {exc}") from exc
    return rows
