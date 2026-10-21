"""Projet Digital Start : de la vente gagnée à la mise en ligne, dans le cadre de l'offre."""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.main import create_app
from digital360.modules.projects.domain.offer import add_business_days
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

# Encaissement manuel : obligatoire pour passer une demande à « Gagnée »
PAYMENT = {"method": "ORANGE_MONEY", "amount": 109900, "reference": "OM-TEST-0001"}
WON_WITH_PAYMENT = {"status": "WON", "payment": PAYMENT}

TEAM = "production@benilab.test"
APP_URL = "https://app.example.test/"
COMPLETE_BRIEF: dict[str, Any] = {
    "business_name": "Salon Belle Afrique",
    "activity": "Coiffure, tresses et soins pour femmes à Cocody.",
    "services": [{"name": "Tresses", "description": "Toutes longueurs"}, {"name": "Soins"}],
    "phone": "+2250700000000",
    "template": "CHALEUREUX",
    "palette": "SAVANE",
    "has_logo": True,
    "desired_domain": "salonbelleafrique.ci",
}


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(
        database_url=test_database_url, sales_alert_emails=[TEAM], app_url=APP_URL
    )
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _won_sale(
    client: ApiClient,
    staff: ApiClient,
    http_client: TestClient,
    database_url: str,
    product_code: str = "DIGITAL_START",
) -> dict[str, Any]:
    """Un client fait son diagnostic, demande l'offre ; l'équipe gagne la vente."""
    org = _client_with_diagnostic(client)
    request_id = client.post(
        f"/orgs/{org['id']}/purchase-requests",
        {"product_code": product_code, "channel": "EMAIL"},
    ).json()["id"]
    _staff_member(staff, database_url, "ADMIN")
    assert (
        staff.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT).status_code == 200
    )
    run_pending_jobs(http_client)
    return org


def _project(client: ApiClient, org_id: str) -> Any:
    return client.get(f"/orgs/{org_id}/website-project")


def _submit(client: ApiClient, org_id: str) -> Any:
    client.request("PUT", f"/orgs/{org_id}/website-project/brief", COMPLETE_BRIEF)
    return client.post(f"/orgs/{org_id}/website-project/submit", {"accept_scope": True})


def _to_review(staff: ApiClient, project_id: str) -> Any:
    return staff.post(
        f"/admin/website-projects/{project_id}/transition",
        {"target": "CLIENT_REVIEW", "preview_url": "https://preview.example.test/salon"},
    )


# ── Création à la vente gagnée ──


