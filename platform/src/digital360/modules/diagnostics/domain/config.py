"""Modèles de la configuration métier versionnée : questionnaire, score, règles.

Validés à la publication : une configuration invalide n'atteint jamais un diagnostic.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from digital360.core.rules import Condition, validate


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QuestionType(StrEnum):
    SINGLE = "SINGLE"
    MULTI = "MULTI"
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    PHONE = "PHONE"
    EMAIL = "EMAIL"


class Category(StrEnum):
    IDENTITY = "IDENTITY"
    PRESENCE = "PRESENCE"
    VISIBILITY = "VISIBILITY"
    ACQUISITION = "ACQUISITION"
    CONVERSION = "CONVERSION"
    RETENTION = "RETENTION"
    GOALS = "GOALS"


SCORED_CATEGORIES = (
    Category.PRESENCE,
    Category.VISIBILITY,
    Category.ACQUISITION,
    Category.CONVERSION,
    Category.RETENTION,
)


class PassportItemKey(StrEnum):
    WEBSITE = "WEBSITE"
    DOMAIN = "DOMAIN"
    HOSTING = "HOSTING"
    EMAIL = "EMAIL"
    GOOGLE_BUSINESS = "GOOGLE_BUSINESS"
    FACEBOOK = "FACEBOOK"
    INSTAGRAM = "INSTAGRAM"
    TIKTOK = "TIKTOK"
    LINKEDIN = "LINKEDIN"
    WHATSAPP = "WHATSAPP"
    SEO = "SEO"
    ADS = "ADS"
    EMAILING = "EMAILING"
    CRM = "CRM"
    CONVERSION = "CONVERSION"
    ANALYTICS = "ANALYTICS"


def _checked(condition: Condition | None) -> Condition | None:
    if condition is not None:
        validate(condition)
    return condition


class Option(_Frozen):
    value: str
    label: str
    points: int = 0


class Question(_Frozen):
    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    label: str
    help_text: str | None = None
    type: QuestionType
    category: Category
    required: bool = False
    options: tuple[Option, ...] = ()
    # Plafond des points cumulés d'une question MULTI
    max_points: int | None = None
    visible_if: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        has_options = self.type in (QuestionType.SINGLE, QuestionType.MULTI)
        if has_options and not self.options:
            raise ValueError(f"{self.key} : une question {self.type} doit avoir des options")
        if not has_options and self.options:
            raise ValueError(f"{self.key} : seules SINGLE et MULTI ont des options")
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise ValueError(f"{self.key} : valeurs d'options en double")
        _checked(self.visible_if)
        return self

    @property
    def max_score(self) -> int:
        if self.type is QuestionType.SINGLE:
            return max((option.points for option in self.options), default=0)
        if self.type is QuestionType.MULTI:
            total = sum(max(option.points, 0) for option in self.options)
            return min(total, self.max_points) if self.max_points is not None else total
        return 0


class Section(_Frozen):
    key: str
    title: str
    questions: tuple[Question, ...]


class FactDefinition(_Frozen):
    """Exactement une forme : valeur d'une réponse, nombre de choix, ou condition booléenne."""

    answer: str | None = None
    count: str | None = None
    condition: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        forms = [self.answer, self.count, self.condition]
        if sum(form is not None for form in forms) != 1:
            raise ValueError("un fait se définit par answer, count OU condition")
        _checked(self.condition)
        return self


class PassportRule(_Frozen):
    active_if: dict[str, Any] | None = None
    in_progress_if: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _checked(self.active_if)
        _checked(self.in_progress_if)
        return self


class QuestionnaireDefinition(_Frozen):
    key: str
    version: int
    sections: tuple[Section, ...]
    facts: dict[str, FactDefinition] = Field(default_factory=dict)
    passport: dict[PassportItemKey, PassportRule] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [question.key for question in self.questions]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            raise ValueError(f"clés de questions en double : {sorted(duplicates)}")
        return self

    @property
    def questions(self) -> list[Question]:
        return [question for section in self.sections for question in section.questions]

    def question(self, key: str) -> Question | None:
        return next((question for question in self.questions if question.key == key), None)


class MaturityBand(_Frozen):
    min: Annotated[int, Field(ge=0, le=100)]
    level: str
    label: str


class ScoringModel(_Frozen):
    key: str
    version: int
    category_weights: dict[Category, Annotated[float, Field(gt=0)]]
    category_labels: dict[Category, str]
    maturity_bands: tuple[MaturityBand, ...]
    disclaimer: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.category_weights) != set(SCORED_CATEGORIES):
            raise ValueError(
                f"poids attendus pour exactement : {[c.value for c in SCORED_CATEGORIES]}"
            )
        minimums = [band.min for band in self.maturity_bands]
        if minimums != sorted(minimums, reverse=True) or minimums[-1] != 0:
            raise ValueError("maturity_bands : seuils décroissants, le dernier à 0")
        return self


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


PRIORITY_ORDER = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}


class Phase(StrEnum):
    PRESENT = "PRESENT"
    VISIBLE = "VISIBLE"
    ATTRACT = "ATTRACT"
    CONVERT = "CONVERT"
    RETAIN = "RETAIN"


class Rule(_Frozen):
    key: str
    module: Literal["PRESENCE", "VISIBILITY", "ACQUISITION", "CONVERSION", "RETENTION", "ANALYTICS"]
    phase: Phase
    priority: Priority
    when: dict[str, Any]
    reason: str
    current_state: str
    recommended_action: str
    product_code: str | None = None
    cta: Literal["ACTIVATE", "BUY", "CONTACT", "LEARN"]
    exclusivity_group: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        validate(self.when)
        return self


class RuleSet(_Frozen):
    key: str
    version: int
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [rule.key for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("clés de règles en double")
        return self
