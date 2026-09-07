import re
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def current_month() -> str:
    """한국 시간(KST) 기준 현재 연월 (YYYY-MM). UTC 사용 시 월초 새벽에 전 달로 잘못 잡히는 문제 방지."""
    return datetime.now(KST).strftime("%Y-%m")


def validate_month(value: str) -> str:
    """ASCII YYYY-MM, 실제 연도 0001~9999와 월 01~12만 허용한다."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("처리 월은 YYYY-MM 형식의 실제 연월이어야 합니다.")
    if value[:4] == "0000":
        raise ValueError("처리 연도는 0001년부터 9999년까지 입력해 주세요.")
    return value
