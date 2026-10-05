"""Registre applicatif des tâches : chaque module y branche ses handlers et leurs dépendances.

Construit une fois par processus (API ou worker) : l'API s'en sert pour publier les
événements (qui enfilent une tâche par abonné), le worker pour exécuter les tâches.
"""

from digital360.core.config import Settings
from digital360.core.email import EmailSender
from digital360.core.jobs import JobRegistry
from digital360.modules.billing.application import online_payments, renewals
from digital360.modules.billing.application import service as billing
from digital360.modules.billing.infrastructure.cartflox import PaymentGateway
from digital360.modules.diagnostics.application import notifications as diagnostic_notifications
from digital360.modules.identity.application import password_reset
from digital360.modules.organizations.application import team
from digital360.modules.projects.application import notifications as project_notifications
from digital360.modules.referrals.application import service as referrals


def build_registry(
    settings: Settings, email_sender: EmailSender, payment_gateway: PaymentGateway | None = None
) -> JobRegistry:
    registry = JobRegistry()
    password_reset.register_jobs(registry, email_sender)
    team.register_jobs(registry, email_sender, app_url=settings.app_url)
    diagnostic_notifications.register_jobs(
        registry,
        email_sender,
        recipients=settings.sales_alert_emails,
        admin_url=settings.admin_url,
        app_url=settings.app_url,
    )
    billing.register_jobs(
        registry,
        email_sender,
        recipients=settings.sales_alert_emails,
        admin_url=settings.admin_url,
    )
    project_notifications.register_jobs(
        registry,
        email_sender,
        team_recipients=settings.sales_alert_emails,
        app_url=settings.app_url,
        admin_url=settings.admin_url,
    )
    renewals.register_jobs(
        registry,
        email_sender,
        team=settings.sales_alert_emails,
        app_url=settings.app_url,
        admin_url=settings.admin_url,
    )
    online_payments.register_jobs(registry, payment_gateway)
    referrals.register_jobs(registry, email_sender, app_url=settings.app_url)
    return registry