def test_should_create_prefilled_project_and_email_client_when_start_is_won(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    run_pending_jobs(http_client)
    outbox.sent.clear()
    org = _won_sale(api, second_api, http_client, test_database_url)

    body = _project(api, org["id"]).json()

    assert body["status"] == "BRIEF_PENDING"
    assert body["brief"]["business_name"] == org["commercial_name"]
    assert body["brief"]["whatsapp"] == "+2250500000000"
    assert "template" in body["missing_fields"]
    assert body["available_actions"][0] == "EDIT_BRIEF"
    assert body["offer"]["pitch"].startswith("Conception de votre site offerte")
    assert len(body["offer"]["templates"]) == 3
    assert len(body["offer"]["palettes"]) == 4
    owner_email = api.get("/me").json()["user"]["email"]
    [welcome] = [m for m in outbox.sent if m.subject.startswith("Votre offre Start est activée")]
    assert welcome.to == [owner_email]
    assert f"{APP_URL}dashboard.html" in welcome.text


def test_should_not_create_website_project_for_a_subscription(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url, "DIGITAL_ESSENTIAL")

    assert _project(api, org["id"]).status_code == 404


def test_should_hide_project_from_other_clients(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    test_database_url: str,
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    intruder = ApiClient(http_client)
    intruder.register()

    assert _project(intruder, org["id"]).status_code == 404


# ── Le brief reste dans le cadre de l'offre ──


@pytest.mark.parametrize(
    "out_of_scope",
    [
        {"services": [{"name": f"Service {n}"} for n in range(7)]},  # 6 au maximum
        {"template": "SUR_MESURE"},  # modèle inexistant
        {"palette": "ROSE_FLUO"},
        {"pages": ["Accueil", "Boutique"]},  # aucun champ hors formulaire
        {"desired_domain": "https://www.trop-long-et-faux"},
    ],
)
def test_should_refuse_brief_outside_offer_scope(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    test_database_url: str,
    out_of_scope: dict[str, Any],
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)

    response = api.request("PUT", f"/orgs/{org['id']}/website-project/brief", out_of_scope)

    assert response.status_code == 400


def test_should_require_scope_acceptance_and_complete_brief(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    submit = f"/orgs/{org['id']}/website-project/submit"

    not_accepted = api.post(submit, {"accept_scope": False})
    incomplete = api.post(submit, {"accept_scope": True})

    assert not_accepted.json()["code"] == "SCOPE_NOT_ACCEPTED"
    assert incomplete.status_code == 422
    assert incomplete.json()["code"] == "TRANSITION_GUARD_FAILED"
    assert _project(api, org["id"]).json()["status"] == "BRIEF_PENDING"


def test_should_start_production_with_delivery_date_and_lock_brief(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    outbox.sent.clear()

    response = _submit(api, org["id"])
    run_pending_jobs(http_client)

    body = response.json()
    assert body["status"] == "IN_PRODUCTION"
    assert body["scope_accepted_at"] is not None
    assert date.fromisoformat(body["due_on"]) == add_business_days(date.today(), 7)
    locked = api.request("PUT", f"/orgs/{org['id']}/website-project/brief", COMPLETE_BRIEF)
    assert locked.json()["code"] == "BRIEF_LOCKED"
    subjects = {m.subject for m in outbox.sent}
    assert "Brief reçu : votre site est en préparation" in subjects
    assert any(s.startswith("Nouveau site Start à produire") for s in subjects)


# ── Relecture, une série de corrections de contenu, mise en ligne ──


def test_should_run_full_delivery_with_one_content_revision(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    project_id = _submit(api, org["id"]).json()["id"]
    transition = f"/admin/website-projects/{project_id}/transition"

    assert second_api.post(transition, {"target": "CLIENT_REVIEW"}).status_code == 422
    assert _to_review(second_api, project_id).json()["status"] == "CLIENT_REVIEW"

    revision = {"items": [{"category": "TEXT", "page": "Accueil", "text": "Corriger le slogan"}]}
    first = api.post(f"/orgs/{org['id']}/website-project/revision", revision)
    assert first.json()["status"] == "REVISION"
    assert first.json()["revisions_left"] == 0

    assert _to_review(second_api, project_id).status_code == 200
    second = api.post(f"/orgs/{org['id']}/website-project/revision", revision)
    assert second.status_code == 422  # la série incluse est utilisée

    approved = api.post(f"/orgs/{org['id']}/website-project/approve")
    assert approved.json()["status"] == "APPROVED"
    assert second_api.post(transition, {"target": "LIVE"}).status_code == 422
    outbox.sent.clear()
    live = second_api.post(transition, {"target": "LIVE", "live_url": "https://salon.ci/"})
    run_pending_jobs(http_client)

    assert live.json()["status"] == "LIVE"
    assert any(m.subject == "Votre site est en ligne" for m in outbox.sent)


def test_should_refuse_design_changes_in_revision(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    project_id = _submit(api, org["id"]).json()["id"]
    _to_review(second_api, project_id)
    url = f"/orgs/{org['id']}/website-project/revision"

    design = api.post(url, {"items": [{"category": "DESIGN", "page": "Accueil", "text": "Bleu"}]})
    new_page = api.post(url, {"items": [{"category": "TEXT", "page": "Boutique", "text": "Ajout"}]})

    assert design.status_code == 400
    assert new_page.status_code == 400
    assert _project(api, org["id"]).json()["revisions_left"] == 1


# ── Qui peut faire quoi ──


def test_should_keep_team_steps_away_from_clients_and_finance(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    project_id = _submit(api, org["id"]).json()["id"]
    finance = ApiClient(http_client)
    _staff_member(finance, test_database_url, "FINANCE")

    assert _to_review(api, project_id).status_code == 403
    assert _to_review(finance, project_id).status_code == 403
    assert api.get("/admin/website-projects").status_code == 403


def test_should_require_reason_to_cancel_project(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    project_id = _project(api, org["id"]).json()["id"]
    url = f"/admin/website-projects/{project_id}/transition"

    without_reason = second_api.post(url, {"target": "CANCELLED"})
    cancelled = second_api.post(url, {"target": "CANCELLED", "reason": "Client injoignable"})

    assert without_reason.status_code == 422
    assert cancelled.json()["status"] == "CANCELLED"
    assert _project(api, org["id"]).status_code == 404


def test_should_list_projects_for_team_with_status_filter(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    _submit(api, org["id"])

    listed = second_api.get("/admin/website-projects?status=IN_PRODUCTION&limit=100").json()

    mine = [p for p in listed["data"] if p["organization_id"] == org["id"]]
    assert len(mine) == 1
    assert mine[0]["organization_name"] == org["commercial_name"]
    assert "CLIENT_REVIEW" in mine[0]["available_actions"]
