from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ADMIN_PASSWORD = "changeme"
DEFAULT_SECRET_KEY = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    secret_key: str = DEFAULT_SECRET_KEY
    ai_provider: str = "mock"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    mock_table_json: str = ""
    database_path: str = "data/pointbook.db"

    # 보안 하드닝
    enforce_secure_defaults: bool = False
    login_max_attempts: int = 5
    login_lockout_seconds: int = 300
    max_upload_mb: int = 10
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_secure: bool = False

    def security_warnings(self) -> list[str]:
        """운영 전에 반드시 확인해야 할 보안 설정 문제를 목록으로 반환한다."""
        warnings: list[str] = []
        if self.secret_key == DEFAULT_SECRET_KEY:
            warnings.append("SECRET_KEY가 기본값입니다. .env에서 임의의 긴 문자열로 변경하세요.")
        if self.admin_password == DEFAULT_ADMIN_PASSWORD:
            warnings.append("ADMIN_PASSWORD가 기본값입니다. .env에서 변경하세요.")
        if self.ai_provider == "gemini" and not self.gemini_api_key:
            warnings.append("AI_PROVIDER=gemini인데 GEMINI_API_KEY가 비어 있습니다.")
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
