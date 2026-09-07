import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ADMIN_PASSWORD = "changeme"
DEFAULT_SECRET_KEY = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True
    )

    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    secret_key: str = DEFAULT_SECRET_KEY
    ai_provider: str = "mock"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    mock_table_json: str = ""
    database_path: str = "data/pointbook.db"
    app_env: Literal["development", "test", "production"] = "development"
    backup_dir: str = ""
    instance_notice: str = Field(default="", max_length=160)

    # 보안 하드닝
    enforce_secure_defaults: bool = False
    login_max_attempts: int = Field(default=5, ge=1, le=100)
    login_lockout_seconds: int = Field(default=300, ge=1, le=86400)
    max_upload_mb: int = Field(default=10, ge=1, le=20)
    ai_max_concurrency: int = Field(default=2, ge=1, le=8)
    ai_timeout_seconds: float = Field(default=90, ge=1, le=300)
    ai_max_response_bytes: int = Field(default=2_000_000, ge=1024, le=8_000_000)
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_secure: bool = False
    draft_keep_days: int = Field(default=30, ge=1, le=365)
    draft_max_active: int = Field(default=20, ge=1, le=100)
    backup_keep: int = Field(default=30, ge=1, le=10000)

    @model_validator(mode="before")
    @classmethod
    def deployment_settings(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        deployment_keys = {
            "pointbook_env_file",
            "pointbook_data_dir",
            "pointbook_backup_dir",
            "pointbook_image",
            "pointbook_version",
            "pointbook_deploy_image",
            "pointbook_uid",
            "pointbook_gid",
            "pointbook_port",
            "compose_project_name",
            "forwarded_allow_ips",
        }
        return {key: value for key, value in values.items() if key.lower() not in deployment_keys}

    @model_validator(mode="after")
    def validate_cookie_policy(self) -> "Settings":
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=none 쿠키에는 COOKIE_SECURE=true가 필요합니다.")
        return self

    def validate_runtime(self) -> None:
        """초기 관리자 환경 암호와 실제 DB의 인증 상태를 구분한다."""
        if self.ai_provider not in {"mock", "gemini"}:
            raise ValueError("AI_PROVIDER는 mock 또는 gemini여야 합니다.")
        if self.ai_provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")
        if (self.app_env == "production" or self.enforce_secure_defaults) and (
            not self.secret_key.strip() or self.secret_key == DEFAULT_SECRET_KEY
        ):
            raise ValueError("운영 SECRET_KEY에 기본값 또는 빈 값을 사용할 수 없습니다.")
        if self.app_env == "production" and self.ai_provider == "mock":
            raise ValueError("운영 모드에서는 Mock AI를 사용할 수 없습니다.")

    def security_warnings(self) -> list[str]:
        """운영 전에 반드시 확인해야 할 보안 설정 문제를 목록으로 반환한다."""
        warnings: list[str] = []
        if not self.secret_key.strip() or self.secret_key == DEFAULT_SECRET_KEY:
            warnings.append("SECRET_KEY가 기본값입니다. .env에서 임의의 긴 문자열로 변경하세요.")
        if self.admin_password == DEFAULT_ADMIN_PASSWORD:
            warnings.append("ADMIN_PASSWORD가 기본값입니다. .env에서 변경하세요.")
        if self.ai_provider == "gemini" and not self.gemini_api_key:
            warnings.append("AI_PROVIDER=gemini인데 GEMINI_API_KEY가 비어 있습니다.")
        return warnings


@lru_cache
def get_settings() -> Settings:
    selected = os.environ.get("POINTBOOK_ENV_FILE")
    env_file = Path(selected or ".env")
    if selected and not env_file.is_file():
        raise ValueError("선택한 환경 파일이 없습니다.")
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    values = {**file_values, **os.environ}
    defaults: dict[str, Any] = {}
    data_dir = values.get("POINTBOOK_DATA_DIR")
    if "DATABASE_PATH" not in values and data_dir:
        defaults["database_path"] = str(Path(data_dir) / "pointbook.db")
    if "BACKUP_DIR" not in values and values.get("POINTBOOK_BACKUP_DIR"):
        defaults["backup_dir"] = str(values["POINTBOOK_BACKUP_DIR"])
    return Settings(_env_file=env_file, **defaults)  # type: ignore[call-arg]
