import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from digital360.core.csrf import require_csrf
from digital360.core.pagination import PageInfo, PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.modules.billing.application.online_payments import (
    CheckoutView,
    OnlinePaymentService,
)
from digital360.modules.billing.application.renewals import RenewalService
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
from digital360.modules.catalog.api.routes import CatalogOut, get_catalog_service, to_catalog_out
from digital360.modules.catalog.application.service import CatalogService
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.identity.api.schemas import PhoneNumber

router = APIRouter(tags=["billing"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_renewal_service(request: Request) -> RenewalService:
    service: RenewalService = request.app.state.renewal_service
    return service


def get_online_payment_service(request: Request) -> OnlinePaymentService:
    service: OnlinePaymentService = request.app.state.online_payment_service
    return service


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
        if self.method is PaymentMethod.CARTFLOX:
            raise ValueError("un paiement Cartflox est enregistré automatiquement")
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


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID
    organization_name: str
    product_code: str
    product_name: str
    # Fin de la période payée ; au-delà, les droits de l'offre s'arrêtent d'eux-mêmes
    covers_until: datetime
    status: Literal["ACTIVE", "EXPIRING", "EXPIRED"]
    days_left: int
    last_payment: dict[str, Any]


class SubscriptionList(BaseModel):
    data: list[SubscriptionOut]


class RenewalIn(BaseModel):
    product_code: ProductCode
    payment: PaymentIn


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


@router.get("/orgs/{org_id}/catalog", response_model=CatalogOut)
async def get_organization_catalog(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> CatalogOut:
    """Offres aux prix que l'entreprise paiera : devise imposée, pas de paramètre `currency`.

    Prix Europe dès qu'un numéro de l'entreprise est hors d'Afrique (catalogue v4).
    """
    currency = await service.billing_currency(access.tenant)
    return to_catalog_out(await catalog.public_catalog(currency))


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


Renewals = Annotated[RenewalService, Depends(get_renewal_service)]


@admin_router.get("/subscriptions", response_model=SubscriptionList)
async def admin_list_subscriptions(
    _: StaffSales,
    service: Renewals,
    status: Literal["ACTIVE", "EXPIRING", "EXPIRED"] | None = None,
) -> SubscriptionList:
    """Abonnements payés hors ligne, de l'échéance la plus proche à la plus lointaine."""
    views = await service.admin_list(status=status)
    return SubscriptionList(data=[SubscriptionOut.model_validate(view) for view in views])


@admin_router.post(
    "/organizations/{organization_id}/renewals",
    status_code=status.HTTP_201_CREATED,
    response_model=SubscriptionOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_renew_subscription(
    organization_id: uuid.UUID, body: RenewalIn, principal: StaffSales, service: Renewals
) -> SubscriptionOut:
    """Encaissement d'un renouvellement : la période repart de l'échéance (ou d'aujourd'hui)."""
    view = await service.renew(
        organization_id,
        principal.user_id,
        product_code=body.product_code,
        payment=body.payment.to_input(),
    )
    return SubscriptionOut.model_validate(view)


OnlinePayments = Annotated[OnlinePaymentService, Depends(get_online_payment_service)]


class CheckoutCreate(BaseModel):
    product_code: ProductCode


class CheckoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_code: str
    product_name: str
    # Prix HT figé au lancement, en unité mineure
    amount: int
    currency: str
    period: str | None
    status: Literal["PENDING", "PAID", "FAILED", "CANCELLED", "EXPIRED"]
    # Page de paiement Cartflox vers laquelle rediriger le client (paiement en attente)
    checkout_url: str | None
    # Numéro du reçu, une fois payé
    receipt_number: str | None
    created_at: datetime


def _checkout_out(view: CheckoutView) -> CheckoutOut:
    return CheckoutOut.model_validate(view)


@router.post(
    "/orgs/{org_id}/checkouts",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckoutOut,
    dependencies=[Depends(require_csrf)],
)
async def start_checkout(
    body: CheckoutCreate,
    access: Annotated[OrganizationAccess, Depends(require_org_permission(Permission.ORDER_CREATE))],
    service: OnlinePayments,
) -> CheckoutOut:
    """Lance un paiement en ligne (achat ou renouvellement) : rediriger vers `checkout_url`."""
    view = await service.start(
        access.tenant, access.principal.user_id, product_code=body.product_code
    )
    return _checkout_out(view)


@router.get("/orgs/{org_id}/checkouts/{checkout_id}", response_model=CheckoutOut)
async def get_checkout(
    checkout_id: uuid.UUID,
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: OnlinePayments,
) -> CheckoutOut:
    """Retour de la page de paiement : vérifie auprès de Cartflox et active l'offre si payé."""
    return _checkout_out(await service.refresh(access.tenant, checkout_id))


class ClientPaymentOut(BaseModel):
    receipt_number: str
    product_code: str
    product_name: str
    amount: int
    currency: str
    method: PaymentMethod
    reference: str | None
    received_on: date
    covers_until: datetime | None


class ClientBillingOut(BaseModel):
    subscriptions: list[SubscriptionOut]
    payments: list[ClientPaymentOut]
    # Bouton « Payer en ligne » à proposer (Cartflox configuré, client en FCFA XOF)
    online_payment_available: bool


@router.get("/orgs/{org_id}/billing", response_model=ClientBillingOut)
async def get_client_billing(
    access: Annotated[OrganizationAccess, Depends(require_org_permission(Permission.INVOICE_READ))],
    service: Renewals,
    online: OnlinePayments,
) -> ClientBillingOut:
    """Abonnements en cours (échéance) et historique des paiements, avec leur numéro de reçu."""
    billing = await service.client_billing(access.tenant)
    return ClientBillingOut(
        subscriptions=[SubscriptionOut.model_validate(view) for view in billing.subscriptions],
        payments=[ClientPaymentOut(**payment) for payment in billing.payments],
        online_payment_available=await online.is_available(access.tenant),
    )


class VoidDuplicateIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


@admin_router.post(
    "/purchase-requests/{request_id}/void-duplicate",
    response_model=StaffPurchaseRequestOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_void_duplicate_payment(
    request_id: uuid.UUID,
    body: VoidDuplicateIn,
    principal: Annotated[Principal, Depends(require_staff_permission(Permission.PAYMENT_REFUND))],
    service: Service,
) -> StaffPurchaseRequestOut:
    """Annule un paiement en double (ADMIN, FINANCE). 409 NOT_A_DUPLICATE s'il est le seul."""
    view = await service.admin_void_duplicate(request_id, principal.user_id, reason=body.reason)
    return _staff_out(view)
