"""Envoi d'emails transactionnels (port `EmailProvider` de l'ARCHITECTURE.md).

L'implémentation est choisie au démarrage selon `EMAIL_PROVIDER` et injectée dans les
handlers de tâches. Changer de fournisseur (Resend, Postmark…) revient à ajouter une classe ici.
"""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from digital360.core.config import EmailProvider, Settings

logger = logging.getLogger("digital360.email")

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


@dataclass(frozen=True)
class EmailMessage:
    to: list[str]
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class EmailDeliveryError(Exception):
    """Refus du fournisseur : la tâche échoue et sera retentée par le worker."""


class ConsoleEmailSender:
    """Développement et tests : l'email est écrit dans les journaux, rien n'est envoyé."""

    def __init__(self, *, include_body: bool) -> None:
        # Le corps peut contenir un lien de réinitialisation : jamais dans les journaux de prod
        self._include_body = include_body

    async def send(self, message: EmailMessage) -> None:
        extra: dict[str, object] = {"to": message.to, "subject": message.subject}
        if self._include_body:
            extra["body"] = message.text
        logger.info("email (console)", extra=extra)


class BrevoEmailSender:
    def __init__(
        self, api_key: str, *, sender_email: str, sender_name: str, timeout: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender_email, "name": sender_name}
        self._timeout = timeout

    async def send(self, message: EmailMessage) -> None:
        body = {
            "sender": self._sender,
            "to": [{"email": address} for address in message.to],
            "subject": message.subject,
            "textContent": message.text,
            "htmlContent": message.html,
        }
        request = urllib.request.Request(
            BREVO_SEND_URL,
            data=json.dumps(body).encode(),
            headers={
                "api-key": self._api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        # urllib est bloquant : exécuté hors de la boucle d'événements
        message_id = await asyncio.to_thread(self._post, request)
        # Le corps n'est jamais journalisé : il peut contenir un lien de réinitialisation
        logger.info(
            "email envoyé (brevo)",
            extra={"to": message.to, "subject": message.subject, "brevo_message_id": message_id},
        )

    def _post(self, request: urllib.request.Request) -> str | None:
        try:
            with urllib.request.urlopen(  # noqa: S310 (URL fixe)
                request, timeout=self._timeout
            ) as response:
                answer = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise EmailDeliveryError(f"Brevo a refusé l'envoi ({exc.code}) : {detail}") from exc
        message_id: str | None = answer.get("messageId")
        return message_id


def build_email_sender(settings: Settings) -> EmailSender:
    # La clé est garantie par la validation de Settings quand le fournisseur est Brevo
    if settings.email_provider is EmailProvider.BREVO and settings.brevo_api_key is not None:
        return BrevoEmailSender(
            settings.brevo_api_key.get_secret_value(),
            sender_email=settings.email_from,
            sender_name=settings.email_from_name,
        )
    return ConsoleEmailSender(include_body=not settings.is_production)
