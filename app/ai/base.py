from abc import ABC, abstractmethod
from uuid import uuid4

from app.logging import get_logger
from app.services.parsing import RawRequestRow


class VisionError(ValueError):
    """외부 응답·키를 포함하지 않는 오류와 공개 가능한 상관 ID."""

    def __init__(self, message: str, *, code: str = "vision_error") -> None:
        self.reference = uuid4().hex[:12]
        get_logger("pointbook.vision").warning("AI 실패 code=%s reference=%s", code, self.reference)
        super().__init__(
            f"{message} 붙여넣기나 수동 입력을 이용할 수 있습니다. (문의 ID: {self.reference})"
        )


class VisionProvider(ABC):
    """사진 → 원문 행. 제안은 검수 화면의 공통 검증을 통과해야 확정할 수 있다."""

    @abstractmethod
    def extract_table(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        """오류·불완전 행도 순서대로 보존하며 장부에 쓰지 않는다."""
