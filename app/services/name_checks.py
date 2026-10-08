"""독립 이름 재판독을 대조한다. 후보는 이름/번호/장부를 자동 변경하지 않는다."""

import json
from collections import Counter, defaultdict
from dataclasses import replace

from app.services.identifiers import normalize_point_no
from app.services.import_notes import EVIDENCE_MARKER
from app.services.parsing import RawRequestRow
from app.services.validation import parse_money

GRADES = {"경": "소방경", "위": "소방위", "장": "소방장", "교": "소방교", "사": "소방사"}
MAX_NAME_CANDIDATES = 5


def normalized_grade(value: str) -> str:
    value = value.strip()
    return GRADES.get(value, value)


def similar_final_consonant(left: str, right: str) -> bool:
    """같은 길이 이름의 한 음절에서 종성만 다른지 판단한다. 자동 연결에는 사용하지 않는다."""
    left, right = left.strip(), right.strip()
    if not left or len(left) != len(right):
        return False
    different = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
    if len(different) != 1:
        return False
    a, b = (ord(char) - 0xAC00 for char in different[0])
    return 0 <= a <= 11171 and 0 <= b <= 11171 and a // 28 == b // 28


def _anchor(row: RawRequestRow, *, roster: bool) -> tuple[str, ...] | None:
    """이름을 제외한 표 안의 근거. 단순 행 위치로는 대응하지 않는다."""
    if row.source_issue or not row.name.strip():
        return None
    if row.point_no.strip():
        try:
            return ("point", normalize_point_no(row.point_no))
        except ValueError:
            return None
    if not row.team.strip() or not row.grade.strip():
        return None
    profile = (row.team.strip(), normalized_grade(row.grade))
    if roster:
        if not row.personal_no.strip():
            return None
        return ("roster", *profile, row.personal_no.strip())
    try:
        amount = str(parse_money(row.amount))
    except ValueError:
        return None
    return ("payment", *profile, amount, row.personal_no.strip())


def _evidence(row: RawRequestRow) -> dict[str, str]:
    return {
        key: getattr(row, key)
        for key in ("name", "team", "grade", "personal_no", "point_no", "amount")
    }


def require_name_review(
    row: RawRequestRow, message: str, candidates: list[RawRequestRow] | None = None
) -> RawRequestRow:
    result = replace(row)
    result.source_warning = "\n".join(filter(None, [row.source_warning, message]))
    result.source_issue = " ".join(filter(None, [row.source_issue, message]))
    if candidates:
        result.source_line = json.dumps(
            {
                "처음 인식 원문": row.source_line or _evidence(row),
                "이름 재확인 후보": [
                    _evidence(candidate) for candidate in candidates[:MAX_NAME_CANDIDATES]
                ],
                "후보 수": len(candidates),
            },
            ensure_ascii=False,
        )
    return result


def name_check_unavailable(rows: list[RawRequestRow]) -> list[RawRequestRow]:
    return [
        require_name_review(
            row, "이름 재확인을 완료하지 못했습니다. 원본 사진에서 이름을 직접 확인하세요."
        )
        for row in rows
    ]


def compare_name_reads(
    original: list[RawRequestRow],
    checked: list[RawRequestRow],
    *,
    roster: bool = False,
    notices: list[str] | None = None,
) -> list[RawRequestRow]:
    """순서 변경은 허용하되 행 수·근거·이름의 유일 대응이 맞을 때만 불일치를 해제한다."""
    first: dict[tuple[str, ...], list[RawRequestRow]] = defaultdict(list)
    second: dict[tuple[str, ...], list[RawRequestRow]] = defaultdict(list)
    for rows, groups in ((original, first), (checked, second)):
        for row in rows:
            anchor = _anchor(row, roster=roster)
            if anchor is not None:
                groups[anchor].append(row)
    first_counts = {
        anchor: Counter(row.name.strip() for row in rows) for anchor, rows in first.items()
    }
    second_counts = {
        anchor: Counter(row.name.strip() for row in rows) for anchor, rows in second.items()
    }
    unmatched = [row for row in checked if _anchor(row, roster=roster) not in first]
    if notices is not None and unmatched:
        label = "개인번호 명단" if roster else "지급표"
        notices.append(
            f"{label} 이름 재확인에서 기존 행과 대응하지 못한 {len(unmatched)}행이 있습니다. "
            "아래 원문을 대조하세요. 이 결과로 요청서 행을 추가하거나 이름을 바꾸지 않았습니다."
            + EVIDENCE_MARKER
            + json.dumps(
                {f"{label} 이름 재판독 미대응 행": [_evidence(row) for row in unmatched]},
                ensure_ascii=False,
            )
        )
    result = []
    for row in original:
        anchor = _anchor(row, roster=roster)
        candidates = second.get(anchor, []) if anchor is not None else []
        initial = first.get(anchor, []) if anchor is not None else []
        same_count = len(original) == len(checked) and len(initial) == len(candidates)
        first_names = first_counts.get(anchor, Counter()) if anchor is not None else Counter()
        second_names = second_counts.get(anchor, Counter()) if anchor is not None else Counter()
        if (
            same_count
            and row.name.strip()
            and first_names[row.name.strip()] == second_names[row.name.strip()] == 1
        ):
            result.append(replace(row))
            continue
        if same_count and len(initial) == len(candidates) == 1:
            message = f"이름 확인: 처음 인식 ‘{row.name}’ / 재확인 ‘{candidates[0].name}’. 원본 사진과 대조해 사용할 이름을 확인하세요."
        else:
            names = ", ".join(other.name for other in candidates[:MAX_NAME_CANDIDATES])
            message = "이름 재확인의 행 수·팀·계급·번호/금액이 다르거나 중복됩니다. 원본에서 이름과 행을 확인하세요."
            if names:
                message += f" 같은 근거의 후보: {names}" + (
                    f" 외 {len(candidates) - MAX_NAME_CANDIDATES}명"
                    if len(candidates) > MAX_NAME_CANDIDATES
                    else ""
                )
        result.append(require_name_review(row, message, candidates))
    return result
