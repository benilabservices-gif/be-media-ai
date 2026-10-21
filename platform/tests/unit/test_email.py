"""Envoi par Brevo : requête envoyée, identifiant journalisé, refus transformé en erreur."""

import io
import json
import logging
import urllib.error
import urllib.request
from typing import Any

import pytest

from digital360.core.email import BrevoEmailSender, EmailDeliveryError, EmailMessage

pytestmark = pytest.mark.anyio

MESSAGE = EmailMessage(
    to=["awa@exemple.ci"], subject="Bienvenue", text="lien secret", html="<p>lien secret</p>"
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _sender() -> BrevoEmailSender:
    return BrevoEmailSender("xkeysib-test", sender_email="no-reply@exemple.ci", sender_name="D360")


async def test_should_send_message_to_brevo_and_log_its_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        sent["headers"] = dict(request.header_items())
        sent["body"] = json.loads(request.data)  # type: ignore[arg-type]
        return FakeResponse(b'{"messageId": "<abc@smtp-relay.brevo.com>"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    caplog.set_level(logging.INFO, logger="digital360.email")

    await _sender().send(MESSAGE)

    assert sent["headers"]["Api-key"] == "xkeysib-test"
    assert sent["body"]["sender"] == {"email": "no-reply@exemple.ci", "name": "D360"}
    assert sent["body"]["to"] == [{"email": "awa@exemple.ci"}]
    [record] = [r for r in caplog.records if r.getMessage() == "email envoyé (brevo)"]
    assert record.brevo_message_id == "<abc@smtp-relay.brevo.com>"  # type: ignore[attr-defined]
    # Le corps (qui peut contenir un lien de réinitialisation) n'est jamais journalisé
    assert "lien secret" not in caplog.text


async def test_should_raise_delivery_error_when_brevo_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", None, io.BytesIO(b'{"code":"unauthorized"}')  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(EmailDeliveryError, match="401"):
        await _sender().send(MESSAGE)
