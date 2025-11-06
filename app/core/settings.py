from pydantic import Json, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    # ===== App =====
    APP_NAME: str = "vietnam-legal-rag-agent"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000
    WORKERS: int = 1

    # ===== Auth =====
    API_KEYS: Optional[Json[List[str]]] = Field(default="[]")

    # ===== LLM =====
    LLM_PROVIDER: str = "groq"
    LLM_MODEL: str = "openai/gpt-oss-120b"
    LLM_API_KEY: Optional[str] = None

    # ===== Embedding =====
    EMBEDDING_PROVIDER: str = "openai"
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    EMBEDDING_API_KEY: Optional[str] = None

    # ===== Vector Store =====
    FAISS_INDEX_DIR: str = "data/faiss_index"
    FAISS_METRIC: str = "cosine"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()