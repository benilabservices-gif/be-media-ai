"""Parcours d'authentification tel que le frontend l'exécute."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.api_client import PASSWORD, ApiClient

pytestmark = pytest.mark.integration


def _session_cookie_header(response: object) -> str:
    headers = response.headers.get_list("set-cookie")  # type: ignore[attr-defined]
    return next(header for header in headers if header.startswith("d360_session="))


def _expire_sessions(database_url: str, user_id: str) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE sessions SET expires_at = :past WHERE user_id = :user_id"),
                {"past": datetime.now(UTC) - timedelta(minutes=1), "user_id": user_id},
            )
        await engine.dispose()

    asyncio.run(run())


def test_should_set_readable_csrf_cookie(api: ApiClient) -> None:
    response = api.get("/auth/csrf")

    assert response.status_code == 200
    assert response.json()["csrf_token"] == api.cookies.get("csrf_token")
    assert "HttpOnly" not in response.headers["set-cookie"]


def test_should_register_open_session_and_return_me(api: ApiClient) -> None:
    body = api.register(email=f"Awa.Kone.{uuid.uuid4().hex[:6]}@Exemple.CI")

    assert body["user"]["email"].startswith("awa.kone.")
    assert body["user"]["email"].endswith("@exemple.ci")
    me = api.get("/me").json()
    assert me["user"]["full_name"] == "Awa Koné"
    assert me["memberships"] == []
    assert me["staff_roles"] == []


def test_should_protect_session_cookie_from_javascript(api: ApiClient) -> None:
    email = f"u-{uuid.uuid4().hex[:8]}@exemple.ci"
    response = api.post(
        "/auth/register", {"email": email, "password": PASSWORD, "full_name": "Awa Koné"}
    )

    header = _session_cookie_header(response)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header


def test_should_reject_duplicate_email_case_insensitively(api: ApiClient) -> None:
    email = f"dup-{uuid.uuid4().hex[:8]}@exemple.ci"
    api.register(email=email)

    response = api.post(
        "/auth/register", {"email": email.upper(), "password": PASSWORD, "full_name": "Autre"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_EXISTS"


def test_should_list_every_password_problem(api: ApiClient) -> None:
    response = api.post(
        "/auth/register",
        {"email": "weak@exemple.ci", "password": "aaaa", "full_name": "Awa Koné"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "WEAK_PASSWORD"
    assert {error["reason"] for error in body["errors"]} == {"too_short", "single_character"}


def test_should_refuse_modifying_request_without_csrf(api: ApiClient) -> None:
    response = api.post_without_csrf("/auth/login", {"email": "a@exemple.ci", "password": "x"})

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_should_login_with_correct_password(api: ApiClient, second_api: ApiClient) -> None:
    email = f"login-{uuid.uuid4().hex[:8]}@exemple.ci"
    api.register(email=email)

    response = second_api.post("/auth/login", {"email": email, "password": PASSWORD})

    assert response.status_code == 200
    assert second_api.get("/me").json()["user"]["email"] == email


def test_should_give_same_error_for_unknown_email_and_wrong_password(
    api: ApiClient, second_api: ApiClient
) -> None:
    email = f"enum-{uuid.uuid4().hex[:8]}@exemple.ci"
    api.register(email=email)

    wrong_password = second_api.post("/auth/login", {"email": email, "password": "mauvais-mdp"})
    unknown = second_api.post(
        "/auth/login", {"email": f"inconnu-{uuid.uuid4().hex[:6]}@exemple.ci", "password": "x"}
    )

    assert wrong_password.status_code == unknown.status_code == 401
    assert wrong_password.json()["detail"] == unknown.json()["detail"]


def test_should_rate_limit_repeated_login_attempts_per_email(second_api: ApiClient) -> None:
    email = f"brute-{uuid.uuid4().hex[:8]}@exemple.ci"
    responses = [
        second_api.post("/auth/login", {"email": email, "password": "essai"}) for _ in range(6)
    ]

    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert int(responses[5].headers["Retry-After"]) > 0


def test_should_return_401_on_me_without_session(api: ApiClient) -> None:
    response = api.get("/me")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_should_revoke_session_on_logout(api: ApiClient) -> None:
    api.register()
    token = api.cookies["d360_session"]

    assert api.post("/auth/logout").status_code == 204

    # Même en rejouant l'ancien jeton, la session est révoquée côté serveur
    api.set_cookie("d360_session", token)
    response = api.get("/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"


def test_should_reject_expired_session(api: ApiClient, test_database_url: str) -> None:
    body = api.register()
    _expire_sessions(test_database_url, body["user"]["id"])

    response = api.get("/me")

    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"


def test_should_update_profile(api: ApiClient) -> None:
    api.register()

    response = api.patch("/me", {"full_name": "Awa Koné Traoré", "phone": "+2250500000000"})

    assert response.status_code == 200
    assert response.json()["full_name"] == "Awa Koné Traoré"
    assert api.patch("/me", {"phone": ""}).json()["phone"] is None
