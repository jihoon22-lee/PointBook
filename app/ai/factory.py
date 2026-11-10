from app.ai.base import VisionError, VisionProvider
from app.ai.fallback import FallbackProvider
from app.ai.gemini import GeminiProvider
from app.ai.mock import MockProvider
from app.ai.openai import OpenAIProvider
from app.config import get_settings


def get_provider() -> VisionProvider:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        gemini = GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout=settings.ai_timeout_seconds,
            max_response_bytes=settings.ai_max_response_bytes,
        )
        if not settings.openai_api_key.strip():
            return gemini
        # OPENAI_API_KEY가 있으면 Gemini의 한도·혼잡·연결 실패를 OpenAI가 이어받는다.
        return FallbackProvider(
            gemini,
            lambda remaining: OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout=remaining,
                max_response_bytes=settings.ai_max_response_bytes,
            ),
            timeout=settings.ai_timeout_seconds,
        )
    if settings.ai_provider == "mock" and settings.app_env in {"development", "test"}:
        return MockProvider(mock_json=settings.mock_table_json)
    raise VisionError(
        "AI 제공자 설정이 올바르지 않습니다. 운영 모드에서는 Gemini를 설정하세요.",
        code="provider_config",
    )
