"""
config.py — All application settings loaded from environment variables.
Never hardcode secrets here; use .env or system environment.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (Google Gemini)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # Database
    database_url: str = "sqlite+aiosqlite:///./autoanalyst.db"

    # Auth
    jwt_secret_key: str = "CHANGE_ME"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Cache
    cache_ttl_seconds: int = 300
    cache_max_size: int = 512

    # Orchestration
    max_retries: int = 3
    query_row_limit: int = 1000
    query_timeout_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
