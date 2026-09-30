"""Configuration métier versionnée : publication (depuis les fichiers YAML) et lecture."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.errors import AppError
from digital360.modules.diagnostics.domain.config import (
    QuestionnaireDefinition,
    RuleSet,
    ScoringModel,
)
from digital360.modules.diagnostics.infrastructure.models import ConfigDocument, ConfigKind

MODEL_FOR_KIND: dict[ConfigKind, type[BaseModel]] = {
    ConfigKind.QUESTIONNAIRE: QuestionnaireDefinition,
    ConfigKind.SCORING_MODEL: ScoringModel,
    ConfigKind.RULE_SET: RuleSet,
}

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
            "Le diagnostic est momentanément indisponible.",
            status=503,
        )
    return document_id


async def _load(session: AsyncSession, document_id: uuid.UUID, kind: ConfigKind) -> BaseModel:
    if document_id not in _cache:
        definition = (
            await session.execute(
                select(ConfigDocument.definition).where(ConfigDocument.id == document_id)
            )
        ).scalar_one()
        _cache[document_id] = MODEL_FOR_KIND[kind].model_validate(definition)
    return _cache[document_id]


async def load_questionnaire(
    session: AsyncSession, document_id: uuid.UUID
) -> QuestionnaireDefinition:
    result = await _load(session, document_id, ConfigKind.QUESTIONNAIRE)
    assert isinstance(result, QuestionnaireDefinition)  # noqa: S101 — garanti par le type
    return result


async def load_scoring_model(session: AsyncSession, document_id: uuid.UUID) -> ScoringModel:
    result = await _load(session, document_id, ConfigKind.SCORING_MODEL)
    assert isinstance(result, ScoringModel)  # noqa: S101
    return result


async def load_rule_set(session: AsyncSession, document_id: uuid.UUID) -> RuleSet:
    result = await _load(session, document_id, ConfigKind.RULE_SET)
    assert isinstance(result, RuleSet)  # noqa: S101
    return result
