"""
Central application settings, loaded once from environment variables / .env.
Everything else (storage root, DB path, Firebase project) reads from here
instead of touching os.environ directly, so there's one place to see the
full config surface and one place to change it for tests.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Firebase ---
    firebase_project_id: str
    # Path to a service account JSON key, OR leave unset to use Application
    # Default Credentials (e.g. when GOOGLE_APPLICATION_CREDENTIALS is set).
    firebase_credentials_path: str | None = None

    # --- Storage ---
    # Physical root all storage keys resolve under. Never expose anything
    # outside this directory (see storage/local_storage.py's path guard).
    storage_root: Path = Path("./storage")

    # --- Database ---
    database_path: Path = Path("./data/virtual_desktop.db")

    # --- CORS ---
    # Comma-separated origins in .env, e.g. "https://app.example.com,http://localhost:5000"
    cors_allow_origins: str = "http://locahost:\\d+"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"


@lru_cache
def get_settings() -> Settings:
    """
    Cached so Settings() (which reads the environment / .env) only runs
    once per process. FastAPI routes depend on this via Depends(get_settings)
    rather than importing a module-level instance, which makes it trivial
    to override in tests.
    """
    return Settings()