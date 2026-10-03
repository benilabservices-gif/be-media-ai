"""Schémas publics du diagnostic, alignés sur les mocks de Kilo (digital360/js/api/mocks)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from digital360.core.pagination import PageInfo
from digital360.modules.diagnostics.domain.config import QuestionnaireDefinition

# ── Questionnaire (sans les points : le barème ne quitte jamais le serveur) ──


class OptionOut(BaseModel):
    value: str
    label: str


class QuestionOut(BaseModel):
    key: str
    label: str
    help_text: str | None
    type: str
    required: bool
    options: list[OptionOut]
    # Condition sur les réponses (clés de questions), même grammaire que les règles serveur
    visible_if: dict[str, Any] | None


class SectionOut(BaseModel):
    key: str
    title: str
    questions: list[QuestionOut]


class QuestionnaireOut(BaseModel):
    version: int
    sections: list[SectionOut]

    @classmethod
    def from_definition(cls, definition: QuestionnaireDefinition) -> "QuestionnaireOut":
        return cls(
            version=definition.version,
            sections=[
                SectionOut(
                    key=section.key,
                    title=section.title,
                    questions=[
                        QuestionOut(
                            key=question.key,
                            label=question.label,
                            help_text=question.help_text,
                            type=question.type.value,
                            required=question.required,
                            options=[
                                OptionOut(value=option.value, label=option.label)
                                for option in question.options
                            ],
                            visible_if=question.visible_if,
                        )
                        for question in section.questions
                    ],
                )
                for section in definition.sections
            ],
        )


# ── Parcours ──

Utm = Annotated[str, StringConstraints(max_length=200)]


class DiagnosticSource(BaseModel):
    utm_source: Utm | None = None
    utm_medium: Utm | None = None
    utm_campaign: Utm | None = None
    referrer: Annotated[str, StringConstraints(max_length=500)] | None = None


class StartDiagnosticRequest(BaseModel):
    source: DiagnosticSource | None = None


class StartDiagnosticResponse(BaseModel):
    id: uuid.UUID
    # À conserver par le frontend (localStorage) et à renvoyer dans X-Diagnostic-Token
    token: str
    questionnaire_version: int


class SaveAnswersRequest(BaseModel):
    # Réponses partielles acceptées ; null ou "" efface une réponse
    answers: Annotated[dict[str, Any], Field(max_length=100)]


class SaveAnswersResponse(BaseModel):
    answers: dict[str, Any]
    missing_required: list[str]


class Consents(BaseModel):
    privacy: bool
    marketing_email: bool = False
    marketing_whatsapp: bool = False


class CompleteDiagnosticRequest(BaseModel):
    consents: Consents


# ── Résultat (format du mock completeDiagnostic de Kilo) ──


class CategoryScoreOut(BaseModel):
    score: int
    label: str


class ScoreOut(BaseModel):
    total: int
    categories: dict[str, CategoryScoreOut]
    maturity_level: str
    maturity_label: str
    disclaimer: str


class PassportItemOut(BaseModel):
    key: str
    status: str
    source: Literal["DECLARED", "VERIFIED", "SYNCED"]
    details: dict[str, Any]
    status_changed_at: datetime | None = None


class PassportOut(BaseModel):
    items: list[PassportItemOut]


class PlanItemOut(BaseModel):
    id: uuid.UUID
    rule_key: str
    module: str
    phase: str
    priority: str
    reason: str
    current_state: str
    recommended_action: str
    product_code: str | None
    # Rempli à partir du catalogue (M4) ; null en attendant
    price: dict[str, Any] | None = None
    cta: str
    status: str


class ActionPlanOut(BaseModel):
    items: list[PlanItemOut]


class DiagnosticResultOut(BaseModel):
    id: uuid.UUID
    status: str
    completed_at: datetime | None
    score: ScoreOut
    passport: PassportOut
    action_plan: ActionPlanOut


class DiagnosticHistory(BaseModel):
    data: list[DiagnosticResultOut]


class PlanItemUpdate(BaseModel):
    # Le client peut écarter une recommandation ou la remettre en proposition
    status: Literal["DISMISSED", "PROPOSED"]


# ── Admin : liste des diagnostics (les prospects captés) ──


class DiagnosticSummaryOut(BaseModel):
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


class DiagnosticPage(BaseModel):
    data: list[DiagnosticSummaryOut]
    page: PageInfo
