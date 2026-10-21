from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from digital360.core.permissions import Permission
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
)
from digital360.modules.passport.application.service import PassportService

router = APIRouter(tags=["passport"])


class PassportItemOut(BaseModel):
    key: str
    status: str
    # DECLARED (déclaré au diagnostic), VERIFIED (vérifié par BENILAB), SYNCED (intégration)
    source: str
    details: dict[str, Any]
    status_changed_at: datetime | None


class PassportOut(BaseModel):
    items: list[PassportItemOut]


def get_passport_service(request: Request) -> PassportService:
    service: PassportService = request.app.state.passport_service
    return service


@router.get("/orgs/{org_id}/passport", response_model=PassportOut)
async def get_passport(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.PASSPORT_READ))
    ],
    service: Annotated[PassportService, Depends(get_passport_service)],
) -> PassportOut:
    items = await service.items(access.tenant)
    return PassportOut(items=[PassportItemOut.model_validate(vars(item)) for item in items])
