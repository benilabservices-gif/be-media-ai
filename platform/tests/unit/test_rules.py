from typing import Any

import pytest

from digital360.core.rules import RuleSyntaxError, evaluate, validate

FACTS: dict[str, Any] = {
    "has_website": False,
    "social_networks_count": 2,
    "content_frequency": "monthly",
    "networks": ["facebook", "instagram"],
    "city": "Abidjan",
    "empty": None,
}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"fact": "has_website", "eq": False}, True),
        ({"fact": "has_website", "ne": False}, False),
        ({"fact": "social_networks_count", "gt": 1}, True),
        ({"fact": "social_networks_count", "gte": 2}, True),
        ({"fact": "social_networks_count", "lt": 2}, False),
        ({"fact": "social_networks_count", "lte": 2}, True),
        ({"fact": "content_frequency", "in": ["monthly", "rarely"]}, True),
        ({"fact": "networks", "contains": "instagram"}, True),
        ({"fact": "city", "contains": "bid"}, True),
        ({"fact": "has_website", "exists": True}, True),
        ({"fact": "empty", "exists": False}, True),
        ({"fact": "unknown", "exists": False}, True),
    ],
)
def test_should_evaluate_each_operator(condition: dict[str, Any], expected: bool) -> None:
    assert evaluate(condition, FACTS) is expected


def test_should_combine_all_any_not() -> None:
    condition = {
        "all": [
            {"fact": "social_networks_count", "gt": 0},
            {
                "any": [
                    {"fact": "content_frequency", "eq": "daily"},
                    {"not": {"fact": "has_website", "eq": True}},
                ]
            },
        ]
    }

    assert evaluate(condition, FACTS) is True


def test_should_treat_empty_all_as_true_and_empty_any_as_false() -> None:
    assert evaluate({"all": []}, FACTS) is True
    assert evaluate({"any": []}, FACTS) is False


@pytest.mark.parametrize("op", ["eq", "ne", "gt", "in", "contains"])
def test_should_return_false_when_fact_is_missing(op: str) -> None:
    value: Any = ["x"] if op == "in" else 1
    assert evaluate({"fact": "unknown", op: value}, FACTS) is False


def test_should_return_false_instead_of_raising_on_incompatible_types() -> None:
    assert evaluate({"fact": "city", "gt": 3}, FACTS) is False
    assert evaluate({"fact": "social_networks_count", "contains": "a"}, FACTS) is False


def test_should_not_confuse_booleans_with_integers() -> None:
    facts = {"flag": True, "count": 1}

    assert evaluate({"fact": "flag", "eq": 1}, facts) is False
    assert evaluate({"fact": "count", "eq": True}, facts) is False
    assert evaluate({"fact": "flag", "gt": 0}, facts) is False


@pytest.mark.parametrize(
    "condition",
    [
        "pas un objet",
        {"all": {"fact": "x", "eq": 1}},
        {"all": [], "any": []},
        {"fact": "x"},
        {"fact": "x", "eq": 1, "ne": 2},
        {"fact": "x", "matches": ".*"},
        {"fact": "", "eq": 1},
        {"fact": "x", "in": "abc"},
        {"fact": "x", "exists": "oui"},
        {"not": {"fact": "x", "unknown_op": 1}},
    ],
)
def test_should_reject_invalid_syntax(condition: Any) -> None:
    with pytest.raises(RuleSyntaxError):
        validate(condition)


def test_should_accept_valid_nested_condition() -> None:
    validate({"all": [{"fact": "a", "eq": 1}, {"not": {"any": [{"fact": "b", "in": [1, 2]}]}}]})


def test_should_locate_syntax_error_in_message() -> None:
    with pytest.raises(RuleSyntaxError, match=r"\$\.all\[1\]"):
        validate({"all": [{"fact": "a", "eq": 1}, {"fact": "b"}]})
