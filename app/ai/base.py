from abc import ABC, abstractmethod

from app.services.sync import RequestRow


class VisionProvider(ABC):
    """요청서 사진 → 테이블 행 추출을 추상화한 인터페이스.

    실제 프로바이더(Gemini/GPT-4o 등)는 API 키 확보 후 구현체를 추가한다.
    """

    @abstractmethod
    def extract_table(self, image_bytes: bytes, filename: str) -> list[RequestRow]:
        """이미지에서 요청서 테이블을 읽어 행 목록을 반환한다."""
