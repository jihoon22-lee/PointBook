"""이월 입력 상태. 직전 잔액 상한은 월간 비재직 대상에만 적용한다."""

from dataclasses import dataclass
from typing import Literal

from app.services.validation import parse_balance


def parse_carry_input(value: str, *, previous_total: int | None = None) -> int:
    amount = parse_balance(value, label="이월 잔액")
    if previous_total is not None and amount > previous_total:
        raise ValueError(
            f"이월 잔액이 직전 총 잔액보다 {amount - previous_total:,}원 많습니다. "
            f"직전 총 잔액 {previous_total:,}원 / 입력 금액 {amount:,}원. "
            "원본 금액과 이전 장부를 확인해 주세요."
        )
    return amount


@dataclass(frozen=True)
class CarryInputState:
    status: Literal["missing", "complete", "error"]
    message: str = ""


def carry_input_state(value: str, *, previous_total: int | None = None) -> CarryInputState:
    if not value.strip():
        return CarryInputState("missing")
    try:
        parse_carry_input(value, previous_total=previous_total)
    except ValueError as exc:
        return CarryInputState("error", str(exc))
    return CarryInputState("complete")
