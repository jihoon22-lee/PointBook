import json

from app.ai.base import VisionProvider
from app.ai.rows import parse_rows
from app.services.parsing import RawRequestRow

DEFAULT_ROWS = [
    {
        "point_no": "00000001",
        "personal_no": "101",
        "name": "김소방",
        "team": "1팀",
        "grade": "소방경",
        "amount": 50000,
    },
    {
        "point_no": "00000002",
        "personal_no": "102",
        "name": "이소방",
        "team": "1팀",
        "grade": "소방위",
        "amount": 50000,
    },
    {
        "point_no": "00000003",
        "personal_no": "103",
        "name": "박소방",
        "team": "2팀",
        "grade": "소방사",
        "amount": 30000,
    },
]


class MockProvider(VisionProvider):
    """개발용 Mock 프로바이더.

    MOCK_TABLE_JSON 환경변수로 반환할 테이블을 지정할 수 있다.
    (실제 AI 키 없이도 전체 플로우를 검증하기 위함)
    """

    def __init__(self, mock_json: str = "") -> None:
        self._mock_json = mock_json

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return parse_rows(self._mock_json or json.dumps(DEFAULT_ROWS, ensure_ascii=False))
