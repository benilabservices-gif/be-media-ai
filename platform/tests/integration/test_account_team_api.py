"""Mon compte (mot de passe), équipe (invitations), plusieurs projets par compte sans mélange."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import PASSWORD, ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic
from tests.integration.test_diagnostics_api import BEGINNER_ANSWERS, Diagnostic

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

NEW_PASSWORD = "Nouveau-Maquis-2027!"


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, app_url="https://digital360.test/")
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _login(api: ApiClient, email: str, password: str) -> Any:
    return api.post("/auth/login", {"email": email, "password": password})


# ── Mot de passe ──


def test_should_change_password_and_disconnect_other_devices(
    api: ApiClient, second_api: ApiClient
) -> None:
    email = api.register()["user"]["email"]
    assert _login(second_api, email, PASSWORD).status_code == 200

    changed = api.post("/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})

    assert changed.status_code == 204
    assert api.get("/me").status_code == 200  # cet appareil reste connecté
    assert second_api.get("/me").status_code == 401  # l'autre appareil est déconnecté
    assert _login(second_api, email, PASSWORD).status_code == 401
    assert _login(second_api, email, NEW_PASSWORD).status_code == 200


def test_should_refuse_password_change_with_wrong_current_password(api: ApiClient) -> None:
    api.register()

    response = api.post(
        "/me/password", {"current_password": "pas-le-bon", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURRENT_PASSWORD"


def test_should_refuse_weak_new_password(api: ApiClient) -> None:
    api.register()

    response = api.post("/me/password", {"current_password": PASSWORD, "new_password": "123"})

    assert response.status_code == 400
    assert response.json()["code"] == "WEAK_PASSWORD"


def test_should_update_name_from_account_settings(api: ApiClient) -> None:
    api.register()

    response = api.patch("/me", {"full_name": "Awa Koné Diallo", "phone": "+2250700000000"})

    assert response.status_code == 200
    assert response.json()["full_name"] == "Awa Koné Diallo"


# ── Équipe ──


def _invitation_token(outbox: RecordingEmailSender, email: str) -> str:
    message = next(m for m in reversed(outbox.sent) if m.to == [email])
    match = re.search(r"#invitation=([\w-]+)", message.text)
    assert match, message.text
    return match.group(1)


def _owner_with_org(api: ApiClient) -> dict[str, Any]:
    api.register()
    return api.create_organization("Maquis Le Délice")


def test_should_invite_member_by_email_and_let_them_join_after_signup(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _owner_with_org(api)
    invited = "collegue-" + org["id"][-6:] + "@exemple.ci"

    created = api.post(f"/orgs/{org['id']}/invitations", {"email": invited.upper()})
    run_pending_jobs(http_client)
    token = _invitation_token(outbox, invited)
    second_api.register(email=invited)
    accepted = second_api.post("/invitations/accept", {"token": token})

    assert created.status_code == 201
    assert created.json()["email"] == invited
    assert accepted.status_code == 200
    assert accepted.json()["organization_name"] == "Maquis Le Délice"
    memberships = second_api.get("/me").json()["memberships"]
    assert [(m["organization_id"], m["role"]) for m in memberships] == [
        (org["id"], "CLIENT_MEMBER")
    ]
    members = api.get(f"/orgs/{org['id']}/members").json()["data"]
    assert invited in [m["email"] for m in members]
    assert api.get(f"/orgs/{org['id']}/invitations").json()["data"] == []
    # Lien à usage unique
    assert second_api.post("/invitations/accept", {"token": token}).status_code == 400


def test_should_refuse_invitation_opened_with_another_account(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _owner_with_org(api)
    invited = "invite-" + org["id"][-6:] + "@exemple.ci"
    api.post(f"/orgs/{org['id']}/invitations", {"email": invited})
    run_pending_jobs(http_client)
    second_api.register()

    response = second_api.post("/invitations/accept", {"token": _invitation_token(outbox, invited)})

    assert response.status_code == 403
    assert response.json()["code"] == "INVITATION_EMAIL_MISMATCH"
    assert second_api.get("/me").json()["memberships"] == []


def test_should_reserve_team_management_to_owners(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _owner_with_org(api)
    member_email = "membre-" + org["id"][-6:] + "@exemple.ci"
    api.post(f"/orgs/{org['id']}/invitations", {"email": member_email})
    run_pending_jobs(http_client)
    second_api.register(email=member_email)
    second_api.post("/invitations/accept", {"token": _invitation_token(outbox, member_email)})

    attempt = second_api.post(f"/orgs/{org['id']}/invitations", {"email": "autre@exemple.ci"})

    assert attempt.status_code == 403


def test_should_keep_at_least_one_owner_and_revoke_removed_member(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _owner_with_org(api)
    owner_id = api.get("/me").json()["user"]["id"]
    member_email = "retire-" + org["id"][-6:] + "@exemple.ci"
    api.post(f"/orgs/{org['id']}/invitations", {"email": member_email})
    run_pending_jobs(http_client)
    member_id = second_api.register(email=member_email)["user"]["id"]
    second_api.post("/invitations/accept", {"token": _invitation_token(outbox, member_email)})

    last_owner = api.request("DELETE", f"/orgs/{org['id']}/members/{owner_id}")
    removed = api.request("DELETE", f"/orgs/{org['id']}/members/{member_id}")

    assert last_owner.status_code == 409
    assert last_owner.json()["code"] == "LAST_OWNER"
    assert removed.status_code == 204
    assert second_api.get(f"/orgs/{org['id']}").status_code in (403, 404)


def test_should_cancel_pending_invitation(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _owner_with_org(api)
    invited = "annule-" + org["id"][-6:] + "@exemple.ci"
    invitation = api.post(f"/orgs/{org['id']}/invitations", {"email": invited}).json()
    run_pending_jobs(http_client)
    token = _invitation_token(outbox, invited)

    cancelled = api.request("DELETE", f"/orgs/{org['id']}/invitations/{invitation['id']}")
    second_api.register(email=invited)

    assert cancelled.status_code == 204
    assert second_api.post("/invitations/accept", {"token": token}).status_code == 400


def test_should_refuse_inviting_an_existing_member(api: ApiClient) -> None:
    org = _owner_with_org(api)
    own_email = api.get("/me").json()["user"]["email"]

    response = api.post(f"/orgs/{org['id']}/invitations", {"email": own_email})

    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_MEMBER"


# ── Plusieurs projets, chacun avec son diagnostic ──


def _completed_diagnostic(api: ApiClient, company: str) -> Diagnostic:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers({**BEGINNER_ANSWERS, "company": company})
    assert diagnostic.complete().status_code == 200
    return diagnostic


def test_should_keep_each_project_with_its_own_diagnostic(api: ApiClient) -> None:
    first = _client_with_diagnostic(api)
    second_diagnostic = _completed_diagnostic(api, "Boutique Mode Akwaba")

    second = api.post(
        "/orgs",
        {"diagnostic_id": second_diagnostic.id},
        headers={"X-Diagnostic-Token": second_diagnostic.token},
    )

    assert second.status_code == 201
    assert second.json()["commercial_name"] == "Boutique Mode Akwaba"
    projects = {m["organization_id"] for m in api.get("/me").json()["memberships"]}
    assert projects == {first["id"], second.json()["id"]}
    first_history = api.get(f"/orgs/{first['id']}/diagnostics").json()["data"]
    second_history = api.get(f"/orgs/{second.json()['id']}/diagnostics").json()["data"]
    assert len(first_history) == 1 and len(second_history) == 1
    assert first_history[0]["id"] != second_history[0]["id"]
    assert second_history[0]["id"] == second_diagnostic.id


def test_should_add_new_diagnostic_to_the_chosen_project_only(api: ApiClient) -> None:
    first = _client_with_diagnostic(api)
    second = api.create_organization("Atelier Couture Bintou")
    redo = _completed_diagnostic(api, "Atelier Couture Bintou")

    claimed = api.post(
        f"/orgs/{second['id']}/diagnostics/claim",
        {"diagnostic_id": redo.id},
        headers={"X-Diagnostic-Token": redo.token},
    )

    assert claimed.status_code in (200, 201), claimed.json()
    assert len(api.get(f"/orgs/{first['id']}/diagnostics").json()["data"]) == 1
    assert [d["id"] for d in api.get(f"/orgs/{second['id']}/diagnostics").json()["data"]] == [
        redo.id
    ]
