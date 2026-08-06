from app.ai.base import VisionProvider
from app.ai.mock import MockProvider
from app.config import get_settings


def get_provider() -> VisionProvider:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        raise NotImplementedError("Gemini 프로바이더는 API 키 확보 후 구현 예정입니다.")
    if settings.ai_provider == "openai":
        raise NotImplementedError("OpenAI 프로바이더는 API 키 확보 후 구현 예정입니다.")
    return MockProvider(mock_json=settings.mock_table_json)
