from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from digital360 import __version__
from digital360.core import health
from digital360.core.config import Settings, get_settings
from digital360.core.db import create_engine, create_session_factory
from digital360.core.errors import register_error_handlers
from digital360.core.logging import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Le moteur ne se connecte qu'au premier usage : l'API démarre même si la base
        # est momentanément indisponible, et /health/ready le signale
        app.state.engine = create_engine(str(settings.database_url))
        app.state.session_factory = create_session_factory(app.state.engine)
        yield
        await app.state.engine.dispose()

    app = FastAPI(
        title="BENILAB Digital360 API",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_production else f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Diagnostic-Token"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Ajouté en dernier = exécuté en premier : le request_id existe pour toute la chaîne
    app.add_middleware(RequestContextMiddleware)

    api_router = APIRouter(prefix=API_PREFIX)
    api_router.include_router(health.router)
    app.include_router(api_router)

    return app
