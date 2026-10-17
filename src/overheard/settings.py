from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    """Configuration from environment variables first, then `.env` when present.

    Everything has a default except `DATABASE_URL`. Secrets are `SecretStr`, so printing
    the settings never shows their values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: SecretStr | None = None

    serpapi_api_key: SecretStr | None = None
    serpapi_daily_cap: int | None = None
    serpapi_monthly_cap: int | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: SecretStr | None = None
    email_from: str | None = None
    email_mode: Literal["smtp", "file", "off"] | None = None

    llm_base_url: str = "http://127.0.0.1:11434"
    llm_api_key: SecretStr | None = None
    chat_model: str = "gemma4:e4b"
    embed_model: str = "embeddinggemma:300m"

    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr | None = None
    temporal_ui_url: str = "http://127.0.0.1:8233"

    overheard_host: str = "127.0.0.1"
    overheard_port: int = 8000
    overheard_password: SecretStr | None = None
    overheard_db_schema: str = "overheard"
    overheard_data_dir: Path = Path(".data")
    overheard_offline: bool = False

    @model_validator(mode="after")
    def _derive_defaults(self) -> "Settings":
        if self.email_from is None:
            self.email_from = self.smtp_user
        if self.email_mode is None:
            self.email_mode = "smtp" if self.smtp_host else "off"
        if not self.overheard_db_schema.isidentifier():
            raise ValueError("OVERHEARD_DB_SCHEMA must be a plain identifier")
        return self

    @property
    def dsn(self) -> str:
        """The Postgres connection string.

        Returns:
            The value of `DATABASE_URL`.

        Raises:
            RuntimeError: If `DATABASE_URL` is not set.
        """
        if self.database_url is None or not self.database_url.get_secret_value():
            raise RuntimeError("DATABASE_URL is not set. Put it in .env or the environment.")
        return self.database_url.get_secret_value()

    @property
    def is_local_host(self) -> bool:
        """Whether the web app binds to a loopback address."""
        return self.overheard_host in LOCAL_HOSTS

    @property
    def app_url(self) -> str:
        """The address people open in a browser."""
        host = "127.0.0.1" if self.overheard_host in {"0.0.0.0", "::"} else self.overheard_host  # noqa: S104
        return f"http://{host}:{self.overheard_port}"

    @property
    def digests_dir(self) -> Path:
        """Where `EMAIL_MODE=file` writes digests."""
        return self.overheard_data_dir / "digests"

    @property
    def is_default_ollama(self) -> bool:
        """Whether the model server is the default local Ollama."""
        return self.llm_base_url.rstrip("/") in {
            "http://127.0.0.1:11434",
            "http://localhost:11434",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load the settings once per process.

    Returns:
        The process-wide settings.
    """
    return Settings()
