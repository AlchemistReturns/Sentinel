import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql://sentinel:sentinel@localhost:5433/sentinel"
    )
    redis_url: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    langchain_tracing_v2: bool = os.environ.get("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    langchain_api_key: str = os.environ.get("LANGCHAIN_API_KEY", "")
    langchain_project: str = os.environ.get("LANGCHAIN_PROJECT", "sentinel")
    embedding_model: str = os.environ.get("SENTINEL_EMBEDDING_MODEL", "text-embedding-3-small")
    agent_model: str = os.environ.get("SENTINEL_AGENT_MODEL", "gpt-4.1-mini")

    @property
    def pgvector_url(self) -> str:
        # SQLAlchemy needs an explicit driver; we use psycopg3, not the psycopg2 default.
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


settings = Settings()
