"""Accès typé aux configurations du diagnostic (questionnaire, barème, règles)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from digital360.modules.configuration.application.store import load, publish, published_id
from digital360.modules.diagnostics.domain.config import (
    QuestionnaireDefinition,
    RuleSet,
    ScoringModel,
)

__all__ = [
    "load_questionnaire",
    "load_rule_set",
    "load_scoring_model",
    "publish",
    "published_id",
]


async def load_questionnaire(
    session: AsyncSession, document_id: uuid.UUID
) -> QuestionnaireDefinition:
    return await load(session, document_id, QuestionnaireDefinition)


async def load_scoring_model(session: AsyncSession, document_id: uuid.UUID) -> ScoringModel:
    return await load(session, document_id, ScoringModel)


async def load_rule_set(session: AsyncSession, document_id: uuid.UUID) -> RuleSet:
    return await load(session, document_id, RuleSet)
