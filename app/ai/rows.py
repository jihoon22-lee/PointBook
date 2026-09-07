"""AI와 Mock의 동일한 원문/스키마 계약."""

import json

from app.ai.base import VisionError
from app.services.parsing import MAX_REQUEST_ROWS, ROW_FIELDS, RawRequestRow


def _reject_constant(value: str) -> None:
    raise ValueError("JSON 상수 오류")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 중복 필드")
        result[key] = value
    return result


def load_json(text: str) -> object:
    try:
        return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise VisionError("AI 응답 JSON을 파싱할 수 없습니다.", code="invalid_json") from None


def parse_rows(text: str) -> list[RawRequestRow]:
    data = load_json(text)
    if not isinstance(data, list) or not data:
        raise VisionError("AI가 요청서 행 배열을 반환하지 않았습니다.", code="empty_rows")
    if len(data) > MAX_REQUEST_ROWS:
        raise VisionError(f"AI 응답이 최대 {MAX_REQUEST_ROWS}행을 초과했습니다.", code="row_limit")
    rows = []
    for index, item in enumerate(data, 1):
        source = json.dumps(item, ensure_ascii=False)
        if not isinstance(item, dict):
            rows.append(
                RawRequestRow(
                    source_line=source,
                    source_issue=f"AI {index}행: 행 객체가 아닙니다. 원문을 확인하세요.",
                )
            )
            continue
        values = {}
        issues = []
        for key in ROW_FIELDS:
            value = item.get(key, "person" if key == "account_type" else "")
            if value is None:
                values[key] = ""
            elif isinstance(value, str):
                values[key] = value
            elif key == "amount" and type(value) is int:
                values[key] = str(value)
            else:
                values[key] = json.dumps(value, ensure_ascii=False)
                issues.append(f"{key} 필드의 자료형을 확인하세요.")
        if set(item) - set(ROW_FIELDS):
            issues.append("알 수 없는 필드가 있습니다. 원문을 확인하세요.")
        row = RawRequestRow(**values, source_line=source)
        try:
            row.validated()
        except ValueError as exc:
            # The validator only generates fixed field messages, never external errors.
            issues.append(str(exc))
        row.source_issue = f"AI {index}행: " + " ".join(issues) if issues else ""
        rows.append(row)
    return rows
