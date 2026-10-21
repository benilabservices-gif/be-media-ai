import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, HttpUrl, StringConstraints

from digital360.core.csrf import require_csrf
from digital360.core.permissions import Permission, Principal
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.performance.application.service import PerformanceService
from digital360.modules.performance.domain.metrics import MAX_TOP_SEARCHES, Channel, SetupStatus

router = APIRouter(tags=["performance"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_performance_service(request: Request) -> PerformanceService:
    service: PerformanceService = request.app.state.performance_service
    return service


Service = Annotated[PerformanceService, Depends(get_performance_service)]
StaffReader = Annotated[Principal, Depends(require_staff_permission(Permission.ORGANIZATION_READ))]
# Saisie des chiffres : l'équipe de production (ADMIN, MANAGER, CONTENT_MANAGER, DEVELOPER)
StaffWriter = Annotated[Principal, Depends(require_staff_permission(Permission.TASK_MANAGE))]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class StepOut(BaseModel):
    key: str
    label: str
    done: bool


class GoogleSetupOut(BaseModel):
    status: SetupStatus
    steps: list[StepOut]
    profile_url: str | None
    note: str | None
    updated_at: datetime | None


class DefinitionOut(BaseModel):
    key: str
    label: str
    # count : nombre entier ; rating : note de 1 à 5
    kind: str


class ChangeOut(BaseModel):
    # Évolution en % (nombre) ou en points (note) par rapport au mois précédent
    percent: int | None = None
    points: float | None = None
    # True : bonne nouvelle, False : à surveiller, None : stable
    good: bool | None


class ReportOut(BaseModel):
    # Premier jour du mois couvert
    period: date
    metrics: dict[str, int | float]
    top_searches: list[str]
    note: str | None
    changes: dict[str, ChangeOut]
    updated_at: datetime


class ChannelOut(BaseModel):
    definitions: list[DefinitionOut]
    # Du plus récent au plus ancien
    reports: list[ReportOut]


class PerformanceOut(BaseModel):
    google_setup: GoogleSetupOut
    manager_email: str
    channels: dict[Channel, ChannelOut]


class GoogleSetupIn(BaseModel):
    status: SetupStatus
    profile_url: HttpUrl | None = None
    note: Note | None = None


class ReportIn(BaseModel):
    # Clés des indicateurs du canal ; null ou absent = non mesuré ce mois-ci
    metrics: dict[str, Any]
    top_searches: Annotated[
        list[Annotated[str, StringConstraints(max_length=100)]], Field(max_length=MAX_TOP_SEARCHES)
    ] = Field(default_factory=list)
    note: Note | None = None


def _out(data: dict[str, Any]) -> PerformanceOut:
    return PerformanceOut.model_validate(data)


@router.get("/orgs/{org_id}/performance", response_model=PerformanceOut)
async def get_performance(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
) -> PerformanceOut:
    """Mise en place de Google Business et rapports mensuels (Google, site web) du projet."""
    return _out(await service.overview(access.tenant))


@admin_router.get("/organizations/{organization_id}/performance", response_model=PerformanceOut)
async def admin_get_performance(
    organization_id: uuid.UUID, _: StaffReader, service: Service
) -> PerformanceOut:
    return _out(await service.admin_overview(organization_id))


@admin_router.put(
    "/organizations/{organization_id}/google-setup",
    response_model=GoogleSetupOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_set_google_setup(
    organization_id: uuid.UUID, body: GoogleSetupIn, principal: StaffWriter, service: Service
) -> GoogleSetupOut:
    view = await service.set_google_setup(
        organization_id,
        principal.user_id,
        status=body.status,
        profile_url=str(body.profile_url) if body.profile_url else None,
        note=body.note or None,
    )
    return GoogleSetupOut.model_validate(view)


@admin_router.put(
    "/organizations/{organization_id}/reports/{channel}/{period}",
    response_model=PerformanceOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_save_report(
    organization_id: uuid.UUID,
    channel: Channel,
    period: date,
    body: ReportIn,
    principal: StaffWriter,
    service: Service,
) -> PerformanceOut:
    """Crée ou corrige le rapport d'un mois (`period` : n'importe quel jour du mois)."""
    data = await service.save_report(
        organization_id,
        principal.user_id,
        channel=channel,
        period=period,
        metrics=body.metrics,
        top_searches=body.top_searches,
        note=body.note or None,
    )
    return _out(data)


@admin_router.delete(
    "/organizations/{organization_id}/reports/{channel}/{period}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def admin_delete_report(
    organization_id: uuid.UUID,
    channel: Channel,
    period: date,
    principal: StaffWriter,
    service: Service,
) -> None:
    await service.delete_report(organization_id, principal.user_id, channel=channel, period=period)
