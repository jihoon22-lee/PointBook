"""월간 요청서와 현재 프로필의 차이 및 명시적인 선택을 보존한다."""

from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings
from app.models import Person
from app.services.parsing import RawRequestRow

PROFILE_FIELDS = ("name", "personal_no", "team", "grade", "account_type")


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret_key, salt="monthly-profile-choices-v1")


def values(row: RawRequestRow) -> dict[str, str]:
    return {key: str(getattr(row, key)).strip() for key in PROFILE_FIELDS}


def current_values(person: Person) -> dict[str, str]:
    return {
        "name": person.name.strip(),
        "personal_no": (person.personal_no or "").strip(),
        "team": person.team.name if person.team else "",
        "grade": person.grade or "",
        "account_type": person.account_type,
    }


def decode(row: RawRequestRow) -> dict[str, Any] | None:
    try:
        state = _serializer().loads(row.profile_review)
    except BadSignature:
        return None
    if not isinstance(state, dict) or state.get("row_id") != row.row_id:
        return None
    return state


def expected_values(state: dict[str, Any]) -> dict[str, str]:
    return {
        key: state["base"][key]
        if state["choices"].get(key) == "current"
        else state["incoming"][key]
        for key in PROFILE_FIELDS
    }


def original_values(row: RawRequestRow, state: dict[str, Any]) -> dict[str, str]:
    actual, expected = values(row), expected_values(state)
    return {
        key: actual[key] if actual[key] != expected[key] else state["incoming"][key]
        for key in PROFILE_FIELDS
    }


def reset_target(row: RawRequestRow) -> None:
    state = decode(row)
    if state:
        for key, value in original_values(row, state).items():
            setattr(row, key, value)
    row.profile_review = ""


def prepare(row: RawRequestRow, person: Person) -> dict[str, dict[str, Any]]:
    base, actual = current_values(person), values(row)
    state = decode(row)
    if state and (state["person_id"] != person.id or state["point_no"] != person.point_no):
        reset_target(row)
        actual, state = values(row), None
        row.carry = ""
    unchanged = state is not None and state["base"] == base and expected_values(state) == actual
    if state is None:
        state = {
            "row_id": row.row_id,
            "person_id": person.id,
            "point_no": person.point_no,
            "base": base,
            "incoming": actual,
            "choices": {},
        }
    else:
        incoming = original_values(row, state)
        if any(incoming[key] != state["incoming"][key] for key in ("name", "personal_no")):
            row.carry = ""
        choices = dict(state["choices"])
        if state["base"] != base:
            choices = {}
            row.carry = ""
        else:
            for key in PROFILE_FIELDS:
                if incoming[key] != state["incoming"][key]:
                    choices.pop(key, None)
        state.update(base=base, incoming=incoming, choices=choices)
    for key, value in expected_values(state).items():
        setattr(row, key, value)
    if not unchanged:
        row.profile_review = _serializer().dumps(state)
    return {
        key: {
            "current": base[key],
            "incoming": state["incoming"][key],
            "choice": state["choices"].get(key, ""),
            "pending": key not in state["choices"],
            "incoming_allowed": key != "account_type",
        }
        for key in PROFILE_FIELDS
        if base[key] != state["incoming"][key]
    }


def choose(row: RawRequestRow, person: Person, field: str, side: str) -> None:
    state = decode(row)
    if (
        state is None
        or state["person_id"] != person.id
        or state["point_no"] != person.point_no
        or row.point_no != person.point_no
        or state["base"] != current_values(person)
        or expected_values(state) != values(row)
    ):
        raise ValueError("비교할 정보가 변경되었습니다. 새로 표시된 값을 확인하고 다시 선택하세요.")
    if field not in PROFILE_FIELDS or side not in {"current", "incoming"}:
        raise ValueError("선택할 항목과 값을 확인하세요.")
    if (
        field == "account_type"
        and side == "incoming"
        and state["base"][field] != state["incoming"][field]
    ):
        raise ValueError("유형 변경은 인원 편집에서 확인해 주세요.")
    before = values(row)
    state["choices"][field] = side
    for key, value in expected_values(state).items():
        setattr(row, key, value)
    if any(before[key] != values(row)[key] for key in ("name", "personal_no")):
        row.carry = ""
    row.profile_review = _serializer().dumps(state)
