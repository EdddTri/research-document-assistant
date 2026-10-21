from functools import lru_cache
from pathlib import Path
from typing import Literal
from sqlalchemy.engine import URL

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Terminal services explicitly load the shared root .env; Compose supplies env directly.
    model_config = SettingsConfigDict(extra="ignore")
    database_url: str = Field(default="", repr=False)  # Explicit deployment/test override.
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "research"
    postgres_password: str = Field(default="", repr=False)
    backend_db_password: str = Field(default="", repr=False)
    worker_db_password: str = Field(default="", repr=False)
    agent_db_password: str = Field(default="", repr=False)
    redis_port: int = Field(default=6379, ge=1, le=65535)
    broker_url: str = ""
    storage_root: Path = Path("./storage")
    jwt_secret: str = Field(min_length=32)
    internal_secret: str = Field(min_length=32)
    jwt_issuer: str = "research-backend"
    jwt_audience: str = "research-frontend"
    access_minutes: int = 10
    session_days: int = 30
    cookie_secure: bool = True
    frontend_origin: str = "http://localhost:5173"
    agent_url: str = "http://localhost:8001"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = Field(default=4, ge=1, le=32)
    openai_api_key: str = Field(default="", repr=False)
    # Retained to reject stale provider overrides explicitly; OpenAI is the only backend.
    answer_provider: Literal["openai"] = "openai"
    vision_provider: Literal["openai"] = "openai"
    answer_model: str = ""
    vision_model: str = ""
    answer_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = "low"
    vision_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = "none"
    vision_timeout_seconds: int = Field(default=120, ge=30, le=900)
    vision_max_tokens: int = Field(default=4096, ge=256, le=16384)
    vision_min_interval_seconds: float = Field(default=0, ge=0, le=120)
    vision_split_max_depth: int = Field(default=3, ge=0, le=4)
    vision_split_min_side: int = Field(default=192, ge=64, le=1024)
    vision_split_overlap: float = Field(default=.08, ge=0, le=.2)
    judge_provider: Literal["openai"] | None = None
    judge_model: str = ""
    retrieval_mode: str = "hybrid"
    trace_document_content: bool = False

    def database_url_for(self, role: str = "backend") -> str:
        passwords = {"backend": self.backend_db_password, "worker": self.worker_db_password,
                     "agent": self.agent_db_password, "migration": self.postgres_password}
        if role not in passwords:
            raise ValueError("Unknown database role")
        if self.database_url:
            return self.database_url
        if not passwords[role]:
            key = "POSTGRES_PASSWORD" if role == "migration" else f"{role.upper()}_DB_PASSWORD"
            raise ValueError(f"{key} is required in the root .env")
        return URL.create("postgresql+psycopg", username=f"research_{role}", password=passwords[role],
                          host=self.db_host, port=self.db_port, database=self.db_name,
                          query={"connect_timeout": "10"}).render_as_string(hide_password=False)

    @field_validator("judge_provider", "answer_reasoning_effort", "vision_reasoning_effort", mode="before")
    @classmethod
    def blank_optional_setting(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_dimensions(self):
        self.broker_url = self.broker_url or f"redis://127.0.0.1:{self.redis_port}/0"
        self.answer_model = self.answer_model.strip() or "gpt-5.4-mini"
        self.vision_model = self.vision_model.strip() or "gpt-5.4-mini"
        self.judge_provider = "openai"
        self.judge_model = self.judge_model.strip() or self.answer_model
        if self.embedding_model == "bge-m3:latest":
            self.embedding_model = "bge-m3"
        if self.embedding_model != "bge-m3" or self.embedding_dimensions != 1024:
            raise ValueError("This deployment uses Ollama bge-m3 with 1024 dimensions.")
        if self.retrieval_mode not in {"vector", "hybrid"}:
            raise ValueError("retrieval_mode must be vector or hybrid")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
