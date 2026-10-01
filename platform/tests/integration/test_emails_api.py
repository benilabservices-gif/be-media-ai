"""Emails transactionnels : mot de passe oublié et alerte « nouveau prospect ».

La file est partagée entre les tests : vider la file peut traiter les tâches d'autres tests.
Les assertions filtrent donc les emails sur le destinataire ou l'entreprise du test.
"""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from digital360.core.email import EmailMessage
from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import PASSWORD, ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_diagnostics_api import BEGINNER_ANSWERS, Diagnostic

pytestmark = pytest.mark.integration

NEW_PASSWORD = "Attieke-Poisson-Braise-2026"
RESET_URL = "https://app.example.test/digital360/reset-password.html"


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(
        database_url=test_database_url,
        password_reset_url=RESET_URL,
        sales_alert_emails=["ventes@benilab.test", "direction@benilab.test"],
        admin_url="https://app.example.test/digital360/admin.html",
    )
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _emails_to(outbox: RecordingEmailSender, address: str) -> list[EmailMessage]:
    return [message for message in outbox.sent if address in message.to]


def _request_reset_link(
    api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender, email: str
) -> str:
    assert api.post("/auth/password/forgot", {"email": email.upper()}).status_code == 202
    run_pending_jobs(http_client)
    [message] = _emails_to(outbox, email)
    link = next(word for word in message.text.split() if word.startswith(RESET_URL))
    return link.split("#token=", 1)[1]


def _expire_reset_tokens(database_url: str, user_id: str) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE password_reset_tokens SET expires_at = now() - interval '1 minute' "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )
        await engine.dispose()

    asyncio.run(run())


# ── Mot de passe oublié ──


def test_should_email_reset_link_and_change_password(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    token = _request_reset_link(second_api, http_client, outbox, email)

    response = second_api.post("/auth/password/reset", {"token": token, "password": NEW_PASSWORD})

    assert response.status_code == 204
    assert second_api.post("/auth/login", {"email": email, "password": PASSWORD}).status_code == 401
    login = second_api.post("/auth/login", {"email": email, "password": NEW_PASSWORD})
    assert login.status_code == 200


def test_should_put_token_after_hash_in_reset_link(
    api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    api.post("/auth/password/forgot", {"email": email})
    run_pending_jobs(http_client)

    [message] = _emails_to(outbox, email)

    # Après `#` : le jeton n'est envoyé ni au serveur de pages ni dans le Referer
    assert f"{RESET_URL}#token=" in message.text
    assert f'href="{RESET_URL}#token=' in message.html


def test_should_answer_the_same_for_unknown_email_and_send_nothing(
    api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = f"inconnu-{uuid.uuid4().hex[:8]}@exemple.ci"

    response = api.post("/auth/password/forgot", {"email": email})
    run_pending_jobs(http_client)

    assert response.status_code == 202
    assert _emails_to(outbox, email) == []


def test_should_close_all_open_sessions_after_reset(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    assert api.get("/me").status_code == 200
    token = _request_reset_link(second_api, http_client, outbox, email)

    second_api.post("/auth/password/reset", {"token": token, "password": NEW_PASSWORD})

    assert api.get("/me").status_code == 401


def test_should_refuse_reusing_a_reset_link(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    token = _request_reset_link(second_api, http_client, outbox, email)
    second_api.post("/auth/password/reset", {"token": token, "password": NEW_PASSWORD})

    again = second_api.post(
        "/auth/password/reset", {"token": token, "password": "Autre-Mot-De-Passe-2026"}
    )

    assert again.status_code == 400
    assert again.json()["code"] == "INVALID_RESET_TOKEN"


def test_should_invalidate_older_links_once_one_is_used(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    first = _request_reset_link(second_api, http_client, outbox, email)
    outbox.sent.clear()
    second = _request_reset_link(second_api, http_client, outbox, email)

    second_api.post("/auth/password/reset", {"token": second, "password": NEW_PASSWORD})
    response = second_api.post("/auth/password/reset", {"token": first, "password": NEW_PASSWORD})

    assert response.status_code == 400


def test_should_refuse_expired_reset_link(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    body = api.register()
    token = _request_reset_link(second_api, http_client, outbox, body["user"]["email"])
    _expire_reset_tokens(test_database_url, body["user"]["id"])

    response = second_api.post("/auth/password/reset", {"token": token, "password": NEW_PASSWORD})

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RESET_TOKEN"


def test_should_refuse_unknown_reset_token(api: ApiClient) -> None:
    response = api.post("/auth/password/reset", {"token": "faux-jeton", "password": NEW_PASSWORD})

    assert response.status_code == 400


def test_should_keep_link_valid_when_new_password_is_too_weak(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    email = api.register()["user"]["email"]
    token = _request_reset_link(second_api, http_client, outbox, email)

    weak = second_api.post("/auth/password/reset", {"token": token, "password": "123"})
    retry = second_api.post("/auth/password/reset", {"token": token, "password": NEW_PASSWORD})

    assert weak.status_code == 400
    assert weak.json()["code"] == "WEAK_PASSWORD"
    assert retry.status_code == 204


def test_should_limit_reset_requests_per_email(api: ApiClient) -> None:
    email = f"cible-{uuid.uuid4().hex[:8]}@exemple.ci"

    statuses = [api.post("/auth/password/forgot", {"email": email}).status_code for _ in range(4)]

    assert statuses == [202, 202, 202, 429]


def test_should_require_csrf_to_request_reset(api: ApiClient) -> None:
    response = api.post_without_csrf("/auth/password/forgot", {"email": "x@exemple.ci"})

    assert response.status_code == 403


# ── Alerte à l'équipe commerciale ──


def test_should_alert_sales_team_when_diagnostic_is_completed(
    api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender, published_config: None
) -> None:
    company = f"Salon <b>Belle</b> {uuid.uuid4().hex[:6]}"
    diagnostic = Diagnostic(api)
    diagnostic.put_answers({**BEGINNER_ANSWERS, "company": company})
    assert diagnostic.complete().status_code == 200

    run_pending_jobs(http_client)

    [alert] = [message for message in outbox.sent if company in message.subject]
    assert alert.to == ["ventes@benilab.test", "direction@benilab.test"]
    assert "Digital Score :" in alert.text
    # Le nom vient du formulaire public : jamais interprété comme du HTML
    assert "<b>Belle</b>" not in alert.html
    assert "&lt;b&gt;Belle&lt;/b&gt;" in alert.html


def test_should_not_alert_when_no_recipient_is_configured(
    test_database_url: str, published_config: None
) -> None:
    outbox = RecordingEmailSender()
    app = create_app(make_settings(database_url=test_database_url), email_sender=outbox)
    with TestClient(app) as client:
        api = ApiClient(client)
        company = f"Sans alerte {uuid.uuid4().hex[:6]}"
        diagnostic = Diagnostic(api)
        diagnostic.put_answers({**BEGINNER_ANSWERS, "company": company})
        diagnostic.complete()

        run_pending_jobs(client)

    assert [message for message in outbox.sent if company in message.subject] == []
