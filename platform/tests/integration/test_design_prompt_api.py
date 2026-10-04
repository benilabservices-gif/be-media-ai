"""Prompt de conception : rédigé à l'envoi du brief, envoyé en PDF à l'équipe, amélioré par elle."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _staff_member
from tests.integration.test_projects_api import APP_URL, TEAM, _project, _submit, _won_sale

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]


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


def _submitted_project(
    api: ApiClient, staff: ApiClient, http_client: TestClient, database_url: str
) -> str:
    org = _won_sale(api, staff, http_client, database_url)
    assert _submit(api, org["id"]).status_code == 200
    project_id: str = _project(api, org["id"]).json()["id"]
    return project_id


def test_should_email_design_prompt_pdf_to_team_when_brief_is_submitted(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    _submitted_project(api, second_api, http_client, test_database_url)
    outbox.sent.clear()

    run_pending_jobs(http_client)

    team_mail = next(m for m in outbox.sent if m.subject.startswith("Nouveau site Start"))
    [pdf] = team_mail.attachments
    assert pdf.filename == "prompt-conception-salon-belle-afrique.pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "joint en PDF" in team_mail.text


def test_should_write_prompt_from_brief_within_offer_scope(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    project_id = _submitted_project(api, second_api, http_client, test_database_url)

    body = second_api.get(f"/admin/website-projects/{project_id}/design-prompt").json()

    prompt = body["prompt"]
    assert body["saved"] is True and body["edited_at"] is None
    assert "SALON BELLE AFRIQUE" in prompt
    assert "Coiffure, tresses et soins" in prompt
    assert "- Tresses : Toutes longueurs" in prompt
    assert "Chaleureux" in prompt and "Savane" in prompt
    assert "salonbelleafrique.ci" in prompt
    assert "PAGES (exactement 5, aucune autre)" in prompt
    assert "Un design sur mesure ou une maquette créée de zéro" in prompt
    # Champs facultatifs vides : signalés à l'équipe
    assert "À VÉRIFIER PAR L'ÉQUIPE" in prompt and "- horaires" in prompt


def test_should_let_team_improve_prompt_then_download_it_as_pdf(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    project_id = _submitted_project(api, second_api, http_client, test_database_url)
    url = f"/admin/website-projects/{project_id}/design-prompt"
    improved = "PROMPT AMÉLIORÉ\nAjouter une section tarifs indicatifs fournie par le client."

    saved = second_api.request("PUT", url, {"prompt": improved})
    pdf = second_api.get(url + ".pdf")

    assert saved.status_code == 200
    assert saved.json()["prompt"] == improved
    assert saved.json()["edited_by_name"]
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert "prompt-conception-salon-belle-afrique.pdf" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")


def test_should_regenerate_prompt_from_current_brief(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    project_id = _submitted_project(api, second_api, http_client, test_database_url)
    url = f"/admin/website-projects/{project_id}/design-prompt"
    second_api.request("PUT", url, {"prompt": "Version de travail de l'équipe à jeter."})

    regenerated = second_api.post(url + "/regenerate").json()

    assert "SALON BELLE AFRIQUE" in regenerated["prompt"]


def test_should_reserve_prompt_to_team_and_editing_to_producers(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    test_database_url: str,
) -> None:
    project_id = _submitted_project(api, second_api, http_client, test_database_url)
    url = f"/admin/website-projects/{project_id}/design-prompt"
    finance = ApiClient(http_client)
    _staff_member(finance, test_database_url, "FINANCE")

    assert api.get(url).status_code == 403
    assert finance.get(url).status_code == 403
    assert (
        finance.request("PUT", url, {"prompt": "Tentative de modification hors équipe"}).status_code
        == 403
    )


def test_should_preview_prompt_before_brief_is_submitted(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _won_sale(api, second_api, http_client, test_database_url)
    project_id = _project(api, org["id"]).json()["id"]

    body = second_api.get(f"/admin/website-projects/{project_id}/design-prompt").json()

    assert body["saved"] is False
    assert "À VÉRIFIER PAR L'ÉQUIPE" in body["prompt"]
