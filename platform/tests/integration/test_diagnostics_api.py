"""Parcours du diagnostic tel que le frontend de Kilo l'exécute : anonyme, puis rattaché."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.api_client import ApiClient

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

BEGINNER_ANSWERS: dict[str, Any] = {
    "company": "Salon Belle Afrique",
    "country": "CI",
    "sector": "beauty",
    "city": "Abidjan",
    "phone": "+225 07 00 00 00 00",
    "whatsapp": "+2250500000000",
    "site": "non",
    "domain": "non",
    "emailprof": "non",
    "gb": "en-cours",
    "reviews": "0-5",
    "socials": ["instagram"],
    "ads": "none",
    "contact": "whatsapp",
    "booking": "none",
    "clientbase": "notebook",
    "comm": "whatsapp",
    "freq": "monthly",
    "producer": "internal",
    "goal": "presence",
}

CONSENTS = {"consents": {"privacy": True, "marketing_whatsapp": True}}


class Diagnostic:
    """Un diagnostic anonyme : identifiant + jeton envoyé dans X-Diagnostic-Token."""

    def __init__(self, api: ApiClient) -> None:
        body = api.post_without_csrf("/public/diagnostics", {"source": {"utm_source": "facebook"}})
        assert body.status_code == 201, body.json()
        data = body.json()
        self.api, self.id, self.token = api, data["id"], data["token"]

    def headers(self, token: str | None = None) -> dict[str, str]:
        return {"X-Diagnostic-Token": token or self.token}

    def put_answers(self, answers: dict[str, Any], token: str | None = None) -> Any:
        return self.api.put_without_csrf(
            f"/public/diagnostics/{self.id}/answers",
            {"answers": answers},
            headers=self.headers(token),
        )

    def complete(self, body: dict[str, Any] = CONSENTS) -> Any:
        return self.api.post_without_csrf(
            f"/public/diagnostics/{self.id}/complete", body, headers=self.headers()
        )


def _query(database_url: str, sql: str, **params: Any) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.scope', 'staff', true)"))
            rows = (await connection.execute(text(sql), params)).all()
        await engine.dispose()
        return list(rows)

    return asyncio.run(run())


# ── Questionnaire ──


def test_should_serve_questionnaire_without_scoring_points(api: ApiClient) -> None:
    body = api.get("/public/questionnaire").json()

    assert body["version"] == 2
    questions = {q["key"]: q for section in body["sections"] for q in section["questions"]}
    assert questions["site"]["options"][0] == {
        "value": "oui",
        "label": "Oui, mon site est en ligne",
    }
    assert questions["seo"]["visible_if"] == {"fact": "site", "eq": "oui"}
    assert questions["country"]["required"] is True
    assert "points" not in str(body)


# ── Parcours anonyme ──


def test_should_save_partial_answers_and_report_missing_required(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)

    response = diagnostic.put_answers({"company": "Salon", "phone": "+225 07 00 00 00 00"})

    assert response.status_code == 200
    body = response.json()
    assert body["answers"]["phone"] == "+2250700000000"
    assert body["missing_required"] == ["country", "site", "gb", "ads", "goal"]


def test_should_reject_invalid_answers_with_field_details(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)

    response = diagnostic.put_answers({"site": "peut-être", "email": "pas-un-email"})

    assert response.status_code == 400
    errors = {e["field"]: e["reason"] for e in response.json()["errors"]}
    assert errors == {"answers.site": "unknown_option", "answers.email": "invalid_email"}


def test_should_answer_404_without_or_with_wrong_token(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    other = Diagnostic(api)

    without_token = api.put_without_csrf(
        f"/public/diagnostics/{diagnostic.id}/answers", {"answers": {}}
    )
    # Jeton valide… mais d'un autre diagnostic
    foreign_token = diagnostic.put_answers({"company": "X"}, token=other.token)

    assert without_token.status_code == foreign_token.status_code == 404


def test_should_require_privacy_consent(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)

    response = diagnostic.complete({"consents": {"privacy": False}})

    assert response.status_code == 422
    assert response.json()["code"] == "CONSENT_REQUIRED"


def test_should_refuse_completing_incomplete_questionnaire(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers({"company": "Salon"})

    response = diagnostic.complete()

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "QUESTIONNAIRE_INCOMPLETE"
    assert {e["field"] for e in body["errors"]} >= {"answers.site", "answers.goal"}


def test_should_complete_and_return_score_passport_and_plan(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)

    response = diagnostic.complete()

    assert response.status_code == 200
    result = response.json()
    score = result["score"]
    assert 0 <= score["total"] <= 100
    assert set(score["categories"]) == {
        "PRESENCE",
        "VISIBILITY",
        "ACQUISITION",
        "CONVERSION",
        "RETENTION",
    }
    assert score["categories"]["PRESENCE"]["label"] == "Présence"
    assert score["disclaimer"].startswith("Le Digital Score est un indicateur interne")
    passport = {item["key"]: item for item in result["passport"]["items"]}
    assert passport["GOOGLE_BUSINESS"]["status"] == "IN_PROGRESS"
    assert passport["INSTAGRAM"] == {
        "key": "INSTAGRAM",
        "status": "ACTIVE",
        "source": "DECLARED",
        "details": {},
        "status_changed_at": None,
    }
    plan = result["action_plan"]["items"]
    assert plan[0]["rule_key"] == "no_website"
    assert plan[0]["product_code"] == "DIGITAL_START"
    assert all(item["status"] == "PROPOSED" for item in plan)


def test_should_return_same_result_when_completed_twice(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)

    first = diagnostic.complete().json()
    second = diagnostic.complete().json()
    stored = api.get_with(
        f"/public/diagnostics/{diagnostic.id}/result", diagnostic.headers()
    ).json()

    assert first == second == stored


def test_should_freeze_answers_after_completion(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)
    diagnostic.complete()

    response = diagnostic.put_answers({"site": "oui"})

    assert response.status_code == 409
    assert response.json()["code"] == "DIAGNOSTIC_ALREADY_COMPLETED"


def test_should_ignore_answers_to_questions_that_became_hidden(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers({**BEGINNER_ANSWERS, "site": "oui", "seo": "top3"})
    diagnostic.put_answers({"site": "non"})  # la question SEO disparaît

    result = diagnostic.complete().json()

    passport = {item["key"]: item["status"] for item in result["passport"]["items"]}
    assert passport["SEO"] == "NOT_CONFIGURED"


def test_should_record_consents(api: ApiClient, test_database_url: str) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)
    diagnostic.complete()

    rows = _query(
        test_database_url,
        "SELECT purpose, granted FROM consent_records WHERE subject_id = :id ORDER BY purpose",
        id=diagnostic.id,
    )

    assert [tuple(row) for row in rows] == [
        ("MARKETING_EMAIL", False),
        ("MARKETING_WHATSAPP", True),
        ("PRIVACY", True),
    ]


# ── Rattachement à l'inscription ──


def _completed_diagnostic(api: ApiClient) -> Diagnostic:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)
    assert diagnostic.complete().status_code == 200
    return diagnostic


def _claim(api: ApiClient, diagnostic: Diagnostic, **body: Any) -> Any:
    return api.post(
        "/orgs",
        {"diagnostic_id": diagnostic.id, **body},
        headers={"X-Diagnostic-Token": diagnostic.token},
    )


def test_should_create_organization_from_diagnostic(api: ApiClient) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()

    response = _claim(api, diagnostic)

    assert response.status_code == 201, response.json()
    organization = response.json()
    assert organization["commercial_name"] == "Salon Belle Afrique"
    assert (organization["country"], organization["city"]) == ("CI", "Abidjan")
    assert organization["whatsapp"] == "+2250500000000"
    org_id = organization["id"]

    passport = {i["key"]: i for i in api.get(f"/orgs/{org_id}/passport").json()["items"]}
    assert passport["GOOGLE_BUSINESS"]["status"] == "IN_PROGRESS"
    assert passport["GOOGLE_BUSINESS"]["source"] == "DECLARED"
    assert passport["HOSTING"]["status"] == "NOT_CONFIGURED"

    plan = api.get(f"/orgs/{org_id}/action-plan").json()["items"]
    assert plan[0]["rule_key"] == "no_website"
    history = api.get(f"/orgs/{org_id}/diagnostics").json()["data"]
    assert [item["status"] for item in history] == ["CLAIMED"]


def test_should_let_body_override_diagnostic_answers(api: ApiClient) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()

    response = _claim(api, diagnostic, commercial_name="Belle Afrique SARL")

    assert response.json()["commercial_name"] == "Belle Afrique SARL"


def test_should_refuse_claiming_a_diagnostic_twice(api: ApiClient, second_api: ApiClient) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()
    assert _claim(api, diagnostic).status_code == 201
    second_api.register()

    response = _claim(second_api, diagnostic)

    assert response.status_code == 409
    assert response.json()["code"] == "DIAGNOSTIC_ALREADY_CLAIMED"


def test_should_refuse_claiming_without_token(api: ApiClient) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()

    response = api.post("/orgs", {"diagnostic_id": diagnostic.id})

    assert response.status_code == 404


def test_should_refuse_claiming_unfinished_diagnostic(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)
    api.register()

    response = _claim(api, diagnostic)

    assert response.status_code == 409
    assert response.json()["code"] == "DIAGNOSTIC_NOT_COMPLETED"


def test_should_keep_claimed_diagnostic_out_of_anonymous_reach_of_other_orgs(
    api: ApiClient, second_api: ApiClient
) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()
    org_id = _claim(api, diagnostic).json()["id"]
    second_api.register()

    assert second_api.get(f"/orgs/{org_id}/action-plan").status_code == 404
    assert second_api.get(f"/orgs/{org_id}/passport").status_code == 404


def test_should_dismiss_recommendation(api: ApiClient) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()
    org_id = _claim(api, diagnostic).json()["id"]
    item = api.get(f"/orgs/{org_id}/action-plan").json()["items"][0]

    response = api.patch(f"/orgs/{org_id}/action-plan/items/{item['id']}", {"status": "DISMISSED"})

    assert response.status_code == 200
    assert response.json()["status"] == "DISMISSED"
    refused = api.patch(f"/orgs/{org_id}/action-plan/items/{item['id']}", {"status": "DONE"})
    assert refused.status_code == 400


def test_should_return_empty_passport_for_organization_without_diagnostic(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]

    items = api.get(f"/orgs/{org_id}/passport").json()["items"]

    assert len(items) == 16
    assert {item["status"] for item in items} == {"NOT_CONFIGURED"}
    assert api.get(f"/orgs/{org_id}/action-plan").json() == {"items": []}


# ── Admin : prospects captés ──


def test_should_list_captured_diagnostics_for_staff_only(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    diagnostic = _completed_diagnostic(api)
    api.register()
    assert api.get("/admin/diagnostics").status_code == 403
    staff = second_api.register()
    _query(
        test_database_url,
        "INSERT INTO staff_roles (user_id, role) VALUES (:user_id, 'MANAGER') RETURNING user_id",
        user_id=staff["user"]["id"],
    )

    body = second_api.get("/admin/diagnostics?status=COMPLETED&limit=100").json()

    entry = next(item for item in body["data"] if item["id"] == diagnostic.id)
    assert entry["company"] == "Salon Belle Afrique"
    assert entry["phone"] == "+2250700000000"
    assert entry["marketing_whatsapp_consent"] is True
    assert entry["total_score"] is not None


def test_should_rate_limit_diagnostic_creation(api: ApiClient) -> None:
    statuses = [api.post_without_csrf("/public/diagnostics").status_code for _ in range(31)]

    assert statuses.count(201) == 30
    assert statuses[-1] == 429


def test_should_not_leak_diagnostic_via_unknown_id(api: ApiClient) -> None:
    diagnostic = Diagnostic(api)

    response = api.get_with(f"/public/diagnostics/{uuid.uuid4()}/result", diagnostic.headers())

    assert response.status_code == 404


# ── Rattachement à une entreprise existante (connexion après le diagnostic) ──


def _claim_existing(api: ApiClient, org_id: str, diagnostic: Diagnostic) -> Any:
    return api.post(
        f"/orgs/{org_id}/diagnostics/claim",
        {"diagnostic_id": diagnostic.id},
        headers={"X-Diagnostic-Token": diagnostic.token},
    )


def test_should_attach_new_diagnostic_to_existing_organization(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    diagnostic = _completed_diagnostic(api)

    response = _claim_existing(api, org_id, diagnostic)

    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "CLAIMED"
    history = api.get(f"/orgs/{org_id}/diagnostics").json()["data"]
    assert [item["id"] for item in history] == [diagnostic.id]
    passport = {i["key"]: i["status"] for i in api.get(f"/orgs/{org_id}/passport").json()["items"]}
    assert passport["GOOGLE_BUSINESS"] == "IN_PROGRESS"
    assert api.get(f"/orgs/{org_id}/action-plan").json()["items"][0]["rule_key"] == "no_website"


def test_should_keep_verified_passport_items_when_attaching(
    api: ApiClient, test_database_url: str
) -> None:
    api.register()
    first = _completed_diagnostic(api)
    org_id = _claim(api, first).json()["id"]
    # L'équipe BENILAB a vérifié que le site est en ligne
    _query(
        test_database_url,
        "UPDATE passport_items SET status = 'ACTIVE', source = 'VERIFIED' "
        "WHERE organization_id = :org AND item_key = 'WEBSITE' RETURNING id",
        org=org_id,
    )
    second = _completed_diagnostic(api)  # déclare toujours « pas de site »

    assert _claim_existing(api, org_id, second).status_code == 200

    items = {i["key"]: i for i in api.get(f"/orgs/{org_id}/passport").json()["items"]}
    assert (items["WEBSITE"]["status"], items["WEBSITE"]["source"]) == ("ACTIVE", "VERIFIED")
    assert len(api.get(f"/orgs/{org_id}/diagnostics").json()["data"]) == 2


def test_should_refuse_attaching_twice_or_without_token(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    diagnostic = _completed_diagnostic(api)
    assert _claim_existing(api, org_id, diagnostic).status_code == 200

    again = _claim_existing(api, org_id, diagnostic)
    without_token = api.post(f"/orgs/{org_id}/diagnostics/claim", {"diagnostic_id": diagnostic.id})

    assert again.status_code == 409
    assert again.json()["code"] == "DIAGNOSTIC_ALREADY_CLAIMED"
    assert without_token.status_code == 404


def test_should_refuse_attaching_an_unfinished_diagnostic(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)

    response = _claim_existing(api, org_id, diagnostic)

    assert response.status_code == 409
    assert response.json()["code"] == "DIAGNOSTIC_NOT_COMPLETED"
