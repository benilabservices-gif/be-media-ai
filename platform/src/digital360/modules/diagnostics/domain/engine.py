"""Moteurs purs du diagnostic (ARCHITECTURE.md §7) : aucune base, aucun réseau."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from email_validator import EmailNotValidError, validate_email

from digital360.core.rules import evaluate
from digital360.modules.diagnostics.domain.config import (
    PRIORITY_ORDER,
    SCORED_CATEGORIES,
    Category,
    PassportItemKey,
    Phase,
    Priority,
    Question,
    QuestionnaireDefinition,
    QuestionType,
    RuleSet,
    ScoringModel,
)

Answers = Mapping[str, Any]

MAX_TEXT_LENGTH = 2000
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_PHONE_SEPARATORS = re.compile(r"[\s.\-()]")
# Indicatifs des pays du questionnaire. Seule la France retire le 0 initial : ailleurs
# (Bénin 01…, Côte d'Ivoire 07…, Congo 06…) il fait partie du numéro.
_DIAL_CODES = {
    "CI": "225",
    "SN": "221",
    "CM": "237",
    "BJ": "229",
    "TG": "228",
    "BF": "226",
    "ML": "223",
    "NE": "227",
    "GA": "241",
    "CG": "242",
    "FR": "33",
}
_TRUNK_ZERO_DROPPED = {"FR"}
# Un numéro national fait au plus 10 chiffres : au-delà, il contient déjà l'indicatif
_MIN_INTERNATIONAL_DIGITS = 8

# ── Réponses ──


def _to_international(compact: str, country: str | None) -> str:
    """« 0190493132 » au Bénin devient « +2290190493132 » ; un numéro en +… est gardé."""
    if compact.startswith("00"):
        return "+" + compact[2:]
    code = _DIAL_CODES.get(country or "")
    if compact.startswith("+") or code is None or not compact.isdigit():
        return compact
    if compact.startswith(code) and len(compact) >= len(code) + _MIN_INTERNATIONAL_DIGITS:
        return "+" + compact
    if country in _TRUNK_ZERO_DROPPED:
        compact = compact.removeprefix("0")
    return "+" + code + compact


@dataclass(frozen=True)
class AnswerError:
    question: str
    reason: str


def _normalize(question: Question, value: Any, country: str | None) -> Any:
    """Renvoie la valeur normalisée, ou lève ValueError(raison)."""
    match question.type:
        case QuestionType.SINGLE:
            if value not in {option.value for option in question.options}:
                raise ValueError("unknown_option")
            return value
        case QuestionType.MULTI:
            allowed = {option.value for option in question.options}
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("expected_list")
            if not set(value) <= allowed:
                raise ValueError("unknown_option")
            # Ordre du questionnaire, sans doublon
            return [option.value for option in question.options if option.value in value]
        case QuestionType.TEXT:
            if not isinstance(value, str):
                raise ValueError("expected_text")
            value = value.strip()
            if len(value) > MAX_TEXT_LENGTH:
                raise ValueError("too_long")
            return value
        case QuestionType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("expected_number")
            return value
        case QuestionType.PHONE:
            if not isinstance(value, str):
                raise ValueError("expected_text")
            # « +225 07 00 00 00 00 » est accepté et stocké « +2250700000000 »
            compact = _to_international(_PHONE_SEPARATORS.sub("", value), country)
            if not _E164.match(compact):
                raise ValueError("invalid_phone")
            return compact
        case QuestionType.EMAIL:
            if not isinstance(value, str):
                raise ValueError("expected_text")
            try:
                return validate_email(value.strip(), check_deliverability=False).normalized
            except EmailNotValidError as exc:
                raise ValueError("invalid_email") from exc


def clean_answers(
    questionnaire: QuestionnaireDefinition, answers: Answers, *, country: str | None = None
) -> tuple[dict[str, Any], list[AnswerError]]:
    """Valide et normalise. `None` ou "" efface une réponse (renvoyée avec la valeur None).

    `country` (réponse déjà enregistrée) complète les numéros saisis sans indicatif ;
    le pays envoyé dans `answers` est prioritaire.
    """
    sent_country = answers.get("country")
    if isinstance(sent_country, str):
        country = sent_country
    cleaned: dict[str, Any] = {}
    errors: list[AnswerError] = []
    for key, value in answers.items():
        question = questionnaire.question(key)
        if question is None:
            errors.append(AnswerError(key, "unknown_question"))
            continue
        if value is None or value == "" or value == []:
            cleaned[key] = None
            continue
        try:
            cleaned[key] = _normalize(question, value, country)
        except ValueError as exc:
            errors.append(AnswerError(key, str(exc)))
    return cleaned, errors


def is_visible(question: Question, answers: Answers) -> bool:
    return question.visible_if is None or evaluate(question.visible_if, answers)


def visible_answers(questionnaire: QuestionnaireDefinition, answers: Answers) -> dict[str, Any]:
    """Réponses aux seules questions visibles : une réponse devenue sans objet est ignorée."""
    return {
        question.key: answers[question.key]
        for question in questionnaire.questions
        if question.key in answers and is_visible(question, answers)
    }


def missing_required(questionnaire: QuestionnaireDefinition, answers: Answers) -> list[str]:
    return [
        question.key
        for question in questionnaire.questions
        if question.required and is_visible(question, answers) and answers.get(question.key) is None
    ]


# ── Faits ──


def compute_facts(questionnaire: QuestionnaireDefinition, answers: Answers) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for name, definition in questionnaire.facts.items():
        if definition.answer is not None:
            facts[name] = answers.get(definition.answer)
        elif definition.count is not None:
            value = answers.get(definition.count)
            facts[name] = len(value) if isinstance(value, list) else 0
        elif definition.condition is not None:
            facts[name] = evaluate(definition.condition, answers)
    return facts


# ── Score ──


@dataclass(frozen=True)
class CategoryScore:
    score: int
    label: str


@dataclass(frozen=True)
class ScoreResult:
    total: int
    categories: dict[Category, CategoryScore]
    maturity_level: str
    maturity_label: str
    disclaimer: str


def _points(question: Question, value: Any) -> int:
    if value is None:
        return 0
    if question.type is QuestionType.SINGLE:
        return next((option.points for option in question.options if option.value == value), 0)
    if question.type is QuestionType.MULTI:
        total = sum(option.points for option in question.options if option.value in value)
        return min(total, question.max_points) if question.max_points is not None else total
    return 0


def compute_score(
    questionnaire: QuestionnaireDefinition, model: ScoringModel, answers: Answers
) -> ScoreResult:
    """Score par catégorie = points / maximum possible, sur les questions visibles."""
    earned: dict[Category, int] = dict.fromkeys(SCORED_CATEGORIES, 0)
    possible: dict[Category, int] = dict.fromkeys(SCORED_CATEGORIES, 0)
    for question in questionnaire.questions:
        if question.category not in earned or not is_visible(question, answers):
            continue
        earned[question.category] += _points(question, answers.get(question.key))
        possible[question.category] += question.max_score

    ratios = {
        category: earned[category] / possible[category]
        for category in SCORED_CATEGORIES
        if possible[category] > 0
    }
    # Une catégorie sans question applicable est exclue et les poids renormalisés
    weight_sum = sum(model.category_weights[category] for category in ratios)
    total = (
        round(
            sum(ratio * model.category_weights[c] for c, ratio in ratios.items()) / weight_sum * 100
        )
        if weight_sum
        else 0
    )
    band = next(band for band in model.maturity_bands if total >= band.min)
    return ScoreResult(
        total=total,
        categories={
            category: CategoryScore(
                score=round(ratios.get(category, 0) * 100),
                label=model.category_labels.get(category, category.value),
            )
            for category in SCORED_CATEGORIES
        },
        maturity_level=band.level,
        maturity_label=band.label,
        disclaimer=model.disclaimer,
    )


# ── Plan d'action ──


@dataclass(frozen=True)
class PlanItem:
    rule_key: str
    module: str
    phase: Phase
    priority: Priority
    reason: str
    current_state: str
    recommended_action: str
    product_code: str | None
    cta: str


def generate_plan(rule_set: RuleSet, facts: Mapping[str, Any]) -> list[PlanItem]:
    matched = [rule for rule in rule_set.rules if evaluate(rule.when, facts)]
    # Dans un groupe d'exclusivité, seule la règle la plus prioritaire (puis la première déclarée) reste
    kept_groups: dict[str, str] = {}
    for rule in sorted(matched, key=lambda item: PRIORITY_ORDER[item.priority]):
        if rule.exclusivity_group and rule.exclusivity_group not in kept_groups:
            kept_groups[rule.exclusivity_group] = rule.key
    selected = [
        rule
        for rule in matched
        if not rule.exclusivity_group or kept_groups[rule.exclusivity_group] == rule.key
    ]
    order = {rule.key: index for index, rule in enumerate(rule_set.rules)}
    selected.sort(key=lambda rule: (PRIORITY_ORDER[rule.priority], order[rule.key]))
    return [
        PlanItem(
            rule_key=rule.key,
            module=rule.module,
            phase=rule.phase,
            priority=rule.priority,
            reason=rule.reason,
            current_state=rule.current_state,
            recommended_action=rule.recommended_action,
            product_code=rule.product_code,
            cta=rule.cta,
        )
        for rule in selected
    ]


# ── Passport déclaratif ──


def declared_passport(
    questionnaire: QuestionnaireDefinition, answers: Answers
) -> dict[PassportItemKey, str]:
    """Statut de chaque élément du Passport d'après les déclarations."""
    statuses: dict[PassportItemKey, str] = {}
    for key in PassportItemKey:
        rule = questionnaire.passport.get(key)
        if rule and rule.active_if and evaluate(rule.active_if, answers):
            statuses[key] = "ACTIVE"
        elif rule and rule.in_progress_if and evaluate(rule.in_progress_if, answers):
            statuses[key] = "IN_PROGRESS"
        else:
            statuses[key] = "NOT_CONFIGURED"
    return statuses
