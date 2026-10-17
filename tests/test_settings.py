from pathlib import Path

import pytest

from overheard.settings import Settings

ENV_NAMES = [
    "DATABASE_URL",
    "SERPAPI_API_KEY",
    "SERPAPI_DAILY_CAP",
    "SERPAPI_MONTHLY_CAP",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASS",
    "EMAIL_FROM",
    "EMAIL_MODE",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "CHAT_MODEL",
    "EMBED_MODEL",
    "TEMPORAL_ADDRESS",
    "TEMPORAL_NAMESPACE",
    "TEMPORAL_API_KEY",
    "TEMPORAL_UI_URL",
    "OVERHEARD_HOST",
    "OVERHEARD_PORT",
    "OVERHEARD_PASSWORD",
    "OVERHEARD_DB_SCHEMA",
    "OVERHEARD_DATA_DIR",
    "OVERHEARD_OFFLINE",
]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.database_url is None
    assert s.llm_base_url == "http://127.0.0.1:11434"
    assert s.chat_model == "gemma4:e4b"
    assert s.embed_model == "embeddinggemma:300m"
    assert s.email_mode == "off"
    assert s.email_from is None
    assert s.smtp_port == 587
    assert s.temporal_address == "127.0.0.1:7233"
    assert s.temporal_namespace == "default"
    assert s.temporal_ui_url == "http://127.0.0.1:8233"
    assert s.overheard_host == "127.0.0.1"
    assert s.overheard_port == 8000
    assert s.overheard_db_schema == "overheard"
    assert s.overheard_data_dir == Path(".data")
    assert s.serpapi_daily_cap is None
    assert s.serpapi_monthly_cap is None
    assert s.is_local_host
    assert s.is_default_ollama
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _ = s.dsn


def test_smtp_host_switches_email_mode_and_sender(clean_env, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    s = Settings(_env_file=None)
    assert s.email_mode == "smtp"
    assert s.email_from == "me@example.com"


def test_environment_beats_env_file(clean_env, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CHAT_MODEL=from-file\nEMBED_MODEL=file-embed\n")
    monkeypatch.setenv("CHAT_MODEL", "from-env")
    s = Settings(_env_file=env_file)
    assert s.chat_model == "from-env"
    assert s.embed_model == "file-embed"


def test_secrets_are_hidden_in_repr(clean_env, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "not-a-real-key-123")
    s = Settings(_env_file=None)
    assert "not-a-real-key-123" not in repr(s)
    assert "not-a-real-key-123" not in str(s.model_dump())


def test_schema_must_be_an_identifier(clean_env, monkeypatch):
    monkeypatch.setenv("OVERHEARD_DB_SCHEMA", "bad;drop")
    with pytest.raises(ValueError, match="identifier"):
        Settings(_env_file=None)
