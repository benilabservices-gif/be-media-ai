from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from digital360.modules.diagnostics.domain.config import (
    Category,
    PassportItemKey,
    QuestionnaireDefinition,
    RuleSet,
    ScoringModel,
)
from digital360.modules.diagnostics.domain.engine import (
    clean_answers,
    compute_facts,
    compute_score,
    declared_passport,
    generate_plan,
    missing_required,
    visible_answers,
)
from digital360.modules.diagnostics.domain.seeds import (
    load_questionnaire,
    load_rule_set,
    load_scoring_model,
)

SEEDS = Path("config/seeds")


@pytest.fixture(scope="module")
def questionnaire() -> QuestionnaireDefinition:
    return load_questionnaire(SEEDS / "questionnaire.v3.yaml")


@pytest.fixture(scope="module")
def model() -> ScoringModel:
    return load_scoring_model(SEEDS / "scoring.v1.yaml")


@pytest.fixture(scope="module")
def rules() -> RuleSet:
    return load_rule_set(SEEDS / "rules.v1.yaml")


BEGINNER: dict[str, Any] = {
    "company": "Salon Belle Afrique",
    "country": "CI",
    "sector": "beauty",
    "site": "non",
    "domain": "non",
    "emailprof": "non",
    "gb": "non",
    "socials": [],
    "ads": "none",
    "contact": "walkin",
    "booking": "none",
    "clientbase": "none",
    "comm": "none",
    "freq": "rarely",
    "producer": "none",
    "goal": "presence",
}

EXPERT: dict[str, Any] = {
    "company": "Hôtel Lagune",
    "country": "CI",
    "sector": "restaurant",
    "site": "oui",
    "domain": "oui",
    "emailprof": "oui",
    "gb": "oui",
    "reviews": "20+",
    "socials": ["facebook", "instagram", "tiktok", "linkedin"],
    "seo": "top3",
    "ads": "meta",
    "contact": "form",
    "booking": "both",
    "clientbase": "crm",
    "comm": "email",
    "freq": "daily",
    "producer": "agency",
    "goal": "sales",
}

# ── Configuration v1 ──


def test_should_load_all_v1_seeds(
    questionnaire: QuestionnaireDefinition, model: ScoringModel, rules: RuleSet
) -> None:
    assert len(questionnaire.questions) == 23
    assert set(model.category_weights) == {
        Category.PRESENCE,
        Category.VISIBILITY,
        Category.ACQUISITION,
        Category.CONVERSION,
        Category.RETENTION,
    }
    assert len(rules.rules) >= 10


def test_should_reference_only_known_questions_in_facts_and_passport(
    questionnaire: QuestionnaireDefinition,
) -> None:
    keys = {question.key for question in questionnaire.questions}
    for definition in questionnaire.facts.values():
        assert (definition.answer or definition.count or "company") in keys


def test_should_reject_invalid_config() -> None:
    with pytest.raises(ValidationError, match="options"):
        QuestionnaireDefinition.model_validate(
            {
                "key": "x",
                "version": 1,
                "sections": [
                    {
                        "key": "s",
                        "title": "S",
                        "questions": [
                            {"key": "q", "label": "Q", "type": "SINGLE", "category": "PRESENCE"}
                        ],
                    }
                ],
            }
        )


def test_should_reject_rule_with_invalid_condition(rules: RuleSet) -> None:
    broken = rules.model_dump()
    broken["rules"][0]["when"] = {"fact": "has_website", "matches": ".*"}

    with pytest.raises(ValidationError):
        RuleSet.model_validate(broken)


# ── Réponses ──


def test_should_normalize_phone_email_and_multi(questionnaire: QuestionnaireDefinition) -> None:
    cleaned, errors = clean_answers(
        questionnaire,
        {
            "phone": "+225 07 00 00 00 00",
            "email": "Contact@Salon.CI",
            "socials": ["tiktok", "facebook", "tiktok"],
        },
    )

    assert errors == []
    assert cleaned["phone"] == "+2250700000000"
    assert cleaned["email"] == "Contact@salon.ci"
    assert cleaned["socials"] == ["facebook", "tiktok"]


