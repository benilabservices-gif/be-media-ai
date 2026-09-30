"""Publication et lecture des documents de configuration versionnés."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.errors import AppError
from digital360.modules.configuration.infrastructure.models import ConfigDocument, ConfigKind

DEFAULT_KEY = "default"

# Les documents publiés sont immuables : un cache par identifiant est toujours juste
_cache: dict[uuid.UUID, BaseModel] = {}


@dataclass(frozen=True)
class PublishResult:
    kind: ConfigKind
    version: int
    created: bool


def _checksum(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def publish(session: AsyncSession, kind: ConfigKind, definition: BaseModel) -> PublishResult:
    """Publie une version ; idempotent si le même contenu est déjà publié sous ce numéro."""
    payload = definition.model_dump(mode="json")
    key, version, checksum = payload["key"], payload["version"], _checksum(payload)
    existing = (
        await session.execute(
            select(ConfigDocument).where(
                ConfigDocument.kind == kind,
                ConfigDocument.key == key,
                ConfigDocument.version == version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.checksum != checksum:
            raise AppError(
                "CONFLICT",
                f"{kind} {key} v{version} est déjà publié avec un contenu différent : "
                "incrémentez `version` dans le fichier.",
                status=409,
            )
        return PublishResult(kind, version, created=False)

    await session.execute(
        update(ConfigDocument)
        .where(
            ConfigDocument.kind == kind,
            ConfigDocument.key == key,
            ConfigDocument.status == "PUBLISHED",
        )
        .values(status="ARCHIVED")
    )
    session.add(
        ConfigDocument(
            kind=kind,
            key=key,
            version=version,
            status="PUBLISHED",
            definition=payload,
            checksum=checksum,
        )
    )
    await session.flush()
    return PublishResult(kind, version, created=True)


async def published_id(session: AsyncSession, kind: ConfigKind) -> uuid.UUID:
    document_id = (
        await session.execute(
            select(ConfigDocument.id).where(
                ConfigDocument.kind == kind,
                ConfigDocument.key == DEFAULT_KEY,
                ConfigDocument.status == "PUBLISHED",
            )
        )
    ).scalar_one_or_none()
    if document_id is None:
        raise AppError(
            "CONFIG_NOT_PUBLISHED",
            "Ce service est momentanément indisponible.",
            status=503,
        )
    return document_id


async def load[M: BaseModel](session: AsyncSession, document_id: uuid.UUID, model: type[M]) -> M:
    cached = _cache.get(document_id)
    if cached is None:
        definition = (
            await session.execute(
                select(ConfigDocument.definition).where(ConfigDocument.id == document_id)
            )
        ).scalar_one()
        cached = _cache[document_id] = model.model_validate(definition)
    if not isinstance(cached, model):
        raise TypeError(f"le document {document_id} n'est pas un {model.__name__}")
    return cached


async def load_published[M: BaseModel](
    session: AsyncSession, kind: ConfigKind, model: type[M]
) -> M:
    return await load(session, await published_id(session, kind), model)
