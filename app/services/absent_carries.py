"""비재직 잔액을 입력 당시 인원·월에 연결하고 적용할 수 없는 원문을 보존한다."""

from dataclasses import dataclass

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.datastructures import FormData

from app.config import get_settings
from app.services.sync import ABSENT_ACTIONS, PersonChange


def _serializer(salt: str) -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret_key, salt=salt)


def identity(month: str, change: PersonChange) -> dict[str, str | int | None]:
    return {
        "month": month,
        "person_id": change.person_id,
        "point_no": change.point_no,
        "name": change.name,
        "personal_no": change.personal_no,
        "account_type": change.account_type,
        "action": change.action,
    }


def _decode_binding(token: str) -> dict[str, str | int | None] | None:
    try:
        value = _serializer("monthly-absent-identity-v1").loads(token)
    except BadSignature:
        return None
    return value if isinstance(value, dict) else None


def matches_binding(token: str, month: str, change: PersonChange) -> bool:
    return _decode_binding(token) == identity(month, change)


def bindings_from_form(form: FormData) -> dict[str, str]:
    return {
        key.removeprefix("absent_binding_"): str(value)
        for key, value in form.multi_items()
        if key.startswith("absent_binding_")
    }


def preserved_from_form(form: FormData) -> list[str]:
    return [str(value) for value in form.getlist("preserved_absent_carry")]


def preserved_entries(tokens: list[str]) -> list[dict[str, str]]:
    entries = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        try:
            entry = _serializer("monthly-absent-preserved-v1").loads(token)
        except BadSignature:
            continue
        if not isinstance(entry, dict) or not all(isinstance(v, str) for v in entry.values()):
            continue
        if set(entry) != {"point_no", "name", "month", "value", "reason"}:
            continue
        entries.append({**entry, "token": token})
        seen.add(token)
    return entries


@dataclass
class AbsentCarries:
    values: dict[str, str]
    bindings: dict[str, str]
    preserved: list[str]


def reconcile(
    month: str,
    changes: list[PersonChange] | None,
    values: dict[str, str],
    bindings: dict[str, str],
    preserved: list[str],
) -> AbsentCarries:
    """오류로 분석하지 못한 경우 원문을 유지한다. 유효한 새 대상에 재결합하지 않는다."""
    kept = [entry["token"] for entry in preserved_entries(preserved)]
    if changes is None:
        return AbsentCarries(dict(values), dict(bindings), kept)
    targets = {c.point_no: c for c in changes if c.action in ABSENT_ACTIONS}
    current: dict[str, str] = {}
    current_bindings: dict[str, str] = {}
    for point, value in values.items():
        token = bindings.get(point, "")
        target = targets.get(point)
        if target is not None and matches_binding(token, month, target):
            current[point] = value
            continue
        if value:
            old = _decode_binding(token) or {}
            entry = {
                "point_no": str(old.get("point_no") or point),
                "name": str(old.get("name") or "인원 연결 미확인"),
                "month": str(old.get("month") or "월 연결 미확인"),
                "value": value,
                "reason": "새 분석에서 제외된 입력"
                if target is None
                else "인원·월·재직 구분의 연결을 확인할 수 없어 재입력이 필요한 입력",
            }
            preserved_token = _serializer("monthly-absent-preserved-v1").dumps(entry)
            if preserved_token not in kept:
                kept.append(preserved_token)
    for point, change in targets.items():
        current.setdefault(point, "")
        current_bindings[point] = _serializer("monthly-absent-identity-v1").dumps(
            identity(month, change)
        )
    return AbsentCarries(current, current_bindings, kept)
