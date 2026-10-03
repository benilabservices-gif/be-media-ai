import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from digital360.core.csrf import require_csrf
from digital360.core.pagination import PageInfo, PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.modules.billing.application.service import (
    PaymentInput,
    PurchaseRequestService,
    PurchaseRequestView,
)
from digital360.modules.billing.infrastructure.models import (
    ContactChannel,
    PaymentMethod,
    PurchaseRequestStatus,
)
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.identity.api.schemas import PhoneNumber

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
    # Numéro à rappeler (E.164) ; facultatif si la fiche entreprise ou le compte en a déjà un
    contact_number: PhoneNumber | None = None
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
    contact_number: str | None
    message: str | None
    status: PurchaseRequestStatus
    created_at: datetime
    updated_at: datetime


class PaymentOut(BaseModel):
    amount: int
    currency: str
    method: PaymentMethod
    channel: str
    reference: str | None
    received_on: date


class StaffPurchaseRequestOut(PurchaseRequestOut):
    payment: PaymentOut | None
    staff_note: str | None
    requested_by_name: str
    requested_by_email: str
    requested_by_phone: str | None


class PurchaseRequestList(BaseModel):
    data: list[PurchaseRequestOut]


class StaffPurchaseRequestPage(BaseModel):
    data: list[StaffPurchaseRequestOut]
    page: PageInfo


class PaymentIn(BaseModel):
    """Encaissement reçu hors plateforme (mobile money, virement, espèces)."""

    method: PaymentMethod
    # Montant réellement reçu, HT, en unité mineure (FCFA ; centimes pour l'euro).
    # Facultatif : sans montant, c'est le prix de l'offre qui est enregistré.
    amount: Annotated[int, Field(ge=0)] | None = None
    # Identifiant de la transaction : obligatoire sauf pour les espèces
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = (
        None
    )
    received_on: date | None = None

    @model_validator(mode="after")
    def _reference_unless_cash(self) -> "PaymentIn":
        if self.method is not PaymentMethod.CASH and not self.reference:
            raise ValueError("référence de la transaction obligatoire (sauf espèces)")
        return self

    def to_input(self) -> PaymentInput:
        return PaymentInput(
            method=self.method,
            amount=self.amount,
            reference=self.reference or None,
            received_on=self.received_on or date.today(),
        )


class PurchaseRequestUpdate(BaseModel):
    status: PurchaseRequestStatus | None = None
    # Obligatoire pour passer à WON : le paiement est enregistré et un reçu part au client
    payment: PaymentIn | None = None
    # Chaîne vide pour effacer la note
    staff_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = (
        None
    )


class DirectSaleIn(BaseModel):
    product_code: ProductCode
    payment: PaymentIn
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


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
        contact_number=body.contact_number,
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
        principal.user_id,
        status=body.status,
        staff_note=body.staff_note,
        payment=body.payment.to_input() if body.payment else None,
    )
    return _staff_out(view)


@admin_router.post(
    "/organizations/{organization_id}/sales",
    status_code=status.HTTP_201_CREATED,
    response_model=StaffPurchaseRequestOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_direct_sale(
    organization_id: uuid.UUID, body: DirectSaleIn, principal: StaffSales, service: Service
) -> StaffPurchaseRequestOut:
    """Vente encaissée hors plateforme pour un client qui n'a pas fait de demande."""
    view = await service.admin_direct_sale(
        organization_id,
        principal.user_id,
        product_code=body.product_code,
        payment=body.payment.to_input(),
        note=body.note or None,
    )
    return _staff_out(view)
