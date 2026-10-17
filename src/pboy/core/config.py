"""Core configuration for the Pageboy daemon and CLI.

Loads settings from environment variables (PB_*) and optional .env file.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pageboy daemon & client settings."""

    model_config = SettingsConfigDict(
        env_prefix="PB_",
        env_file=".env",
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 8080

    # Database
    database_url: str | None = None

    # Secret encryption key (AES-256)
    secret_key: str = ""

    # Logging
    log_level: str = "INFO"

    # When true, Telegram channel downloads inbound files before emit (size-capped).
    telegram_download_files: bool = True

    # Paths
    config_dir: Path = Path.home() / ".config" / "pboy"
    data_dir: Path = Path.home() / ".local" / "share" / "pboy"

    @property
    def db_path(self) -> Path:
        """Resolve path to SQLite database file."""
        if self.database_url:
            cleaned = self.database_url
            for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
            path = Path(os.path.expanduser(cleaned))
            return path
        return self.data_dir / "pboy.db"

    @property
    def secret_key_path(self) -> Path:
        """Resolve path to secret key file."""
        return self.config_dir / "secret.key"

    def ensure_dirs(self) -> None:
        """Create config and data directories if they do not exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "events" / "raw").mkdir(parents=True, exist_ok=True)


# Singleton
_settings: Settings | None = None


def get_settings() -> Settings:
    """Retrieve or initialize the singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
