"""Routes /admin de composition : tableau de bord, fiche entreprise, équipe, journal d'audit.

La base de test est partagée entre les tests : les compteurs globaux sont vérifiés par
différence (avant/après), jamais par valeur absolue.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.api_client import ApiClient
from tests.integration.test_diagnostics_api import BEGINNER_ANSWERS, Diagnostic

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]


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


def _staff_member(api: ApiClient, database_url: str, role: str) -> dict[str, Any]:
    body = api.register()
    _grant_staff_role(database_url, body["user"]["id"], role)
    return body


def _client_with_diagnostic(api: ApiClient) -> dict[str, Any]:
    """Un client qui a fait son diagnostic puis créé son entreprise à partir de celui-ci."""
    diagnostic = Diagnostic(api)
    diagnostic.put_answers(BEGINNER_ANSWERS)
    assert diagnostic.complete().status_code == 200
    api.register()
    response = api.post(
        "/orgs",
        {"diagnostic_id": diagnostic.id},
        headers={"X-Diagnostic-Token": diagnostic.token},
    )
    assert response.status_code == 201, response.json()
    body: dict[str, Any] = response.json()
    return body


# ── Accès réservé à l'équipe ──


@pytest.mark.parametrize(
    "path",
    ["/admin/dashboard", "/admin/staff", "/admin/audit-logs"],
)
def test_should_forbid_admin_routes_to_clients(api: ApiClient, path: str) -> None:
    api.register()
    api.create_organization()

    response = api.get(path)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_should_forbid_organization_overview_to_clients(api: ApiClient) -> None:
    api.register()
    organization = api.create_organization()

    response = api.get(f"/admin/organizations/{organization['id']}/overview")

    assert response.status_code == 403


def test_should_require_authentication_on_admin_dashboard(api: ApiClient) -> None:
    assert api.get("/admin/dashboard").status_code == 401


def test_should_reserve_staff_management_and_audit_to_admins(
    api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "MANAGER")

    assert api.get("/admin/staff").status_code == 403
    assert api.get("/admin/audit-logs").status_code == 403
    assert api.get("/admin/dashboard").status_code == 200


# ── Tableau de bord ──


def test_should_count_new_claimed_diagnostic_and_user_in_dashboard(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "MANAGER")
    before = api.get("/admin/dashboard").json()

    _client_with_diagnostic(second_api)
    after = api.get("/admin/dashboard").json()

    assert after["diagnostics"]["claimed"] == before["diagnostics"]["claimed"] + 1
    assert (
        after["diagnostics"]["completed_last_24h"]
        == before["diagnostics"]["completed_last_24h"] + 1
    )
    assert after["users"]["total"] == before["users"]["total"] + 1
    assert after["users"]["created_last_7_days"] == before["users"]["created_last_7_days"] + 1
    assert 0 < after["diagnostics"]["conversion_rate"] <= 1
    assert after["diagnostics"]["average_score"] is not None
    assert set(after["organizations_by_status"]) == {"LEAD", "ACTIVE", "SUSPENDED", "CHURNED"}
    assert sum(after["organizations_by_status"].values()) == (
        sum(before["organizations_by_status"].values()) + 1
    )


# ── Fiche entreprise ──


def test_should_assemble_full_organization_overview_for_manager(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    organization = _client_with_diagnostic(second_api)
    _staff_member(api, test_database_url, "MANAGER")

    response = api.get(f"/admin/organizations/{organization['id']}/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["organization"]["id"] == organization["id"]
    assert [member["role"] for member in body["members"]] == ["CLIENT_OWNER"]
    assert len(body["diagnostics"]) == 1
    assert body["passport"]
    assert isinstance(body["entitlements"], list)


def test_should_hide_diagnostics_and_passport_from_finance_role(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    organization = _client_with_diagnostic(second_api)
    _staff_member(api, test_database_url, "FINANCE")

    body = api.get(f"/admin/organizations/{organization['id']}/overview").json()

    assert body["diagnostics"] is None
    assert body["passport"] is None
    assert body["members"]


def test_should_answer_404_for_unknown_organization_overview(
    api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "MANAGER")

    response = api.get("/admin/organizations/00000000-0000-7000-8000-000000000000/overview")

    assert response.status_code == 404


# ── Équipe BENILAB ──


def test_should_grant_list_and_revoke_staff_role(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "ADMIN")
    colleague = second_api.register()
    email = colleague["user"]["email"]
    colleague_id = colleague["user"]["id"]

    granted = api.post("/admin/staff", {"email": email.upper(), "role": "CONTENT_MANAGER"})

    assert granted.status_code == 201
    assert granted.json()["roles"] == ["CONTENT_MANAGER"]
    assert second_api.get("/admin/dashboard").status_code == 200
    listed = {member["user_id"]: member for member in api.get("/admin/staff").json()["data"]}
    assert listed[colleague_id]["roles"] == ["CONTENT_MANAGER"]

    revoked = api.request("DELETE", f"/admin/staff/{colleague_id}/roles/CONTENT_MANAGER")

    assert revoked.status_code == 204
    assert second_api.get("/admin/dashboard").status_code == 403


def test_should_be_idempotent_when_granting_same_role_twice(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "ADMIN")
    email = second_api.register()["user"]["email"]

    api.post("/admin/staff", {"email": email, "role": "FINANCE"})
    again = api.post("/admin/staff", {"email": email, "role": "FINANCE"})

    assert again.status_code == 201
    assert again.json()["roles"] == ["FINANCE"]


def test_should_answer_404_when_granting_role_to_unknown_email(
    api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "ADMIN")

    response = api.post("/admin/staff", {"email": "personne@inconnu.ci", "role": "MANAGER"})

    assert response.status_code == 404


def test_should_forbid_admin_to_revoke_own_admin_role(
    api: ApiClient, test_database_url: str
) -> None:
    admin = _staff_member(api, test_database_url, "ADMIN")

    response = api.request("DELETE", f"/admin/staff/{admin['user']['id']}/roles/ADMIN")

    assert response.status_code == 403
    assert api.get("/admin/staff").status_code == 200


def test_should_answer_404_when_revoking_role_not_assigned(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "ADMIN")
    colleague_id = second_api.register()["user"]["id"]

    response = api.request("DELETE", f"/admin/staff/{colleague_id}/roles/MANAGER")

    assert response.status_code == 404


def test_should_require_csrf_to_grant_staff_role(api: ApiClient, test_database_url: str) -> None:
    _staff_member(api, test_database_url, "ADMIN")

    response = api.post_without_csrf("/admin/staff", {"email": "x@exemple.ci", "role": "MANAGER"})

    assert response.status_code == 403


# ── Journal d'audit ──


def test_should_record_staff_changes_in_audit_log(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(api, test_database_url, "ADMIN")
    colleague_id = second_api.register()["user"]["id"]
    email = second_api.get("/me").json()["user"]["email"]
    api.post("/admin/staff", {"email": email, "role": "DEVELOPER"})

    entries = api.get("/admin/audit-logs?action=staff_role.grant&limit=50").json()["data"]

    assert all(entry["action"] == "staff_role.grant" for entry in entries)
    latest = next(entry for entry in entries if entry["entity_id"] == colleague_id)
    assert latest["new_value"] == {"role": "DEVELOPER"}
    assert latest["actor_type"] == "USER"


def test_should_filter_audit_log_by_organization_and_paginate(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    organization = _client_with_diagnostic(second_api)
    second_api.patch(f"/orgs/{organization['id']}", {"commercial_name": "Maquis Renommé"})
    _staff_member(api, test_database_url, "ADMIN")

    path = f"/admin/audit-logs?organization_id={organization['id']}"
    everything = api.get(path).json()["data"]
    first_page = api.get(f"{path}&limit=1").json()

    assert len(everything) >= 2
    assert all(entry["organization_id"] == organization["id"] for entry in everything)
    assert [entry["id"] for entry in first_page["data"]] == [everything[0]["id"]]


# ── Suppression définitive d'une entreprise ──


def test_should_delete_organization_after_name_confirmation_and_keep_accounts(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "ADMIN")
    # Même entreprise, diagnostic refait sans le rattacher ; et un prospect sans rapport
    related = Diagnostic(ApiClient(api.http))
    related.put_answers({**BEGINNER_ANSWERS, "company": org["commercial_name"].upper()})
    related.complete()
    unrelated = Diagnostic(ApiClient(api.http))
    unrelated.put_answers(
        {
            **BEGINNER_ANSWERS,
            "company": "Autre Boutique Sans Rapport",
            "phone": "+22990000001",
            "whatsapp": "+22990000002",
        }
    )
    unrelated.complete()

    preview = second_api.get(f"/admin/organizations/{org['id']}/deletion-preview").json()
    wrong = second_api.post(f"/admin/organizations/{org['id']}/delete", {"confirm_name": "Autre"})
    deleted = second_api.post(
        f"/admin/organizations/{org['id']}/delete",
        {"confirm_name": "  " + org["commercial_name"].upper() + " "},
    )

    assert (preview["members"], preview["diagnostics"], preview["payments"]) == (1, 1, 0)
    assert preview["prospects"] >= 1
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "CONFIRMATION_MISMATCH"
    assert deleted.status_code == 204
    assert second_api.get(f"/admin/organizations/{org['id']}").status_code == 404
    # Le compte du client existe toujours, sans l'entreprise supprimée
    me = api.get("/me")
    assert me.status_code == 200
    assert me.json()["memberships"] == []
    prospects = [d["id"] for d in second_api.get("/admin/diagnostics?limit=100").json()["data"]]
    assert related.id not in prospects
    assert unrelated.id in prospects
    logs = second_api.get("/admin/audit-logs?action=organization.delete").json()["data"]
    assert any(item["entity_id"] == org["id"] for item in logs)


def test_should_require_explicit_confirmation_to_delete_payments(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "ADMIN")
    second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_START", "payment": {"method": "WAVE", "reference": "WV-DEL"}},
    )
    url = f"/admin/organizations/{org['id']}/delete"

    refused = second_api.post(url, {"confirm_name": org["commercial_name"]})
    accepted = second_api.post(
        url, {"confirm_name": org["commercial_name"], "delete_payments": True}
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "HAS_PAYMENTS"
    assert accepted.status_code == 204


def test_should_reserve_organization_deletion_to_admins(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        f"/admin/organizations/{org['id']}/delete", {"confirm_name": org["commercial_name"]}
    )

    assert response.status_code == 403
    assert api.get(f"/orgs/{org['id']}").status_code == 200
