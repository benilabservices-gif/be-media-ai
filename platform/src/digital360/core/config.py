from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Configuration lue depuis l'environnement (et `.env` en local)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.LOCAL
    log_level: str = "INFO"
    database_url: PostgresDsn
    # Relatif au dossier de lancement (platform/ en local, /app dans l'image Docker)
    alembic_ini_path: Path = Path("alembic.ini")
    # NoDecode : la variable est une liste séparée par des virgules, pas du JSON
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Cookies de session et CSRF. Le domaine parent (ex. ".benilab360.com") permet au
    # frontend (app.) et à l'API (api.) de partager les cookies ; vide = hôte courant.
    session_cookie_domain: str | None = None
    session_idle_days: int = Field(default=7, ge=1)
    session_absolute_days: int = Field(default=30, ge=1)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: PostgresDsn) -> PostgresDsn:
        # L'URL standard (celle de Neon ou Render) est convertie pour asyncpg par
        # core.db.engine_options ; un autre pilote explicite échouerait au premier appel
        if value.scheme not in ("postgresql", "postgres", "postgresql+asyncpg"):
            raise ValueError(
                "DATABASE_URL doit commencer par postgresql:// ou postgresql+asyncpg://"
            )
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def secure_cookies(self) -> bool:
        # HTTPS obligatoire hors poste de développement et tests (qui tournent en HTTP)
        return self.app_env in {AppEnv.STAGING, AppEnv.PRODUCTION}


@lru_cache
def get_settings() -> Settings:
    # database_url n'a pas de valeur par défaut : il vient de l'environnement ou de .env
    return Settings()
