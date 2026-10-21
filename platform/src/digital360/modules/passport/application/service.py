import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.ids import uuid7
from digital360.core.tenancy import TenantContext, tenant_transaction
from digital360.modules.passport.infrastructure.models import (
    DigitalPassport,
    PassportItem,
    PassportItemStatus,
    PassportSource,
)

# Ordre d'affichage et liste complète des éléments suivis
PASSPORT_ITEM_KEYS = (
    "WEBSITE",
    "DOMAIN",
    "HOSTING",
    "EMAIL",
    "GOOGLE_BUSINESS",
    "FACEBOOK",
    "INSTAGRAM",
    "TIKTOK",
    "LINKEDIN",
    "WHATSAPP",
    "SEO",
    "ADS",
    "EMAILING",
    "CRM",
    "CONVERSION",
    "ANALYTICS",
)


@dataclass(frozen=True)
class PassportItemView:
    key: str
    status: str
    source: str
    details: dict[str, Any] = field(default_factory=dict)
    status_changed_at: datetime | None = None


async def initialize_from_declarations(
    session: AsyncSession, organization_id: uuid.UUID, statuses: dict[str, str]
) -> None:
    """Crée le Passport d'après le diagnostic, sans écraser un élément vérifié ou synchronisé.

    S'exécute dans la transaction de l'appelant (contexte tenant de l'organisation).
    """
    passport_id = (
        await session.execute(
            insert(DigitalPassport)
            .values(id=uuid7(), organization_id=organization_id)
            .on_conflict_do_update(
                index_elements=["organization_id"], set_={"organization_id": organization_id}
            )
            .returning(DigitalPassport.id)
        )
    ).scalar_one()
    for key in PASSPORT_ITEM_KEYS:
        statement = insert(PassportItem).values(
            passport_id=passport_id,
            organization_id=organization_id,
            item_key=key,
            status=statuses.get(key, PassportItemStatus.NOT_CONFIGURED),
            source=PassportSource.DECLARED,
        )
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_passport_items_passport_item",
                set_={"status": statement.excluded.status},
                where=PassportItem.source == PassportSource.DECLARED,
            )
        )


class PassportService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def items(self, context: TenantContext) -> list[PassportItemView]:
        async with tenant_transaction(self._session_factory, context) as session:
            rows = (
                await session.execute(
                    select(PassportItem).where(
                        PassportItem.organization_id == context.organization_id
                    )
                )
            ).scalars()
            stored = {item.item_key: item for item in rows}
        # Organisation créée sans diagnostic : tous les éléments restent à configurer
        return [
            (
                PassportItemView(
                    key=key,
                    status=stored[key].status,
                    source=stored[key].source,
                    details=stored[key].details,
                    status_changed_at=stored[key].status_changed_at,
                )
                if key in stored
                else PassportItemView(
                    key=key,
                    status=PassportItemStatus.NOT_CONFIGURED,
                    source=PassportSource.DECLARED,
                )
            )
            for key in PASSPORT_ITEM_KEYS
        ]
