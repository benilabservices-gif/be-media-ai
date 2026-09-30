"""Cycle de vie d'un diagnostic (ARCHITECTURE.md §7) : démarrage anonyme, réponses,
complétion (score, plan, passport déclaratif), rattachement à une organisation.
"""

import dataclasses
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.consent import ConsentPurpose, record_consents
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, publish
from digital360.core.pagination import PageInfo, PageParams, build_page_info
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.catalog.application.service import current_catalog_or_none, price_payload
from digital360.modules.configuration.infrastructure.models import ConfigKind
from digital360.modules.diagnostics.application import config_store
from digital360.modules.diagnostics.domain.config import QuestionnaireDefinition
from digital360.modules.diagnostics.domain.engine import (
    ScoreResult,
    clean_answers,
    compute_facts,
    compute_score,
    declared_passport,
    generate_plan,
    missing_required,
    visible_answers,
)
from digital360.modules.diagnostics.infrastructure.models import (
    ActionPlan,
    ActionPlanItem,
    DiagnosticSession,
    DiagnosticStatus,
    DigitalScore,
    PlanItemStatus,
)

DIAGNOSTIC_COMPLETED_EVENT = "diagnostic.completed"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _not_found() -> AppError:
    # Même réponse pour un identifiant inconnu et un jeton faux : rien n'est révélé
    return AppError("NOT_FOUND", "Diagnostic introuvable.", status=404)


async def _set_diagnostic_scope(session: AsyncSession, token: str) -> None:
    """Donne accès (RLS) au seul diagnostic dont on présente le jeton."""
    await session.execute(
        text("SELECT set_config('app.diagnostic_token_hash', :token_hash, true)"),
        {"token_hash": hash_token(token)},
    )


@dataclass(frozen=True)
class StartedDiagnostic:
    id: uuid.UUID
    token: str
    questionnaire_version: int


@dataclass(frozen=True)
class SavedAnswers:
    answers: dict[str, Any]
    missing_required: list[str]


@dataclass(frozen=True)
class PlanItemView:
    id: uuid.UUID
    rule_key: str
    module: str
    phase: str
    priority: str
    reason: str
    current_state: str
    recommended_action: str
    product_code: str | None
    cta: str
    status: str
    price: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiagnosticResult:
    id: uuid.UUID
    status: str
    completed_at: datetime | None
    score: dict[str, Any]
    passport_items: list[dict[str, Any]]
    plan_items: list[PlanItemView]


@dataclass(frozen=True)
class ClaimedDiagnostic:
    """Ce dont la création d'organisation a besoin : réponses d'identité et passport déclaratif."""

    session_id: uuid.UUID
    answers: dict[str, Any]
    passport_statuses: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagnosticSummary:
    id: uuid.UUID
    status: str
    organization_id: uuid.UUID | None
    company: str | None
    sector: str | None
    country: str | None
    city: str | None
    phone: str | None
    whatsapp: str | None
    email: str | None
    total_score: int | None
    maturity_level: str | None
    marketing_email_consent: bool
    marketing_whatsapp_consent: bool
    created_at: datetime
    completed_at: datetime | None


def _score_payload(score: ScoreResult) -> dict[str, Any]:
    return {
        "total": score.total,
        "categories": {
            category.value: {"score": item.score, "label": item.label}
            for category, item in score.categories.items()
        },
        "maturity_level": score.maturity_level,
        "maturity_label": score.maturity_label,
        "disclaimer": score.disclaimer,
    }


