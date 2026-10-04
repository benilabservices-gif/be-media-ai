import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from digital360 import __version__
from digital360.api import admin, me
from digital360.core import health
from digital360.core.config import Settings, get_settings
from digital360.core.db import create_engine, create_session_factory
from digital360.core.email import EmailSender, build_email_sender
from digital360.core.errors import register_error_handlers
from digital360.core.jobs import Worker
from digital360.core.logging import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from digital360.core.rate_limit import RateLimiter
from digital360.jobs import build_registry
from digital360.modules.billing.api import routes as billing_routes
from digital360.modules.billing.application.online_payments import OnlinePaymentService
from digital360.modules.billing.application.renewals import (
    RenewalService,
    schedule_daily_sweep,
)
from digital360.modules.billing.application.service import PurchaseRequestService
from digital360.modules.billing.infrastructure.cartflox import (
    PaymentGateway,
    build_payment_gateway,
)
from digital360.modules.catalog.api import routes as catalog_routes
from digital360.modules.catalog.application.service import CatalogService
from digital360.modules.diagnostics.api import routes as diagnostic_routes
from digital360.modules.diagnostics.application.service import DiagnosticService
from digital360.modules.identity.api import routes as identity_routes
from digital360.modules.identity.application.auth_service import AuthService
from digital360.modules.identity.application.password_reset import PasswordResetService
from digital360.modules.identity.application.staff_service import StaffService
from digital360.modules.identity.infrastructure.password_hasher import Argon2PasswordHasher
from digital360.modules.organizations.api import routes as organization_routes
from digital360.modules.organizations.application.service import OrganizationService
from digital360.modules.passport.api import routes as passport_routes
from digital360.modules.passport.application.service import PassportService
from digital360.modules.projects.api import routes as project_routes
from digital360.modules.projects.application.service import ProjectService
from digital360.modules.referrals.api import routes as referral_routes
from digital360.modules.referrals.application.service import (
    CloserService,
    schedule_monthly_statements,
)

API_PREFIX = "/api/v1"

logger = logging.getLogger("digital360.app")


def create_app(
    settings: Settings | None = None,
    *,
    email_sender: EmailSender | None = None,
    payment_gateway: PaymentGateway | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Le moteur ne se connecte qu'au premier usage : l'API démarre même si la base
        # est momentanément indisponible, et /health/ready le signale
        app.state.engine = create_engine(str(settings.database_url))
        session_factory = create_session_factory(app.state.engine)
        app.state.session_factory = session_factory
        # Composition : chaque service reçoit ses dépendances ici, et nulle part ailleurs
        hasher = Argon2PasswordHasher()
        gateway = payment_gateway or build_payment_gateway(settings)
        registry = build_registry(settings, email_sender or build_email_sender(settings), gateway)
        app.state.job_registry = registry
        app.state.auth_service = AuthService(
            session_factory,
            hasher,
            idle_timeout=timedelta(days=settings.session_idle_days),
            absolute_timeout=timedelta(days=settings.session_absolute_days),
        )
        app.state.password_reset_service = PasswordResetService(
            session_factory, hasher, reset_url=settings.password_reset_url
        )
        app.state.organization_service = OrganizationService(session_factory)
        app.state.diagnostic_service = DiagnosticService(session_factory, registry)
        app.state.passport_service = PassportService(session_factory)
        app.state.catalog_service = CatalogService(session_factory)
        app.state.staff_service = StaffService(session_factory)
        app.state.purchase_request_service = PurchaseRequestService(session_factory, registry)
        app.state.project_service = ProjectService(session_factory, registry)
        app.state.renewal_service = RenewalService(session_factory, registry)
        app.state.online_payment_service = OnlinePaymentService(
            session_factory, registry, gateway, app_url=settings.app_url
        )
        app.state.closer_service = CloserService(session_factory, app_url=settings.app_url)
        app.state.rate_limiter = RateLimiter()

        stop_worker = asyncio.Event()
        worker_task = None
        if settings.run_worker_in_api:
            worker = Worker(session_factory, registry, worker_id=f"api-{uuid.uuid4().hex[:8]}")
            worker_task = asyncio.create_task(worker.run_forever(stop_worker))
            # Contrôle quotidien des échéances ; il se replanifie ensuite de lui-même.
            # Base indisponible au démarrage : l'API démarre quand même, sans rappels ce jour-là.
            try:
                await schedule_daily_sweep(session_factory)
                await schedule_monthly_statements(session_factory)
            except Exception:
                logger.exception("contrôle quotidien des échéances non planifié")
        # Configuration réellement chargée, sans aucun secret : premier réflexe en cas de doute
        logger.info(
            "configuration des emails",
            extra={
                "email_provider": settings.email_provider.value,
                "email_from": settings.email_from,
                "sales_alert_recipients": len(settings.sales_alert_emails),
                "worker_in_api": settings.run_worker_in_api,
                "online_payment": gateway is not None,
                "app_url": settings.app_url,
            },
        )
        yield
        stop_worker.set()
        if worker_task is not None:
            await worker_task
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
    api_router.include_router(identity_routes.router)
    api_router.include_router(me.router)
    api_router.include_router(organization_routes.router)
    api_router.include_router(organization_routes.admin_router)
    api_router.include_router(diagnostic_routes.public_router)
    api_router.include_router(diagnostic_routes.router)
    api_router.include_router(diagnostic_routes.admin_router)
    api_router.include_router(passport_routes.router)
    api_router.include_router(catalog_routes.public_router)
    api_router.include_router(catalog_routes.router)
    api_router.include_router(catalog_routes.admin_router)
    api_router.include_router(billing_routes.router)
    api_router.include_router(billing_routes.admin_router)
    api_router.include_router(project_routes.router)
    api_router.include_router(project_routes.admin_router)
    api_router.include_router(referral_routes.router)
    api_router.include_router(referral_routes.admin_router)
    api_router.include_router(admin.router)
    app.include_router(api_router)

    return app
