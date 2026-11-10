from collections.abc import Callable

from app.ai.base import VisionError, VisionProvider
from app.ai.fallback import FallbackProvider
from app.ai.gemini import GeminiProvider
from app.ai.mock import MockProvider
from app.ai.openai import OpenAIProvider
from app.config import Settings, get_settings

LABELS = {"gemini": "Gemini", "openai": "OpenAI"}


def _builder(settings: Settings, name: str) -> Callable[[float], VisionProvider]:
    if name == "gemini":
        return lambda timeout: GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout=timeout,
            max_response_bytes=settings.ai_max_response_bytes,
        )
    return lambda timeout: OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout=timeout,
        max_response_bytes=settings.ai_max_response_bytes,
    )


def provider_labels(settings: Settings) -> tuple[str, str]:
    """화면 안내용 (기본, 대체) 이름. 대체는 그 제공자의 키가 있을 때만 있다."""
    primary = settings.ai_provider
    if primary not in LABELS:
        return "", ""
    other = "openai" if primary == "gemini" else "gemini"
    key = settings.openai_api_key if other == "openai" else settings.gemini_api_key
    return LABELS[primary], LABELS[other] if key.strip() else ""


def get_provider() -> VisionProvider:
    settings = get_settings()
    if settings.ai_provider in LABELS:
        primary_label, secondary_label = provider_labels(settings)
        primary = _builder(settings, settings.ai_provider)(settings.ai_timeout_seconds)
        if not secondary_label:
            return primary
        # 다른 제공자의 키가 있으면 기본 제공자의 한도·혼잡·연결 실패를 이어받는다.
        other = "openai" if settings.ai_provider == "gemini" else "gemini"
        return FallbackProvider(
            primary,
            _builder(settings, other),
            timeout=settings.ai_timeout_seconds,
            primary_label=primary_label,
            secondary_label=secondary_label,
        )
    if settings.ai_provider == "mock" and settings.app_env in {"development", "test"}:
        return MockProvider(mock_json=settings.mock_table_json)
    raise VisionError(
        "AI 제공자 설정이 올바르지 않습니다. 운영 모드에서는 Gemini 또는 OpenAI를 설정하세요.",
        code="provider_config",
    )
