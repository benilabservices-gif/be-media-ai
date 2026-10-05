from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class EmailProvider(StrEnum):
    CONSOLE = "console"
    BREVO = "brevo"


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

    # Emails transactionnels : "console" écrit dans les journaux, "brevo" envoie réellement
    email_provider: EmailProvider = EmailProvider.CONSOLE
    brevo_api_key: SecretStr | None = None
    email_from: str = "no-reply@benilab360.com"
    email_from_name: str = "BENILAB Digital360"
    # Pages du frontend vers lesquelles pointent les liens des e-mails, du parrainage Closer et
    # du retour de paiement. Par défaut, la plateforme en ligne : un oubli de variable sur
    # l'hébergeur ne doit jamais envoyer un client vers « localhost » (en local, surcharger
    # dans .env, par exemple APP_URL=http://localhost:5500/digital360/)
    password_reset_url: str = "https://digital360.bemedia-ai.online/reset-password.html"  # noqa: S105
    admin_url: str = "https://digital360.bemedia-ai.online/admin.html"
    # Page d'accueil de Digital360 (bouton « Créer mon espace » de l'e-mail au prospect)
    app_url: str = "https://digital360.bemedia-ai.online/"
    # Destinataires des alertes « nouveau prospect » ; vide = pas d'alerte
    sales_alert_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Hébergement sans worker séparé (Render gratuit) : l'API traite elle-même la file
    run_worker_in_api: bool = False
    # Paiement en ligne Cartflox (clé secrète af_live_sec_… de l'espace SchoolConnect) ;
    # vide = paiement en ligne désactivé, seul le paiement manuel est proposé
    cartflox_secret_key: SecretStr | None = None
    # Adresse que le client ajoute comme gestionnaire de sa fiche Google Business
    google_manager_email: str = "clientele@bemedia-ai.online"

    @model_validator(mode="after")
    def _require_brevo_key(self) -> "Settings":
        if self.email_provider is EmailProvider.BREVO and self.brevo_api_key is None:
            raise ValueError("EMAIL_PROVIDER=brevo exige BREVO_API_KEY")
        return self

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

    @field_validator("cors_allowed_origins", "sales_alert_emails", mode="before")
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
