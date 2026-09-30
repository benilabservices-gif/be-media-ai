"""Catalogue public et droits (entitlements) des organisations.

Règle : le code ne teste jamais « est-ce un client Growth ? », il demande un droit
(`SOCIAL_MANAGEMENT`, `CONTENT_MONTHLY_LIMIT`…). Les offres peuvent évoluer sans code.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.errors import AppError
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.catalog.domain.models import (
    UNLIMITED,
    Catalog,
    Currency,
    EntitlementValue,
    Product,
    resolve_entitlements,
)
from digital360.modules.catalog.infrastructure.models import EntitlementOverride
from digital360.modules.configuration.application.store import load_published
from digital360.modules.configuration.infrastructure.models import ConfigKind


async def current_catalog(session: AsyncSession) -> Catalog:
    return await load_published(session, ConfigKind.CATALOG, Catalog)


async def current_catalog_or_none(session: AsyncSession) -> Catalog | None:
    """Catalogue publié, ou None s'il ne l'est pas encore (les prix restent alors vides)."""
    try:
        return await current_catalog(session)
    except AppError as exc:
        if exc.code == "CONFIG_NOT_PUBLISHED":
            return None
        raise


def price_payload(
    catalog: Catalog, product: Product | None, currency: Currency
) -> dict[str, Any] | None:
    price = catalog.price_for(product, currency) if product else None
    if price is None:
        return None
    return {"amount": price.amount, "currency": price.currency.value, "period": price.period.value}


@dataclass(frozen=True)
class CatalogView:
    catalog: Catalog
    currency: Currency
    available_currencies: list[Currency]
    products: list[Product]


@dataclass(frozen=True)
class EntitlementView:
    key: str
    type: str
    description: str
    value: EntitlementValue
    sources: list[dict[str, Any]] = field(default_factory=list)


def _overrides_active_now() -> Any:
    now = datetime.now(UTC)
    return (
        EntitlementOverride.revoked_at.is_(None),
        or_(EntitlementOverride.expires_at.is_(None), EntitlementOverride.expires_at > now),
    )


class CatalogService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def public_catalog(self, currency: Currency | None) -> CatalogView:
        async with self._session_factory() as session:
            catalog = await current_catalog(session)
        public = [product for product in catalog.products if product.public]
        available = [
            currency
            for currency in Currency
            if any(catalog.price_for(product, currency) for product in public)
        ]
        return CatalogView(
            catalog=catalog,
            currency=currency or catalog.default_currency,
            available_currencies=available,
            products=public,
        )

    async def entitlements(self, context: TenantContext) -> list[EntitlementView]:
        """Droits effectifs. Sources actuelles : droits accordés par l'équipe. Les commandes
        (M5) et abonnements (M7) s'ajouteront comme sources supplémentaires."""
        async with tenant_transaction(self._session_factory, context) as session:
            catalog = await current_catalog(session)
            overrides = list(
                (
                    await session.execute(
                        select(EntitlementOverride).where(
                            EntitlementOverride.organization_id == context.organization_id,
                            *_overrides_active_now(),
                        )
                    )
                ).scalars()
            )
        grants = [{override.entitlement_key: override.value} for override in overrides]
        resolved = resolve_entitlements(catalog, grants)
        return [
            EntitlementView(
                key=definition.key,
                type=definition.type,
                description=definition.description,
                value=resolved[definition.key],
                sources=[
                    {
                        "type": "OVERRIDE",
                        "reason": override.reason,
                        "expires_at": override.expires_at,
                    }
                    for override in overrides
                    if override.entitlement_key == definition.key
                ],
            )
            for definition in catalog.entitlements
        ]

    async def list_overrides(self, organization_id: uuid.UUID) -> list[EntitlementOverride]:
        async with staff_transaction(self._session_factory) as session:
            return list(
                (
                    await session.execute(
                        select(EntitlementOverride)
                        .where(EntitlementOverride.organization_id == organization_id)
                        .order_by(EntitlementOverride.id.desc())
                    )
                ).scalars()
            )

    async def grant_override(
        self,
        organization_id: uuid.UUID,
        actor: Actor,
        *,
        key: str,
        value: EntitlementValue,
        reason: str,
        expires_at: datetime | None,
    ) -> EntitlementOverride:
        async with staff_transaction(self._session_factory) as session:
            catalog = await current_catalog(session)
            definition = next((item for item in catalog.entitlements if item.key == key), None)
            if definition is None:
                raise AppError(
                    "VALIDATION_ERROR",
                    f"Droit inconnu : {key}.",
                    errors=[{"field": "body.entitlement_key", "reason": "unknown"}],
                )
            is_boolean = isinstance(value, bool)
            if (definition.type == "BOOLEAN") != is_boolean or (
                definition.type == "LIMIT" and not (value == UNLIMITED or isinstance(value, int))
            ):
                raise AppError(
                    "VALIDATION_ERROR",
                    f"{key} attend une valeur {definition.type}.",
                    errors=[{"field": "body.value", "reason": "wrong_type"}],
                )
            if expires_at is not None and expires_at <= datetime.now(UTC):
                raise AppError(
                    "VALIDATION_ERROR",
                    "La date d'expiration doit être dans le futur.",
                    errors=[{"field": "body.expires_at", "reason": "past"}],
                )
            override = EntitlementOverride(
                organization_id=organization_id,
                entitlement_key=key,
                value=value,
                reason=reason,
                granted_by=actor.user_id,
                expires_at=expires_at,
            )
            session.add(override)
            await session.flush()
            await record_audit(
                session,
                actor=actor,
                action="entitlement_override.grant",
                entity_type="entitlement_override",
                entity_id=override.id,
                organization_id=organization_id,
                new_value={"key": key, "value": value, "reason": reason},
            )
            await session.refresh(override)
            return override

    async def revoke_override(
        self, organization_id: uuid.UUID, override_id: uuid.UUID, actor: Actor
    ) -> None:
        async with staff_transaction(self._session_factory) as session:
            result = await session.execute(
                update(EntitlementOverride)
                .where(
                    EntitlementOverride.id == override_id,
                    EntitlementOverride.organization_id == organization_id,
                    EntitlementOverride.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
                .returning(EntitlementOverride.entitlement_key)
            )
            key = result.scalar_one_or_none()
            if key is None:
                raise AppError("NOT_FOUND", "Droit accordé introuvable.", status=404)
            await record_audit(
                session,
                actor=actor,
                action="entitlement_override.revoke",
                entity_type="entitlement_override",
                entity_id=override_id,
                organization_id=organization_id,
                old_value={"key": key},
            )
