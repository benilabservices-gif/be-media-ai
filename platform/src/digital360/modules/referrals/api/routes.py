import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, StringConstraints

from digital360.core.csrf import require_csrf
from digital360.core.permissions import Permission, Principal
from digital360.modules.identity.api.dependencies import CurrentPrincipal, require_staff_permission
from digital360.modules.referrals.application.service import CloserService, CloserSpace
from digital360.modules.referrals.infrastructure.models import (
    CloserStatus,
    PayoutMethod,
    StatementStatus,
)

router = APIRouter(tags=["closers"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_closer_service(request: Request) -> CloserService:
    service: CloserService = request.app.state.closer_service
    return service


Service = Annotated[CloserService, Depends(get_closer_service)]
StaffRead = Annotated[Principal, Depends(require_staff_permission(Permission.INVOICE_READ))]
StaffManage = Annotated[Principal, Depends(require_staff_permission(Permission.CLOSER_MANAGE))]

# Numéro mobile money (+229…) ou coordonnées bancaires
PayoutAccount = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=6, max_length=64)
]
CloserCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z0-9]{4,16}$")
]


class Amount(BaseModel):
    # Unité mineure (FCFA ; centimes pour l'euro)
    amount: int
    currency: str


class CloserOut(BaseModel):
    id: uuid.UUID
    code: str
    referral_link: str
    status: CloserStatus
    payout_method: PayoutMethod
    payout_account: str
    created_at: datetime


class ReferralOut(BaseModel):
    organization_name: str
    joined_at: datetime
    # Total des commissions que ce client a rapportées
    commissions_total: int


class PendingOut(Amount):
    count: int


class StatementOut(BaseModel):
    id: uuid.UUID
    # Premier jour du mois couvert
    period: date
    currency: str
    total_amount: int
    commission_count: int
    status: StatementStatus
    payout_method: PayoutMethod | None
    payout_reference: str | None
    paid_on: date | None


class CloserSpaceOut(BaseModel):
    # Peut devenir closer (client ayant déjà payé) ; toujours vrai une fois closer
    eligible: bool
    closer: CloserOut | None
    referrals: list[ReferralOut]
    # Gagné ce mois-ci, pas encore relevé
    pending: list[PendingOut]
    statements: list[StatementOut]


class JoinIn(BaseModel):
    payout_method: PayoutMethod
    payout_account: PayoutAccount
    # Conditions du programme : 20 % pendant 12 mois, versement mensuel, pas d'auto-parrainage
    accept_terms: Literal[True]


class PayoutIn(BaseModel):
    payout_method: PayoutMethod
    payout_account: PayoutAccount


def _space_out(space: CloserSpace) -> CloserSpaceOut:
    return CloserSpaceOut.model_validate(space, from_attributes=True)


@router.get("/me/closer", response_model=CloserSpaceOut)
async def get_my_closer_space(principal: CurrentPrincipal, service: Service) -> CloserSpaceOut:
    """Espace Closer 3.0 : code, lien, clients apportés, commissions et relevés."""
    return _space_out(await service.space(principal.user_id))


@router.post("/me/closer", response_model=CloserSpaceOut, dependencies=[Depends(require_csrf)])
async def join_closer_program(
    body: JoinIn, principal: CurrentPrincipal, service: Service
) -> CloserSpaceOut:
    """Devenir Closer 3.0 (idempotent). 403 NOT_ELIGIBLE sans paiement préalable."""
    space = await service.join(
        principal.user_id, payout_method=body.payout_method, payout_account=body.payout_account
    )
    return _space_out(space)


@router.patch("/me/closer", response_model=CloserSpaceOut, dependencies=[Depends(require_csrf)])
async def update_closer_payout(
    body: PayoutIn, principal: CurrentPrincipal, service: Service
) -> CloserSpaceOut:
    space = await service.update_payout(
        principal.user_id, payout_method=body.payout_method, payout_account=body.payout_account
    )
    return _space_out(space)


# ── Équipe ──


class AdminCloserOut(CloserOut):
    full_name: str
    email: str
    referred_clients: int
    paying_clients: int
    earned: list[Amount]
    due: list[Amount]
    paid: list[Amount]


class AdminCloserList(BaseModel):
    data: list[AdminCloserOut]
    # Total à verser (relevés DUE), par devise
    due_total: list[Amount]


class AdminStatementOut(StatementOut):
    closer_id: uuid.UUID
    closer_name: str
    closer_email: str
    closer_code: str
    closer_payout_method: PayoutMethod
    closer_payout_account: str


class AdminStatementList(BaseModel):
    data: list[AdminStatementOut]


class PayStatementIn(BaseModel):
    method: PayoutMethod
    reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    paid_on: date | None = None


class CloserStatusIn(BaseModel):
    status: CloserStatus


class ReferralIn(BaseModel):
    # null : détache le client de son closer
    closer_code: CloserCode | None


class ReferralOutAdmin(BaseModel):
    closer_code: str | None


@admin_router.get("/closers", response_model=AdminCloserList)
async def admin_list_closers(_: StaffRead, service: Service) -> AdminCloserList:
    return AdminCloserList.model_validate(
        {"data": await service.admin_list(), "due_total": await service.due_total()}
    )


@admin_router.get("/commission-statements", response_model=AdminStatementList)
async def admin_list_statements(
    _: StaffRead, service: Service, status: StatementStatus | None = None
) -> AdminStatementList:
    """Relevés mensuels des closers, du plus récent au plus ancien (filtre `status=DUE`)."""
    return AdminStatementList.model_validate(
        {"data": await service.admin_statements(status=status)}
    )


@admin_router.post(
    "/commission-statements/{statement_id}/pay",
    response_model=StatementOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_pay_statement(
    statement_id: uuid.UUID, body: PayStatementIn, principal: StaffManage, service: Service
) -> StatementOut:
    """Marque un relevé comme versé (le closer est prévenu par e-mail). 409 si déjà versé."""
    paid = await service.admin_pay(
        statement_id,
        principal.user_id,
        method=body.method,
        reference=body.reference,
        paid_on=body.paid_on or date.today(),
    )
    return StatementOut.model_validate(paid)


@admin_router.patch("/closers/{closer_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def admin_set_closer_status(
    closer_id: uuid.UUID, body: CloserStatusIn, principal: StaffManage, service: Service
) -> None:
    await service.admin_set_status(closer_id, principal.user_id, body.status)


@admin_router.put(
    "/organizations/{organization_id}/closer",
    response_model=ReferralOutAdmin,
    dependencies=[Depends(require_csrf)],
)
async def admin_set_referral(
    organization_id: uuid.UUID, body: ReferralIn, principal: StaffManage, service: Service
) -> ReferralOutAdmin:
    """Rattache un client à un closer (ou le détache). Vaut pour les paiements à venir."""
    code = await service.admin_attach(organization_id, principal.user_id, body.closer_code)
    return ReferralOutAdmin(closer_code=code)