@pytest.mark.parametrize(
    ("country", "typed", "expected"),
    [
        # Saisies réelles des visiteurs TikTok : numéro local, sans indicatif
        ("BJ", "01 90 49 31 32", "+2290190493132"),
        ("CI", "07 00 00 00 00", "+2250700000000"),
        ("SN", "77 123 45 67", "+221771234567"),
        ("CM", "6 90 12 34 56", "+237690123456"),
        ("TG", "90 12 34 56", "+22890123456"),
        ("FR", "06 12 34 56 78", "+33612345678"),
        ("BE", "0470 12 34 56", "+32470123456"),
        ("CH", "079 123 45 67", "+41791234567"),
        ("GB", "07911 123456", "+447911123456"),
        ("IT", "333 123 4567", "+393331234567"),
        ("ES", "612 34 56 78", "+34612345678"),
        ("CA", "514 555 0123", "+15145550123"),
        # Indicatif tapé avec 00, ou sans le +
        ("BJ", "00229 01 90 49 31 32", "+2290190493132"),
        ("BJ", "229 01 90 49 31 32", "+2290190493132"),
        # Indicatif d'un autre pays : gardé tel quel
        ("BJ", "+225 07 00 00 00 00", "+2250700000000"),
    ],
)
def test_should_add_country_code_to_local_phone(
    questionnaire: QuestionnaireDefinition, country: str, typed: str, expected: str
) -> None:
    cleaned, errors = clean_answers(
        questionnaire, {"phone": typed, "whatsapp": typed}, country=country
    )

    assert errors == []
    assert cleaned["phone"] == cleaned["whatsapp"] == expected


def test_should_take_country_from_same_answers(questionnaire: QuestionnaireDefinition) -> None:
    cleaned, errors = clean_answers(questionnaire, {"country": "BJ", "phone": "0190493132"})

    assert errors == []
    assert cleaned["phone"] == "+2290190493132"


def test_should_reject_local_phone_without_country(
    questionnaire: QuestionnaireDefinition,
) -> None:
    _, errors = clean_answers(questionnaire, {"phone": "0190493132"})

    assert [error.reason for error in errors] == ["invalid_phone"]


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ({"site": "peut-être"}, "unknown_option"),
        ({"socials": "facebook"}, "expected_list"),
        ({"phone": "07 00 00"}, "invalid_phone"),
        ({"email": "pas-un-email"}, "invalid_email"),
        ({"inconnue": "x"}, "unknown_question"),
        ({"description": "x" * 2001}, "too_long"),
    ],
)
def test_should_reject_invalid_answers(
    questionnaire: QuestionnaireDefinition, answers: dict[str, Any], reason: str
) -> None:
    _, errors = clean_answers(questionnaire, answers)

    assert [error.reason for error in errors] == [reason]


def test_should_clear_answer_with_empty_value(questionnaire: QuestionnaireDefinition) -> None:
    cleaned, errors = clean_answers(questionnaire, {"city": "", "socials": []})

    assert errors == []
    assert cleaned == {"city": None, "socials": None}


def test_should_hide_seo_question_without_website(questionnaire: QuestionnaireDefinition) -> None:
    answers = {"site": "non", "seo": "top3"}

    assert "seo" not in visible_answers(questionnaire, answers)


def test_should_list_missing_required_visible_questions(
    questionnaire: QuestionnaireDefinition,
) -> None:
    assert missing_required(questionnaire, {"company": "X"}) == [
        "country",
        "site",
        "gb",
        "ads",
        "goal",
    ]


# ── Score ──


def test_should_score_beginner_near_zero(
    questionnaire: QuestionnaireDefinition, model: ScoringModel
) -> None:
    result = compute_score(questionnaire, model, BEGINNER)

    # Seul « passage physique » rapporte des points : 5/70 en conversion, pondéré à 20 %, donne 1,4 arrondi à 1
    assert result.categories[Category.CONVERSION].score == 7
    assert result.total == 1
    assert result.maturity_level == "BEGINNER"
    assert result.disclaimer.startswith("Le Digital Score est un indicateur interne")


def test_should_score_100_for_expert(
    questionnaire: QuestionnaireDefinition, model: ScoringModel
) -> None:
    result = compute_score(questionnaire, model, EXPERT)

    assert result.total == 100
    assert {category: item.score for category, item in result.categories.items()} == {
        Category.PRESENCE: 100,
        Category.VISIBILITY: 100,
        Category.ACQUISITION: 100,
        Category.CONVERSION: 100,
        Category.RETENTION: 100,
    }
    assert result.maturity_level == "ADVANCED"


