from app.ai.base import VisionError, VisionProvider
from app.ai.gemini import GeminiProvider
from app.ai.mock import MockProvider
from app.config import get_settings


def get_provider() -> VisionProvider:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout=settings.ai_timeout_seconds,
            max_response_bytes=settings.ai_max_response_bytes,
        )
    if settings.ai_provider == "mock" and settings.app_env in {"development", "test"}:
        return MockProvider(mock_json=settings.mock_table_json)
    raise VisionError(
        "AI 제공자 설정이 올바르지 않습니다. 운영 모드에서는 Gemini를 설정하세요.",
        code="provider_config",
    )
