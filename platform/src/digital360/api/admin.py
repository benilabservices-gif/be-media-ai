"""Routes d'administration de composition : elles assemblent plusieurs modules.

Toutes exigent un rôle staff (permission vérifiée sur chaque route). Un client reçoit 403.
"""

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, EmailStr

from digital360.core.actor import Actor
from digital360.core.audit import list_audit_logs
from digital360.core.csrf import require_csrf
from digital360.core.pagination import PageInfo, PageParams, build_page_info, page_params
from digital360.core.permissions import Permission, Principal, StaffRole
from digital360.core.tenancy import TenantContext, staff_transaction
from digital360.modules.catalog.api.routes import EntitlementOut, get_catalog_service
from digital360.modules.catalog.application.service import CatalogService
from digital360.modules.diagnostics.api.routes import get_diagnostic_service, to_result_out
from digital360.modules.diagnostics.api.schemas import DiagnosticResultOut
from digital360.modules.diagnostics.application.service import DiagnosticService
from digital360.modules.identity.api.dependencies import require_staff_permission
from digital360.modules.identity.application.staff_service import StaffService
from digital360.modules.organizations.api.routes import Service as OrganizationServiceDep
from digital360.modules.organizations.api.schemas import MemberOut, OrganizationOut
from digital360.modules.passport.api.routes import PassportItemOut, get_passport_service
from digital360.modules.passport.application.service import PassportService

router = APIRouter(prefix="/admin", tags=["admin"])


def get_staff_service(request: Request) -> StaffService:
    service: StaffService = request.app.state.staff_service
    return service


Diagnostics = Annotated[DiagnosticService, Depends(get_diagnostic_service)]
Passports = Annotated[PassportService, Depends(get_passport_service)]
Catalog = Annotated[CatalogService, Depends(get_catalog_service)]
Staff = Annotated[StaffService, Depends(get_staff_service)]


def _staff(permission: Permission) -> Any:
    return Annotated[Principal, Depends(require_staff_permission(permission))]


# ── Tableau de bord ──


class DiagnosticKpis(BaseModel):
    in_progress: int
    completed_not_claimed: int
    claimed: int
    completed_last_24h: int
    completed_last_7_days: int
    # Part des diagnostics terminés qui ont abouti à un compte (0 à 1), null si aucun
    conversion_rate: float | None
    average_score: float | None


class UserKpis(BaseModel):
    total: int
    created_last_7_days: int


class DashboardOut(BaseModel):
    diagnostics: DiagnosticKpis
    organizations_by_status: dict[str, int]
    users: UserKpis


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    _: _staff(Permission.ORGANIZATION_READ),  # type: ignore[valid-type]
    organizations: OrganizationServiceDep,
    diagnostics: Diagnostics,
    staff: Staff,
) -> DashboardOut:
    return DashboardOut(
        diagnostics=DiagnosticKpis(**await diagnostics.admin_stats()),
        organizations_by_status=await organizations.admin_stats(),
        users=UserKpis(**asdict(await staff.user_stats())),
    )


# ── Fiche entreprise complète ──


class OrganizationOverview(BaseModel):
    organization: OrganizationOut
    members: list[MemberOut]
    # null quand le rôle n'a pas le droit de lire la section (ex. FINANCE)
    diagnostics: list[DiagnosticResultOut] | None
    passport: list[PassportItemOut] | None
    entitlements: list[EntitlementOut]


@router.get("/organizations/{organization_id}/overview", response_model=OrganizationOverview)
async def get_organization_overview(
    organization_id: uuid.UUID,
    principal: _staff(Permission.ORGANIZATION_READ),  # type: ignore[valid-type]
    organizations: OrganizationServiceDep,
    diagnostics: Diagnostics,
    passports: Passports,
    catalog: Catalog,
) -> OrganizationOverview:
    organization = await organizations.admin_get(organization_id)  # 404 si inconnue
    context = TenantContext(organization_id=organization_id)
    permissions = principal.staff_permissions()
    return OrganizationOverview(
        organization=OrganizationOut.model_validate(organization),
        members=[MemberOut(**vars(member)) for member in await organizations.members(context)],
        diagnostics=(
            [to_result_out(result) for result in await diagnostics.history(context)]
            if Permission.DIAGNOSTIC_READ in permissions
            else None
        ),
        passport=(
            [PassportItemOut.model_validate(vars(item)) for item in await passports.items(context)]
            if Permission.PASSPORT_READ in permissions
            else None
        ),
        entitlements=[EntitlementOut(**vars(item)) for item in await catalog.entitlements(context)],
    )


# ── Équipe BENILAB ──


class StaffMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    roles: list[StaffRole]
    last_login_at: datetime | None


class StaffList(BaseModel):
    data: list[StaffMemberOut]


class StaffGrant(BaseModel):
    email: EmailStr
    role: StaffRole


@router.get("/staff", response_model=StaffList)
async def list_staff(
    _: _staff(Permission.STAFF_MANAGE),  # type: ignore[valid-type]
    staff: Staff,
) -> StaffList:
    return StaffList(data=[StaffMemberOut(**asdict(member)) for member in await staff.list_staff()])


@router.post(
    "/staff",
    status_code=status.HTTP_201_CREATED,
    response_model=StaffMemberOut,
    dependencies=[Depends(require_csrf)],
)
async def grant_staff_role(
    body: StaffGrant,
    principal: _staff(Permission.STAFF_MANAGE),  # type: ignore[valid-type]
    staff: Staff,
) -> StaffMemberOut:
    member = await staff.grant(Actor.user(principal.user_id), email=body.email, role=body.role)
    return StaffMemberOut(**asdict(member))


@router.delete(
    "/staff/{user_id}/roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def revoke_staff_role(
    user_id: uuid.UUID,
    role: StaffRole,
    principal: _staff(Permission.STAFF_MANAGE),  # type: ignore[valid-type]
    staff: Staff,
) -> None:
    await staff.revoke(Actor.user(principal.user_id), user_id=user_id, role=role)


# ── Journal d'audit ──


class AuditLogOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_label: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    request_id: str | None
    ip: str | None


class AuditLogPage(BaseModel):
    data: list[AuditLogOut]
    page: PageInfo


@router.get("/audit-logs", response_model=AuditLogPage)
async def get_audit_logs(
    request: Request,
    _: _staff(Permission.AUDIT_READ),  # type: ignore[valid-type]
    params: Annotated[PageParams, Depends(page_params)],
    organization_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity_type: Annotated[str | None, Query(max_length=100)] = None,
) -> AuditLogPage:
    async with staff_transaction(request.app.state.session_factory) as session:
        entries = await list_audit_logs(
            session,
            limit=params.limit,
            after=params.after,
            organization_id=organization_id,
            action=action,
            entity_type=entity_type,
        )
    kept, page = build_page_info([entry.id for entry in entries], params)
    return AuditLogPage(
        data=[AuditLogOut.model_validate(entry, from_attributes=True) for entry in entries[:kept]],
        page=page,
    )