def test_should_exclude_hidden_questions_from_maximum(
    questionnaire: QuestionnaireDefinition, model: ScoringModel
) -> None:
    """Sans site, la question SEO disparaît : elle ne pénalise pas la visibilité."""
    answers = {**EXPERT, "site": "non", "seo": None}

    result = compute_score(questionnaire, model, answers)

    assert result.categories[Category.VISIBILITY].score == 100
    assert result.categories[Category.PRESENCE].score == 60  # (20+15+25) / 100


def test_should_cap_multi_select_points(
    questionnaire: QuestionnaireDefinition, model: ScoringModel
) -> None:
    two_networks = compute_score(
        questionnaire, model, {**EXPERT, "socials": ["facebook", "tiktok"]}
    )

    four_networks = compute_score(questionnaire, model, EXPERT)

    assert two_networks.categories[Category.VISIBILITY].score < 100
    assert four_networks.categories[Category.VISIBILITY].score == 100


def test_should_weight_categories_in_total(
    questionnaire: QuestionnaireDefinition, model: ScoringModel
) -> None:
    """Présence parfaite (poids 30 %) + 1 point de conversion (passage physique)."""
    answers = {**BEGINNER, "site": "oui", "domain": "oui", "emailprof": "oui", "gb": "oui"}

    result = compute_score(questionnaire, model, visible_answers(questionnaire, answers))

    assert result.categories[Category.PRESENCE].score == 100
    assert result.total == 30 + 1


# ── Faits, plan, passport ──


def test_should_compute_facts(questionnaire: QuestionnaireDefinition) -> None:
    facts = compute_facts(questionnaire, {**BEGINNER, "socials": ["facebook"], "site": "en-cours"})

    assert facts["has_website"] is False
    assert facts["website_in_progress"] is True
    assert facts["social_networks_count"] == 1
    assert facts["runs_ads"] is False
    assert facts["content_frequency"] == "rarely"


def test_should_recommend_digital_start_first_without_website(
    questionnaire: QuestionnaireDefinition, rules: RuleSet
) -> None:
    plan = generate_plan(rules, compute_facts(questionnaire, BEGINNER))

    assert plan[0].rule_key == "no_website"
    assert plan[0].priority == "CRITICAL"
    assert plan[0].product_code == "DIGITAL_START"
    keys = [item.rule_key for item in plan]
    assert "no_google_business" in keys
    assert "no_online_booking" in keys  # secteur beauté


def test_should_keep_only_highest_priority_rule_per_exclusivity_group(
    questionnaire: QuestionnaireDefinition, rules: RuleSet
) -> None:
    plan = generate_plan(rules, compute_facts(questionnaire, {**BEGINNER, "site": "en-cours"}))

    keys = [item.rule_key for item in plan]
    assert "website_in_progress" in keys
    assert "no_website" not in keys


def test_should_recommend_nothing_for_expert(
    questionnaire: QuestionnaireDefinition, rules: RuleSet
) -> None:
    assert generate_plan(rules, compute_facts(questionnaire, EXPERT)) == []


def test_should_sort_plan_by_priority(
    questionnaire: QuestionnaireDefinition, rules: RuleSet
) -> None:
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    plan = generate_plan(rules, compute_facts(questionnaire, BEGINNER))

    ranks = [order[item.priority] for item in plan]
    assert ranks == sorted(ranks)


def test_should_derive_declared_passport(questionnaire: QuestionnaireDefinition) -> None:
    passport = declared_passport(
        questionnaire,
        {**BEGINNER, "site": "en-cours", "socials": ["instagram"], "whatsapp": "+2250700000000"},
    )

    assert passport[PassportItemKey.WEBSITE] == "IN_PROGRESS"
    assert passport[PassportItemKey.INSTAGRAM] == "ACTIVE"
    assert passport[PassportItemKey.FACEBOOK] == "NOT_CONFIGURED"
    assert passport[PassportItemKey.WHATSAPP] == "ACTIVE"
    assert passport[PassportItemKey.HOSTING] == "NOT_CONFIGURED"
    assert len(passport) == len(PassportItemKey)