class DiagnosticService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: JobRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    @asynccontextmanager
    async def _public_transaction(self, token: str) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session, session.begin():
            await _set_diagnostic_scope(session, token)
            yield session

    async def published_questionnaire(self) -> QuestionnaireDefinition:
        async with self._session_factory() as session:
            document_id = await config_store.published_id(session, ConfigKind.QUESTIONNAIRE)
            return await config_store.load_questionnaire(session, document_id)

    async def start(self, source: dict[str, str] | None) -> StartedDiagnostic:
        token = secrets.token_urlsafe(32)
        async with self._public_transaction(token) as session:
            questionnaire_id = await config_store.published_id(session, ConfigKind.QUESTIONNAIRE)
            questionnaire = await config_store.load_questionnaire(session, questionnaire_id)
            diagnostic = DiagnosticSession(
                token_hash=hash_token(token),
                # Les trois versions sont figées dès le départ : une publication en cours de
                # route ne change pas les règles du jeu pour ce répondant
                questionnaire_id=questionnaire_id,
                scoring_model_id=await config_store.published_id(session, ConfigKind.SCORING_MODEL),
                rule_set_id=await config_store.published_id(session, ConfigKind.RULE_SET),
                source=source,
            )
            session.add(diagnostic)
            await session.flush()
        return StartedDiagnostic(diagnostic.id, token, questionnaire.version)

    async def save_answers(
        self, diagnostic_id: uuid.UUID, token: str, answers: dict[str, Any]
    ) -> SavedAnswers:
        async with self._public_transaction(token) as session:
            diagnostic = await self._load_for_token(session, diagnostic_id, token)
            if diagnostic.status != DiagnosticStatus.IN_PROGRESS:
                raise AppError(
                    "DIAGNOSTIC_ALREADY_COMPLETED",
                    "Ce diagnostic est terminé et ne peut plus être modifié.",
                    status=409,
                )
            questionnaire = await config_store.load_questionnaire(
                session, diagnostic.questionnaire_id
            )
            cleaned, errors = clean_answers(questionnaire, answers)
            if errors:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Certaines réponses sont invalides.",
                    errors=[
                        {"field": f"answers.{error.question}", "reason": error.reason}
                        for error in errors
                    ],
                )
            merged = {**diagnostic.answers, **cleaned}
            diagnostic.answers = {key: value for key, value in merged.items() if value is not None}
            return SavedAnswers(
                answers=diagnostic.answers,
                missing_required=missing_required(questionnaire, diagnostic.answers),
            )

    async def complete(
        self,
        diagnostic_id: uuid.UUID,
        token: str,
        *,
        privacy: bool,
        marketing_email: bool,
        marketing_whatsapp: bool,
        ip: str | None,
    ) -> DiagnosticResult:
        async with self._public_transaction(token) as session:
            diagnostic = await self._load_for_token(session, diagnostic_id, token)
            if diagnostic.status != DiagnosticStatus.IN_PROGRESS:
                # Idempotent : un double clic ou un rechargement renvoie le même résultat
                return await self._result(session, diagnostic)
            if not privacy:
                raise AppError(
                    "CONSENT_REQUIRED",
                    "Merci d'accepter la politique de confidentialité pour recevoir votre diagnostic.",
                    status=422,
                    errors=[{"field": "consents.privacy", "reason": "required"}],
                )
            questionnaire = await config_store.load_questionnaire(
                session, diagnostic.questionnaire_id
            )
            answers = visible_answers(questionnaire, diagnostic.answers)
            missing = missing_required(questionnaire, answers)
            if missing:
                raise AppError(
                    "QUESTIONNAIRE_INCOMPLETE",
                    "Certaines questions obligatoires n'ont pas de réponse.",
                    status=422,
                    errors=[{"field": f"answers.{key}", "reason": "required"} for key in missing],
                )

            model = await config_store.load_scoring_model(session, diagnostic.scoring_model_id)
            rule_set = await config_store.load_rule_set(session, diagnostic.rule_set_id)
            facts = compute_facts(questionnaire, answers)
            score = compute_score(questionnaire, model, answers)
            plan_items = generate_plan(rule_set, facts)

            session.add(
                DigitalScore(
                    session_id=diagnostic.id,
                    scoring_model_id=diagnostic.scoring_model_id,
                    total=score.total,
                    categories=_score_payload(score)["categories"],
                    maturity_level=score.maturity_level,
                    maturity_label=score.maturity_label,
                )
            )
            plan = ActionPlan(session_id=diagnostic.id, rule_set_id=diagnostic.rule_set_id)
            session.add(plan)
            await session.flush()
            session.add_all(
                ActionPlanItem(
                    plan_id=plan.id,
                    position=position,
                    rule_key=item.rule_key,
                    module=item.module,
                    phase=item.phase.value,
                    priority=item.priority.value,
                    reason=item.reason,
                    current_state=item.current_state,
                    recommended_action=item.recommended_action,
                    product_code=item.product_code,
                    cta=item.cta,
                )
                for position, item in enumerate(plan_items)
            )
            diagnostic.answers = answers
            diagnostic.facts = facts
            diagnostic.status = DiagnosticStatus.COMPLETED
            diagnostic.completed_at = datetime.now(UTC)
            diagnostic.marketing_email_consent = marketing_email
            diagnostic.marketing_whatsapp_consent = marketing_whatsapp
            await record_consents(
                session,
                subject_type="DIAGNOSTIC_SESSION",
                subject_id=diagnostic.id,
                choices={
                    ConsentPurpose.PRIVACY: True,
                    ConsentPurpose.MARKETING_EMAIL: marketing_email,
                    ConsentPurpose.MARKETING_WHATSAPP: marketing_whatsapp,
                },
                source="diagnostic",
                ip=ip,
            )
            # Prévient l'équipe commerciale (abonnés : notifications, en M8)
            await publish(
                session,
                self._registry,
                DIAGNOSTIC_COMPLETED_EVENT,
                {"diagnostic_id": str(diagnostic.id), "total_score": score.total},
            )
            await session.flush()
            return await self._result(session, diagnostic)

    async def result(self, diagnostic_id: uuid.UUID, token: str) -> DiagnosticResult:
        async with self._public_transaction(token) as session:
            diagnostic = await self._load_for_token(session, diagnostic_id, token)
            if diagnostic.status == DiagnosticStatus.IN_PROGRESS:
                raise AppError(
                    "DIAGNOSTIC_NOT_COMPLETED",
                    "Ce diagnostic n'est pas encore terminé.",
                    status=409,
                )
            return await self._result(session, diagnostic)

    async def latest_for_organization(self, context: TenantContext) -> DiagnosticResult | None:
        async with tenant_transaction(self._session_factory, context) as session:
            diagnostic = (
                await session.execute(
                    select(DiagnosticSession)
                    .where(DiagnosticSession.organization_id == context.organization_id)
                    .order_by(DiagnosticSession.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return await self._result(session, diagnostic) if diagnostic else None

    async def history(self, context: TenantContext) -> list[DiagnosticResult]:
        async with tenant_transaction(self._session_factory, context) as session:
            diagnostics = (
                await session.execute(
                    select(DiagnosticSession)
                    .where(DiagnosticSession.organization_id == context.organization_id)
                    .order_by(DiagnosticSession.id.desc())
                )
            ).scalars()
            return [await self._result(session, diagnostic) for diagnostic in diagnostics]

    async def update_plan_item(
        self, context: TenantContext, actor: Actor, item_id: uuid.UUID, status: PlanItemStatus
    ) -> PlanItemView:
        async with tenant_transaction(self._session_factory, context) as session:
            item = (
                await session.execute(
                    select(ActionPlanItem).where(
                        ActionPlanItem.id == item_id,
                        ActionPlanItem.organization_id == context.organization_id,
                    )
                )
            ).scalar_one_or_none()
            if item is None:
                raise AppError("NOT_FOUND", "Recommandation introuvable.", status=404)
            old_status = item.status
            item.status = status.value
            await record_audit(
                session,
                actor=actor,
                action="action_plan_item.update_status",
                entity_type="action_plan_item",
                entity_id=item.id,
                organization_id=context.organization_id,
                old_value={"status": old_status},
                new_value={"status": status.value},
            )
            return _plan_item_view(item)

    async def admin_list(
        self, params: PageParams, *, status: DiagnosticStatus | None
    ) -> tuple[list[DiagnosticSummary], PageInfo]:
        statement = (
            select(DiagnosticSession, DigitalScore)
            .outerjoin(DigitalScore, DigitalScore.session_id == DiagnosticSession.id)
            .order_by(DiagnosticSession.id.desc())
            .limit(params.limit + 1)
        )
        if params.after is not None:
            statement = statement.where(DiagnosticSession.id < params.after)
        if status is not None:
            statement = statement.where(DiagnosticSession.status == status.value)
        async with staff_transaction(self._session_factory) as session:
            rows = (await session.execute(statement)).tuples().all()
        kept, page = build_page_info([diagnostic.id for diagnostic, _ in rows], params)
        return [_summary(diagnostic, score) for diagnostic, score in rows[:kept]], page

    async def _load_for_token(
        self, session: AsyncSession, diagnostic_id: uuid.UUID, token: str
    ) -> DiagnosticSession:
        diagnostic = (
            await session.execute(
                select(DiagnosticSession).where(
                    DiagnosticSession.id == diagnostic_id,
                    DiagnosticSession.token_hash == hash_token(token),
                )
            )
        ).scalar_one_or_none()
        if diagnostic is None:
            raise _not_found()
        return diagnostic

    async def _result(
        self, session: AsyncSession, diagnostic: DiagnosticSession
    ) -> DiagnosticResult:
        score = (
            await session.execute(
                select(DigitalScore).where(DigitalScore.session_id == diagnostic.id)
            )
        ).scalar_one()
        model = await config_store.load_scoring_model(session, diagnostic.scoring_model_id)
        questionnaire = await config_store.load_questionnaire(session, diagnostic.questionnaire_id)
        items = (
            await session.execute(
                select(ActionPlanItem)
                .join(ActionPlan, ActionPlan.id == ActionPlanItem.plan_id)
                .where(ActionPlan.session_id == diagnostic.id)
                .order_by(ActionPlanItem.position)
            )
        ).scalars()
        passport = declared_passport(questionnaire, diagnostic.answers)
        # Prix dans la devise du pays déclaré ; absents si le catalogue n'est pas publié
        catalog = await current_catalog_or_none(session)
        currency = (
            catalog.currency_for_country(diagnostic.answers.get("country")) if catalog else None
        )

        def with_price(view: PlanItemView) -> PlanItemView:
            if catalog is None or currency is None or view.product_code is None:
                return view
            price = price_payload(catalog.product(view.product_code), currency)
            return dataclasses.replace(view, price=price)

        return DiagnosticResult(
            id=diagnostic.id,
            status=diagnostic.status,
            completed_at=diagnostic.completed_at,
            score={
                "total": score.total,
                "categories": score.categories,
                "maturity_level": score.maturity_level,
                "maturity_label": score.maturity_label,
                "disclaimer": model.disclaimer,
            },
            passport_items=[
                {"key": key.value, "status": status, "source": "DECLARED", "details": {}}
                for key, status in passport.items()
            ],
            plan_items=[with_price(_plan_item_view(item)) for item in items],
        )


async def load_claimable(
    session: AsyncSession, diagnostic_id: uuid.UUID, token: str
) -> ClaimedDiagnostic:
    """Étape 1 du rattachement : vérifie que le diagnostic peut être rattaché, renvoie ses données.

    S'exécute dans la transaction de l'appelant (création de l'organisation).
    """
    await _set_diagnostic_scope(session, token)
    diagnostic = (
        await session.execute(
            select(DiagnosticSession).where(
                DiagnosticSession.id == diagnostic_id,
                DiagnosticSession.token_hash == hash_token(token),
            )
        )
    ).scalar_one_or_none()
    if diagnostic is None:
        raise _not_found()
    if diagnostic.status == DiagnosticStatus.CLAIMED:
        raise AppError(
            "DIAGNOSTIC_ALREADY_CLAIMED",
            "Ce diagnostic est déjà rattaché à une entreprise.",
            status=409,
        )
    if diagnostic.status != DiagnosticStatus.COMPLETED:
        raise AppError(
            "DIAGNOSTIC_NOT_COMPLETED",
            "Terminez le diagnostic avant de créer votre espace.",
            status=409,
        )
    questionnaire = await config_store.load_questionnaire(session, diagnostic.questionnaire_id)
    return ClaimedDiagnostic(
        session_id=diagnostic.id,
        answers=dict(diagnostic.answers),
        passport_statuses={
            key.value: status
            for key, status in declared_passport(questionnaire, diagnostic.answers).items()
        },
    )


async def attach_to_organization(
    session: AsyncSession, diagnostic_id: uuid.UUID, organization_id: uuid.UUID
) -> None:
    """Étape 2 : rattache le diagnostic, son score et son plan à l'organisation créée."""
    await session.execute(
        update(DiagnosticSession)
        .where(DiagnosticSession.id == diagnostic_id)
        .values(
            organization_id=organization_id,
            status=DiagnosticStatus.CLAIMED,
            claimed_at=datetime.now(UTC),
        )
    )
    for model in (DigitalScore, ActionPlan):
        await session.execute(
            update(model)
            .where(model.session_id == diagnostic_id)
            .values(organization_id=organization_id)
        )
    await session.execute(
        update(ActionPlanItem)
        .where(
            ActionPlanItem.plan_id.in_(
                select(ActionPlan.id).where(ActionPlan.session_id == diagnostic_id)
            )
        )
        .values(organization_id=organization_id)
    )


def _plan_item_view(item: ActionPlanItem) -> PlanItemView:
    return PlanItemView(
        id=item.id,
        rule_key=item.rule_key,
        module=item.module,
        phase=item.phase,
        priority=item.priority,
        reason=item.reason,
        current_state=item.current_state,
        recommended_action=item.recommended_action,
        product_code=item.product_code,
        cta=item.cta,
        status=item.status,
    )


def _summary(diagnostic: DiagnosticSession, score: DigitalScore | None) -> DiagnosticSummary:
    answers = diagnostic.answers
    return DiagnosticSummary(
        id=diagnostic.id,
        status=diagnostic.status,
        organization_id=diagnostic.organization_id,
        company=answers.get("company"),
        sector=answers.get("sector"),
        country=answers.get("country"),
        city=answers.get("city"),
        phone=answers.get("phone"),
        whatsapp=answers.get("whatsapp"),
        email=answers.get("email"),
        total_score=score.total if score else None,
        maturity_level=score.maturity_level if score else None,
        marketing_email_consent=diagnostic.marketing_email_consent,
        marketing_whatsapp_consent=diagnostic.marketing_whatsapp_consent,
        created_at=diagnostic.created_at,
        completed_at=diagnostic.completed_at,
    )
