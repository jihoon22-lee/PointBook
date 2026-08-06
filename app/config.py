from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    admin_username: str = "admin"
    admin_password: str = "changeme"
    secret_key: str = "dev-secret-change-me"
    ai_provider: str = "mock"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    mock_table_json: str = ""
    database_path: str = "data/pointbook.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
