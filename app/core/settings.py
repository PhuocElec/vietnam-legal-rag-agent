from pydantic import Json, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    # ===== App Config =====
    APP_NAME: str = "vietnam-legal-rag-agent"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000
    WORKERS: int = 1

    # ===== Auth Config =====
    API_KEYS: Optional[Json[List[str]]] = Field(default="[]")

    # ===== LLM Config =====
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()