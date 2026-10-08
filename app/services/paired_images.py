"""지급표를 기준으로 별도 명단의 개인번호만 보완한다. DB 계정 연결과는 별개다."""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace

from app.services.parsing import ROW_FIELDS, RawRequestRow

GRADES = {"경": "소방경", "위": "소방위", "장": "소방장", "교": "소방교", "사": "소방사"}


@dataclass
class PairedRequest:
    rows: list[RawRequestRow]
    import_notice: str = ""


def _source(row: RawRequestRow) -> dict[str, str]:
    return {
        key: value
        for key, value in asdict(row).items()
        if key in (*ROW_FIELDS, "source_line", "source_issue")
    }


def _key(row: RawRequestRow) -> tuple[str, str, str]:
    grade = row.grade.strip()
    return row.team.strip(), GRADES.get(grade, grade), row.name.strip()


def merge_roster(payments: list[RawRequestRow], roster: list[RawRequestRow]) -> PairedRequest:
    """양쪽에서 유일한 팀·계급·이름만 결합하며 중복/불일치는 사람이 확인한다."""
    rows = [replace(row) for row in payments]
    payment_counts = Counter(_key(row) for row in rows)
    roster_counts = Counter(_key(row) for row in roster)
    used: set[int] = set()
    for original, row in zip(payments, rows, strict=True):
        key = _key(row)
        candidates = [(i, other) for i, other in enumerate(roster) if _key(other) == key]
        related = [(i, other) for i, other in enumerate(roster) if other.name.strip() == key[2]]
        warning = ""
        match = candidates[0] if len(candidates) == 1 else None
        if (
            all(key)
            and payment_counts[key] == roster_counts[key] == 1
            and match is not None
            and not match[1].source_issue
            and match[1].account_type == row.account_type == "person"
            and re.fullmatch(r"[0-9]{4}", match[1].personal_no.strip())
        ):
            index, other = match
            used.add(index)
            if row.personal_no.strip() and row.personal_no.strip() != other.personal_no.strip():
                warning = "지급표와 개인번호 명단의 번호가 다릅니다. 두 원문을 확인하세요."
            else:
                row.personal_no = other.personal_no.strip()
        else:
            warning = (
                "개인번호 명단과 팀·계급·이름이 다르거나 중복/불완전합니다. 후보를 확인해 개인번호를 입력하세요."
                if related
                else "개인번호 명단에서 이름을 찾지 못했습니다. 개인번호 또는 기존 인원을 직접 확인하세요."
            )
        if warning:
            row.source_warning = warning
            row.source_issue = " ".join(filter(None, [row.source_issue, warning]))
        # 자동 보완 전 지급표 원문과 같은 이름의 모든 후보를 보존한다.
        row.source_line = json.dumps(
            {
                "지급표": _source(original),
                "개인번호 명단 후보": [_source(other) for _, other in related],
            },
            ensure_ascii=False,
        )
    unused = [other for i, other in enumerate(roster) if i not in used]
    notice = ""
    if unused:
        detail = "; ".join(
            f"{other.team} {other.grade} {other.name} {other.personal_no}" for other in unused
        )
        notice = (
            f"개인번호 명단 중 자동 연결에 사용하지 않은 {len(unused)}명: {detail}. "
            "지급표와 대조하세요. 명단만 보고 충전 행을 추가하지 않았습니다."
        )
        notice += "\n미사용 명단 원문: " + json.dumps(
            [_source(row) for row in unused], ensure_ascii=False
        )
    return PairedRequest(rows, notice)
