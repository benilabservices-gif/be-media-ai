import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, StringConstraints

from digital360.core.actor import Actor
from digital360.core.csrf import require_csrf
from digital360.core.permissions import Permission, Principal
from digital360.modules.catalog.application.service import (
    CatalogService,
    CatalogView,
    price_payload,
)
from digital360.modules.catalog.domain.models import Currency, EntitlementValue
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)

public_router = APIRouter(prefix="/public", tags=["catalog"])
router = APIRouter(tags=["catalog"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_catalog_service(request: Request) -> CatalogService:
    service: CatalogService = request.app.state.catalog_service
    return service


Service = Annotated[CatalogService, Depends(get_catalog_service)]


# ── Schémas (format du mock getCatalog de Kilo) ──


class PriceOut(BaseModel):
    # Unité mineure : FCFA pour XOF/XAF, centimes pour EUR. Toujours HT.
    amount: int
    currency: str
    period: str


class ProductOut(BaseModel):
    code: str
    name: str
    kind: str
    module: str
    family: str | None
    description: str
    includes: list[str]
    # null si l'offre n'est pas vendue dans la devise demandée
    price: PriceOut | None
    available: bool


class CatalogOut(BaseModel):
    currency: str
    available_currencies: list[str]
    products: list[ProductOut]


class EntitlementOut(BaseModel):
    key: str
    type: str
    description: str
    value: EntitlementValue
    sources: list[dict[str, Any]]


class EntitlementList(BaseModel):
    entitlements: list[EntitlementOut]


class OverrideCreate(BaseModel):
    entitlement_key: Annotated[str, StringConstraints(max_length=64)]
    value: EntitlementValue
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    expires_at: datetime | None = None


class OverrideOut(BaseModel):
    id: uuid.UUID
    entitlement_key: str
    value: EntitlementValue
    reason: str
    granted_by: uuid.UUID
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class OverrideList(BaseModel):
    data: list[OverrideOut]


# ── Routes ──


@public_router.get("/catalog", response_model=CatalogOut)
async def get_catalog(
    service: Service, currency: Annotated[Currency | None, Query()] = None
) -> CatalogOut:
    """Vitrine publique : la devise est libre ; la facturation, elle, l'impose (/orgs/…/catalog)."""
    return to_catalog_out(await service.public_catalog(currency))


def to_catalog_out(view: CatalogView) -> CatalogOut:
    return CatalogOut(
        currency=view.currency.value,
        available_currencies=[item.value for item in view.available_currencies],
        products=[
            ProductOut(
                code=product.code,
                name=product.name,
                kind=product.kind.value,
                module=product.module,
                family=product.family,
                description=product.description,
                includes=list(product.includes),
                price=price_payload(view.catalog, product, view.currency),
                available=view.catalog.price_for(product, view.currency) is not None,
            )
            for product in view.products
        ],
    )


@router.get("/orgs/{org_id}/entitlements", response_model=EntitlementList)
async def get_entitlements(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
) -> EntitlementList:
    views = await service.entitlements(access.tenant)
    return EntitlementList(entitlements=[EntitlementOut(**vars(view)) for view in views])


StaffManager = Annotated[
    Principal, Depends(require_staff_permission(Permission.SUBSCRIPTION_MANAGE))
]


@admin_router.get(
    "/organizations/{organization_id}/entitlement-overrides", response_model=OverrideList
)
async def list_overrides(
    organization_id: uuid.UUID, _: StaffManager, service: Service
) -> OverrideList:
    overrides = await service.list_overrides(organization_id)
    return OverrideList(
        data=[OverrideOut.model_validate(item, from_attributes=True) for item in overrides]
    )


@admin_router.post(
    "/organizations/{organization_id}/entitlement-overrides",
    status_code=status.HTTP_201_CREATED,
    response_model=OverrideOut,
    dependencies=[Depends(require_csrf)],
)
async def grant_override(
    organization_id: uuid.UUID, body: OverrideCreate, principal: StaffManager, service: Service
) -> OverrideOut:
    override = await service.grant_override(
        organization_id,
        Actor.user(principal.user_id),
        key=body.entitlement_key,
        value=body.value,
        reason=body.reason,
        expires_at=body.expires_at,
    )
    return OverrideOut.model_validate(override, from_attributes=True)


@admin_router.delete(
    "/organizations/{organization_id}/entitlement-overrides/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def revoke_override(
    organization_id: uuid.UUID,
    override_id: uuid.UUID,
    principal: StaffManager,
    service: Service,
) -> None:
    await service.revoke_override(organization_id, override_id, Actor.user(principal.user_id))
