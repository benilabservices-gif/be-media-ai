import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.api_client import ApiClient
from tests.integration.test_diagnostics_api import BEGINNER_ANSWERS, Diagnostic

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]


def _make_staff(database_url: str, user_id: str, role: str = "MANAGER") -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO staff_roles (user_id, role) VALUES (:user_id, :role)"),
                {"user_id": user_id, "role": role},
            )
        await engine.dispose()

    asyncio.run(run())


def _plan_prices(api: ApiClient, answers: dict[str, Any]) -> dict[str, Any]:
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(answers)
    result = diagnostic.complete().json()
    return {item["rule_key"]: item["price"] for item in result["action_plan"]["items"]}


# ── Catalogue public ──


def test_should_serve_public_catalog_in_xof_by_default(api: ApiClient) -> None:
    body = api.get("/public/catalog").json()

    assert body["currency"] == "XOF"
    assert body["available_currencies"] == ["XOF", "XAF", "EUR"]
    products = {product["code"]: product for product in body["products"]}
    assert set(products) == {
        "DIGITAL_START",
        "DIGITAL_ESSENTIAL",
        "DIGITAL_GROWTH",
        "DIGITAL_PERFORMANCE",
    }
    assert products["DIGITAL_START"]["price"] == {
        "amount": 109900,
        "currency": "XOF",
        "period": "NONE",
    }
    assert products["DIGITAL_GROWTH"]["price"]["period"] == "MONTH"
    assert "ANNUAL_RENEWAL" not in products  # offre interne, non publique


def test_should_convert_prices_to_euros_on_request(api: ApiClient) -> None:
    body = api.get("/public/catalog?currency=EUR").json()

    assert body["currency"] == "EUR"
    prices = {product["code"]: product["price"] for product in body["products"]}
    assert prices["DIGITAL_START"] == {"amount": 16800, "currency": "EUR", "period": "NONE"}
    assert prices["DIGITAL_ESSENTIAL"] == {"amount": 6900, "currency": "EUR", "period": "MONTH"}
    assert all(product["available"] for product in body["products"])


def test_should_reject_unknown_currency(api: ApiClient) -> None:
    response = api.get("/public/catalog?currency=USD")

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


# ── Prix dans le plan d'action ──


def test_should_price_recommendations_in_local_currency(api: ApiClient) -> None:
    prices = _plan_prices(api, BEGINNER_ANSWERS)

    assert prices["no_website"] == {"amount": 109900, "currency": "XOF", "period": "NONE"}
    assert prices["no_online_booking"] is None  # recommandation sans produit associé


def test_should_price_recommendations_in_xaf_and_eur_by_country(api: ApiClient) -> None:
    cameroon = _plan_prices(api, {**BEGINNER_ANSWERS, "country": "CM"})
    france = _plan_prices(api, {**BEGINNER_ANSWERS, "country": "FR"})

    assert cameroon["no_website"] == {"amount": 109900, "currency": "XAF", "period": "NONE"}
    assert france["no_website"] == {"amount": 16800, "currency": "EUR", "period": "NONE"}


# ── Droits (entitlements) ──


def test_should_grant_no_entitlement_to_new_organization(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]

    entitlements = {
        e["key"]: e for e in api.get(f"/orgs/{org_id}/entitlements").json()["entitlements"]
    }

    assert entitlements["SOCIAL_MANAGEMENT"]["value"] is False
    assert entitlements["CONTENT_MONTHLY_LIMIT"]["value"] == 0
    assert entitlements["CONTENT_MONTHLY_LIMIT"]["type"] == "LIMIT"
    assert all(e["sources"] == [] for e in entitlements.values())


def test_should_apply_and_revoke_staff_override(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    staff = second_api.register()
    _make_staff(test_database_url, staff["user"]["id"])

    created = second_api.post(
        f"/admin/organizations/{org_id}/entitlement-overrides",
        {"entitlement_key": "CONTENT_MONTHLY_LIMIT", "value": 6, "reason": "Offre de lancement"},
    )
    assert created.status_code == 201, created.json()

    entitlement = next(
        e
        for e in api.get(f"/orgs/{org_id}/entitlements").json()["entitlements"]
        if e["key"] == "CONTENT_MONTHLY_LIMIT"
    )
    assert entitlement["value"] == 6
    assert entitlement["sources"][0]["reason"] == "Offre de lancement"

    override_id = created.json()["id"]
    revoked = second_api.request(
        "DELETE", f"/admin/organizations/{org_id}/entitlement-overrides/{override_id}"
    )
    assert revoked.status_code == 204
    entitlements = {
        e["key"]: e["value"] for e in api.get(f"/orgs/{org_id}/entitlements").json()["entitlements"]
    }
    assert entitlements["CONTENT_MONTHLY_LIMIT"] == 0


def test_should_ignore_expired_override(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    staff = second_api.register()
    _make_staff(test_database_url, staff["user"]["id"])
    second_api.post(
        f"/admin/organizations/{org_id}/entitlement-overrides",
        {
            "entitlement_key": "SEO",
            "value": True,
            "reason": "Essai 1 seconde",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        },
    )

    async def wait() -> None:
        await asyncio.sleep(1.5)

    asyncio.run(wait())
    entitlements = {
        e["key"]: e["value"] for e in api.get(f"/orgs/{org_id}/entitlements").json()["entitlements"]
    }
    assert entitlements["SEO"] is False


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"entitlement_key": "INCONNU", "value": True, "reason": "test"}, "unknown"),
        ({"entitlement_key": "SEO", "value": 3, "reason": "test"}, "wrong_type"),
        (
            {"entitlement_key": "CONTENT_MONTHLY_LIMIT", "value": True, "reason": "test"},
            "wrong_type",
        ),
    ],
)
def test_should_reject_invalid_override(
    api: ApiClient,
    second_api: ApiClient,
    test_database_url: str,
    payload: dict[str, Any],
    reason: str,
) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    staff = second_api.register()
    _make_staff(test_database_url, staff["user"]["id"])

    response = second_api.post(f"/admin/organizations/{org_id}/entitlement-overrides", payload)

    assert response.status_code == 400
    assert response.json()["errors"][0]["reason"] == reason


def test_should_forbid_overrides_to_clients_and_content_managers(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    payload = {"entitlement_key": "SEO", "value": True, "reason": "je me l'offre"}

    assert (
        api.post(f"/admin/organizations/{org_id}/entitlement-overrides", payload).status_code == 403
    )

    staff = second_api.register()
    _make_staff(test_database_url, staff["user"]["id"], role="CONTENT_MANAGER")
    assert (
        second_api.post(f"/admin/organizations/{org_id}/entitlement-overrides", payload).status_code
        == 403
    )


def test_should_answer_404_when_revoking_unknown_override(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    staff = second_api.register()
    _make_staff(test_database_url, staff["user"]["id"])

    response = second_api.request(
        "DELETE", f"/admin/organizations/{org_id}/entitlement-overrides/{uuid.uuid4()}"
    )

    assert response.status_code == 404
