"""Registre applicatif des tâches : chaque module y branche ses handlers et leurs dépendances.

Construit une fois par processus (API ou worker) : l'API s'en sert pour publier les
événements (qui enfilent une tâche par abonné), le worker pour exécuter les tâches.
"""

from digital360.core.config import Settings
from digital360.core.email import EmailSender
from digital360.core.jobs import JobRegistry
from digital360.modules.billing.application import service as billing
from digital360.modules.diagnostics.application import notifications as diagnostic_notifications
from digital360.modules.identity.application import password_reset


def build_registry(settings: Settings, email_sender: EmailSender) -> JobRegistry:
    registry = JobRegistry()
    password_reset.register_jobs(registry, email_sender)
    diagnostic_notifications.register_jobs(
        registry,
        email_sender,
        recipients=settings.sales_alert_emails,
        admin_url=settings.admin_url,
    )
    billing.register_jobs(
        registry,
        email_sender,
        recipients=settings.sales_alert_emails,
        admin_url=settings.admin_url,
    )
    return registry
