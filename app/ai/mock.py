import json
from typing import Any

from app.ai.base import VisionProvider
from app.services.sync import RequestRow

DEFAULT_ROWS = [
    {"personal_no": "101", "name": "김소방", "team": "1팀", "grade": "소방경", "amount": 50000},
    {"personal_no": "102", "name": "이소방", "team": "1팀", "grade": "소방위", "amount": 50000},
    {"personal_no": "103", "name": "박소방", "team": "2팀", "grade": "소방사", "amount": 30000},
]


class MockProvider(VisionProvider):
    """개발용 Mock 프로바이더.

    MOCK_TABLE_JSON 환경변수로 반환할 테이블을 지정할 수 있다.
    (실제 AI 키 없이도 전체 플로우를 검증하기 위함)
    """

    def __init__(self, mock_json: str = "") -> None:
        self._mock_json = mock_json

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RequestRow]:
        data: list[dict[str, Any]] = (
            json.loads(self._mock_json) if self._mock_json else DEFAULT_ROWS
        )
        return [
            RequestRow(
                personal_no=str(r.get("personal_no", "")),
                name=str(r.get("name", "")),
                team=str(r.get("team", "")),
                grade=str(r.get("grade", "")),
                amount=int(r.get("amount", 0) or 0),
                note=str(r.get("note", "")),
            )
            for r in data
        ]
