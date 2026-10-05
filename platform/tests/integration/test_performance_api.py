"""Suivi des résultats : mise en place de Google Business et rapports mensuels saisis par l'équipe."""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

AUGUST = {
    "metrics": {"views_search": 400, "views_maps": 200, "calls": 20, "reviews_total": 10,
                "rating_average": 4.4, "reviews_unanswered": 4},
    "top_searches": ["coiffeuse cocody"],
}  # fmt: skip
SEPTEMBER = {
    "metrics": {"views_search": 500, "views_maps": 260, "calls": 30, "reviews_total": 14,
                "rating_average": 4.6, "reviews_unanswered": 1, "directions": 12},
    "top_searches": ["coiffeuse cocody", "tresses abidjan"],
    "note": "Fiche complétée avec 12 photos et 2 publications.",
}  # fmt: skip


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, app_url="https://digital360.test/")
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


@pytest.fixture
def team(http_client: TestClient, test_database_url: str) -> ApiClient:
    member = ApiClient(http_client)
    _staff_member(member, test_database_url, "CONTENT_MANAGER")
    return member


def _client(api: ApiClient) -> dict[str, Any]:
    api.register()
    return api.create_organization("Salon Belle Afrique")


def _report_url(org_id: str, channel: str, period: str) -> str:
    return f"/admin/organizations/{org_id}/reports/{channel}/{period}"


def test_should_show_empty_tracking_with_metric_definitions(api: ApiClient) -> None:
    org = _client(api)

    body = api.get(f"/orgs/{org['id']}/performance").json()

    assert body["google_setup"]["status"] == "NOT_STARTED"
    assert [step["done"] for step in body["google_setup"]["steps"]] == [False] * 4
    assert body["manager_email"]
    google = body["channels"]["GOOGLE_BUSINESS"]
    assert google["reports"] == []
    assert "calls" in [item["key"] for item in google["definitions"]]
    assert "visitors" in [item["key"] for item in body["channels"]["WEBSITE"]["definitions"]]


def test_should_show_google_setup_progress_to_client(api: ApiClient, team: ApiClient) -> None:
    org = _client(api)

    saved = team.request(
        "PUT",
        f"/admin/organizations/{org['id']}/google-setup",
        {"status": "ACCESS_GRANTED", "profile_url": "https://maps.google.com/?cid=123",
         "note": "Validation Google en cours : surveillez vos SMS."},
    )  # fmt: skip

    assert saved.status_code == 200
    setup = api.get(f"/orgs/{org['id']}/performance").json()["google_setup"]
    assert [step["done"] for step in setup["steps"]] == [True, True, False, False]
    assert setup["profile_url"] == "https://maps.google.com/?cid=123"
    assert "SMS" in setup["note"]


def test_should_compare_each_month_with_the_previous_one(
    api: ApiClient, team: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _client(api)
    run_pending_jobs(http_client)
    outbox.sent.clear()

    team.request("PUT", _report_url(org["id"], "GOOGLE_BUSINESS", "2026-08-01"), AUGUST)
    team.request("PUT", _report_url(org["id"], "GOOGLE_BUSINESS", "2026-09-15"), SEPTEMBER)
    run_pending_jobs(http_client)

    reports = api.get(f"/orgs/{org['id']}/performance").json()["channels"]["GOOGLE_BUSINESS"][
        "reports"
    ]
    assert [r["period"] for r in reports] == ["2026-09-01", "2026-08-01"]
    latest = reports[0]
    assert latest["changes"]["calls"] == {"percent": 50, "points": None, "good": True}
    assert latest["changes"]["reviews_unanswered"]["good"] is True  # moins d'avis sans réponse
    assert latest["changes"]["rating_average"] == {"percent": None, "points": 0.2, "good": True}
    assert "directions" not in latest["changes"]  # pas mesuré en août
    assert latest["top_searches"] == ["coiffeuse cocody", "tresses abidjan"]
    assert reports[1]["changes"] == {}
    subjects = [m.subject for m in outbox.sent]
    assert "Votre rapport Google Business de septembre 2026" in subjects
    assert "Votre rapport Google Business d'août 2026" in subjects


def test_should_correct_a_month_without_notifying_again(
    api: ApiClient, team: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _client(api)
    url = _report_url(org["id"], "WEBSITE", "2026-09-01")
    team.request("PUT", url, {"metrics": {"visitors": 120}, "top_searches": ["ignoré"]})
    run_pending_jobs(http_client)
    outbox.sent.clear()

    team.request("PUT", url, {"metrics": {"visitors": 150, "whatsapp_clicks": 9}})
    run_pending_jobs(http_client)

    [report] = api.get(f"/orgs/{org['id']}/performance").json()["channels"]["WEBSITE"]["reports"]
    assert report["metrics"] == {"visitors": 150, "whatsapp_clicks": 9}
    assert report["top_searches"] == []
    assert outbox.sent == []


@pytest.mark.parametrize(
    ("channel", "period", "metrics"),
    [
        ("GOOGLE_BUSINESS", "2026-09-01", {"followers": 3}),
        ("GOOGLE_BUSINESS", "2026-09-01", {"calls": -1}),
        ("GOOGLE_BUSINESS", "2026-09-01", {"rating_average": 6}),
        ("WEBSITE", "2026-09-01", {"visitors": 12.5}),
        ("WEBSITE", "2099-01-01", {"visitors": 10}),
    ],
)
def test_should_refuse_invalid_figures(
    api: ApiClient, team: ApiClient, channel: str, period: str, metrics: dict[str, Any]
) -> None:
    org = _client(api)

    response = team.request("PUT", _report_url(org["id"], channel, period), {"metrics": metrics})

    assert response.status_code == 422


def test_should_reserve_entry_to_production_team(
    api: ApiClient, second_api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _client(api)
    finance = ApiClient(http_client)
    _staff_member(finance, test_database_url, "FINANCE")
    second_api.register()
    url = _report_url(org["id"], "WEBSITE", date.today().replace(day=1).isoformat())

    assert api.request("PUT", url, {"metrics": {"visitors": 1}}).status_code == 403
    assert finance.request("PUT", url, {"metrics": {"visitors": 1}}).status_code == 403
    assert second_api.get(f"/orgs/{org['id']}/performance").status_code in (403, 404)


def test_should_delete_a_report(api: ApiClient, team: ApiClient) -> None:
    org = _client(api)
    url = _report_url(org["id"], "WEBSITE", "2026-09-01")
    team.request("PUT", url, {"metrics": {"visitors": 120}})

    assert team.request("DELETE", url).status_code == 204
    assert team.request("DELETE", url).status_code == 404
    assert api.get(f"/orgs/{org['id']}/performance").json()["channels"]["WEBSITE"]["reports"] == []
