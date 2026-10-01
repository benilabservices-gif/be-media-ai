import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, StringConstraints

from digital360.core.actor import Actor
from digital360.core.csrf import require_csrf
from digital360.core.pagination import PageInfo, PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.modules.billing.application.service import (
    PurchaseRequestService,
    PurchaseRequestView,
)
from digital360.modules.billing.infrastructure.models import ContactChannel, PurchaseRequestStatus
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)

router = APIRouter(tags=["billing"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_purchase_request_service(request: Request) -> PurchaseRequestService:
    service: PurchaseRequestService = request.app.state.purchase_request_service
    return service


Service = Annotated[PurchaseRequestService, Depends(get_purchase_request_service)]
StaffSales = Annotated[Principal, Depends(require_staff_permission(Permission.ORDER_CREATE))]

ProductCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=50)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class PurchaseRequestCreate(BaseModel):
    product_code: ProductCode
    # Recommandation du plan d'action à l'origine de la demande (elle passe à ACCEPTED)
    plan_item_id: uuid.UUID | None = None
    channel: ContactChannel
    message: Message | None = None


class PriceOut(BaseModel):
    # Unité mineure, HT (comme le catalogue)
    amount: int
    currency: str
    period: str | None


class PurchaseRequestOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    product_code: str
    product_name: str
    plan_item_id: uuid.UUID | None
    price: PriceOut | None
    channel: ContactChannel
    message: str | None
    status: PurchaseRequestStatus
    created_at: datetime
    updated_at: datetime


class StaffPurchaseRequestOut(PurchaseRequestOut):
    staff_note: str | None
    requested_by_name: str
    requested_by_email: str
    requested_by_phone: str | None


class PurchaseRequestList(BaseModel):
    data: list[PurchaseRequestOut]


class StaffPurchaseRequestPage(BaseModel):
    data: list[StaffPurchaseRequestOut]
    page: PageInfo


class PurchaseRequestUpdate(BaseModel):
    status: PurchaseRequestStatus | None = None
    # Chaîne vide pour effacer la note
    staff_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = (
        None
    )


def _client_out(view: PurchaseRequestView) -> PurchaseRequestOut:
    return PurchaseRequestOut.model_validate(view, from_attributes=True)


def _staff_out(view: PurchaseRequestView) -> StaffPurchaseRequestOut:
    return StaffPurchaseRequestOut.model_validate(view, from_attributes=True)


@router.post(
    "/orgs/{org_id}/purchase-requests",
    status_code=status.HTTP_201_CREATED,
    response_model=PurchaseRequestOut,
    dependencies=[Depends(require_csrf)],
)
async def create_purchase_request(
    body: PurchaseRequestCreate,
    response: Response,
    access: Annotated[OrganizationAccess, Depends(require_org_permission(Permission.ORDER_CREATE))],
    service: Service,
) -> PurchaseRequestOut:
    """« Je veux démarrer ». Une demande déjà en cours pour la même offre est renvoyée (200)."""
    view, created = await service.create(
        access.tenant,
        access.principal.user_id,
        product_code=body.product_code,
        plan_item_id=body.plan_item_id,
        channel=body.channel,
        message=body.message or None,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return _client_out(view)


@router.get("/orgs/{org_id}/purchase-requests", response_model=PurchaseRequestList)
async def list_purchase_requests(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
) -> PurchaseRequestList:
    views = await service.list_for_organization(access.tenant)
    return PurchaseRequestList(data=[_client_out(view) for view in views])


@admin_router.get("/purchase-requests", response_model=StaffPurchaseRequestPage)
async def admin_list_purchase_requests(
    _: StaffSales,
    service: Service,
    params: Annotated[PageParams, Depends(page_params)],
    status: PurchaseRequestStatus | None = None,
) -> StaffPurchaseRequestPage:
    views, page = await service.admin_list(params, status=status)
    return StaffPurchaseRequestPage(data=[_staff_out(view) for view in views], page=page)


@admin_router.patch(
    "/purchase-requests/{request_id}",
    response_model=StaffPurchaseRequestOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_update_purchase_request(
    request_id: uuid.UUID, body: PurchaseRequestUpdate, principal: StaffSales, service: Service
) -> StaffPurchaseRequestOut:
    view = await service.admin_update(
        request_id,
        Actor.user(principal.user_id),
        status=body.status,
        staff_note=body.staff_note,
    )
    return _staff_out(view)
