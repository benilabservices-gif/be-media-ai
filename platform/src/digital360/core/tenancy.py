"""Isolation des données par organisation (ARCHITECTURE.md §4).

Deux barrières :
1. applicative : les services reçoivent un TenantContext et filtrent par organization_id ;
2. PostgreSQL : Row Level Security forcée sur chaque table tenant. La transaction porte
   la variable `app.current_org_id` (ou `app.scope = 'staff'`), et `app.current_user_id`
   pour les données propres à l'utilisateur (ses appartenances), posées par les fonctions
   ci-dessous avec `set_config(..., true)`, donc limitée à la transaction : une connexion
   rendue au pool ne garde aucun contexte.

Sans contexte posé, une table tenant ne renvoie aucune ligne (fermeture par défaut).
L'application doit se connecter avec un rôle NON superuser et sans BYPASSRLS, sinon
PostgreSQL ignore les politiques (vérifié par /health/ready).
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    # Utilisateur à l'origine de la requête (None pour un job système)
    user_id: UUID | None = None


async def set_tenant_scope(session: AsyncSession, organization_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(organization_id)},
    )


async def set_user_scope(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


async def set_staff_scope(session: AsyncSession) -> None:
    """Accès multi-organisations : routes /admin et jobs système uniquement, toujours audités."""
    await session.execute(text("SELECT set_config('app.scope', 'staff', true)"))


@asynccontextmanager
async def tenant_transaction(
    session_factory: async_sessionmaker[AsyncSession], context: TenantContext
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session, session.begin():
        await set_tenant_scope(session, context.organization_id)
        if context.user_id is not None:
            await set_user_scope(session, context.user_id)
        yield session


@asynccontextmanager
async def user_transaction(
    session_factory: async_sessionmaker[AsyncSession], user_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Transaction hors organisation : l'utilisateur ne voit que ses propres appartenances."""
    async with session_factory() as session, session.begin():
        await set_user_scope(session, user_id)
        yield session


@asynccontextmanager
async def staff_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session, session.begin():
        await set_staff_scope(session)
        yield session


def tenant_rls_statements(table: str) -> list[str]:
    """SQL d'activation de la RLS pour une table tenant, à appeler depuis une migration.

    Ne jamais modifier le SQL produit : les migrations passées l'utilisent. Une nouvelle
    politique passe par une nouvelle fonction et une nouvelle migration.
    """
    if not _IDENTIFIER.match(table):
        raise ValueError(f"nom de table invalide : {table!r}")
    predicate = "app_is_staff() OR organization_id = app_current_org_id()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        # FORCE : la politique s'applique aussi au propriétaire de la table (le rôle applicatif)
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})",
    ]
