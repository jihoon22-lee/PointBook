from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def current_month() -> str:
    """한국 시간(KST) 기준 현재 연월 (YYYY-MM). UTC 사용 시 월초 새벽에 전 달로 잘못 잡히는 문제 방지."""
    return datetime.now(KST).strftime("%Y-%m")
