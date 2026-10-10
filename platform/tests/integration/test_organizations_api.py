import asyncio
import re
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.api_client import ApiClient

pytestmark = pytest.mark.integration


def _grant_staff_role(database_url: str, user_id: str, role: str) -> None:
    """Attribution directe en base : l'équivalent de la commande d'administration."""

    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO staff_roles (user_id, role) VALUES (:user_id, :role)"),
                {"user_id": user_id, "role": role},
            )
        await engine.dispose()

    asyncio.run(run())


def test_should_make_creator_owner_of_new_organization(api: ApiClient) -> None:
    api.register()

    organization = api.create_organization("Maquis Le Délice")

    assert organization["status"] == "LEAD"
    me = api.get("/me").json()
    assert me["memberships"][0]["organization_name"] == "Maquis Le Délice"
    assert me["memberships"][0]["role"] == "CLIENT_OWNER"
    assert "order:create" in me["memberships"][0]["permissions"]


def test_should_read_and_partially_update_own_organization(api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]

    response = api.patch(
        f"/orgs/{org_id}",
        {
            "city": "Abidjan",
            "whatsapp": "+2250700000000",
            "business_hours": {"mon": "08:00-12:00,14:00-18:00", "sun": "closed"},
        },
    )

    assert response.status_code == 200
    body = api.get(f"/orgs/{org_id}").json()
    assert body["city"] == "Abidjan"
    assert body["business_hours"]["sun"] == "closed"
    assert body["commercial_name"] == "Maquis Le Délice"  # champ non envoyé : inchangé


@pytest.mark.parametrize(
    "payload",
    [
        {"commercial_name": None},
        {"country": "Côte d'Ivoire"},
        {"phone": "0700000000"},
        {"primary_color": "rouge"},
        {"business_hours": {"lundi": "08:00-18:00"}},
        {"business_hours": {"mon": "8h-18h"}},
        {"website": "javascript:alert(1)"},
    ],
)
def test_should_reject_invalid_organization_fields(
    api: ApiClient, payload: dict[str, object]
) -> None:
    api.register()
    org_id = api.create_organization()["id"]

    response = api.patch(f"/orgs/{org_id}", payload)

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_should_list_members(api: ApiClient) -> None:
    body = api.register()
    org_id = api.create_organization()["id"]

    members = api.get(f"/orgs/{org_id}/members").json()["data"]

    assert [(m["user_id"], m["role"]) for m in members] == [(body["user"]["id"], "CLIENT_OWNER")]


def test_should_hide_organization_of_another_client(api: ApiClient, second_api: ApiClient) -> None:
    api.register()
    org_id = api.create_organization()["id"]
    second_api.register()

    response = second_api.get(f"/orgs/{org_id}")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_should_answer_404_on_every_org_route_for_non_member(
    db_app: FastAPI, api: ApiClient, second_api: ApiClient
) -> None:
    """Parcourt TOUTES les routes /orgs/{org_id}… : aucune ne doit fuiter vers un non-membre."""
    api.register()
    org_id = api.create_organization()["id"]
    second_api.register()

    # Le schéma OpenAPI (contrat publié pour le frontend) liste toutes les routes exposées
    routes = [
        (method.upper(), path.removeprefix("/api/v1"))
        for path, operations in db_app.openapi()["paths"].items()
        if path.startswith("/api/v1/orgs/{org_id}")
        for method in operations
    ]
    assert routes, "aucune route tenant trouvée : le test ne vérifierait rien"

    for method, path in routes:
        concrete = re.sub(r"\{(?!org_id)\w+\}", str(uuid.uuid4()), path).replace("{org_id}", org_id)
        response = second_api.request(method, concrete, json={})
        assert response.status_code == 404, f"{method} {path} → {response.status_code}"


def test_should_answer_404_for_unknown_organization(api: ApiClient) -> None:
    api.register()

    assert api.get(f"/orgs/{uuid.uuid4()}").status_code == 404


def test_should_forbid_admin_list_to_clients(api: ApiClient) -> None:
    api.register()

    response = api.get("/admin/organizations")

    assert response.status_code == 403


def test_should_paginate_and_filter_admin_list_for_staff(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    marker = uuid.uuid4().hex[:8]
    api.register()
    for index in range(3):
        api.create_organization(f"Salon {marker} {index}")
    staff = second_api.register()
    _grant_staff_role(test_database_url, staff["user"]["id"], "MANAGER")

    first = second_api.get(f"/admin/organizations?q={marker}&limit=2").json()
    second = second_api.get(
        f"/admin/organizations?q={marker}&limit=2&cursor={first['page']['next_cursor']}"
    ).json()

    assert [org["commercial_name"] for org in first["data"]] == [
        f"Salon {marker} 2",
        f"Salon {marker} 1",
    ]
    assert first["page"]["has_more"] is True
    assert [org["commercial_name"] for org in second["data"]] == [f"Salon {marker} 0"]
    assert second["page"]["has_more"] is False


def test_should_treat_percent_sign_literally_in_admin_search(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    staff = second_api.register()
    _grant_staff_role(test_database_url, staff["user"]["id"], "ADMIN")
    api.register()
    api.create_organization(f"Boutique {uuid.uuid4().hex[:6]}")

    response = second_api.get("/admin/organizations?q=%25%25%25unlikely%25")

    assert response.status_code == 200
    assert response.json()["data"] == []
