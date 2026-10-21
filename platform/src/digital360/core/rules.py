"""Évaluateur de conditions JSON (ARCHITECTURE.md §7.3).

Sert aux règles de recommandation, aux `visible_if` du questionnaire et, en V3, aux
automatisations. Grammaire :

    {"all": [cond, ...]}   toutes vraies (liste vide : vrai)
    {"any": [cond, ...]}   au moins une vraie (liste vide : faux)
    {"not": cond}
    {"fact": "nom", "<op>": valeur}   op ∈ eq, ne, gt, gte, lt, lte, in, contains, exists

Un fait absent (ou None) rend toute comparaison fausse, sauf `exists`. Des types
incompatibles (ex. comparer un texte à un nombre) donnent faux, jamais une exception :
une règle mal écrite ne doit pas faire échouer un diagnostic. `validate()` détecte en
revanche les erreurs de syntaxe au moment de publier une configuration.

Aucun `eval` : seule cette grammaire est interprétée.
"""

import operator
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Condition = Mapping[str, Any]
Facts = Mapping[str, Any]

_LOGICAL_KEYS = frozenset({"all", "any", "not"})


class RuleSyntaxError(ValueError):
    pass


def _is_number(value: Any) -> bool:
    # bool est une sous-classe d'int en Python : True > 0 ne doit pas être une comparaison valide
    return isinstance(value, int | float) and not isinstance(value, bool)


def _comparable(left: Any, right: Any) -> bool:
    return (_is_number(left) and _is_number(right)) or (
        isinstance(left, str) and isinstance(right, str)
    )


def _equals(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return bool(left == right)


def _ordering(compare: Callable[[Any, Any], bool]) -> Callable[[Any, Any], bool]:
    def apply(left: Any, right: Any) -> bool:
        return _comparable(left, right) and compare(left, right)

    return apply


def _in(left: Any, right: Any) -> bool:
    return isinstance(right, Sequence) and not isinstance(right, str) and left in right


def _contains(left: Any, right: Any) -> bool:
    if isinstance(left, str):
        return isinstance(right, str) and right in left
    return isinstance(left, Sequence) and right in left


_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": _equals,
    "ne": lambda left, right: not _equals(left, right),
    "gt": _ordering(operator.gt),
    "gte": _ordering(operator.ge),
    "lt": _ordering(operator.lt),
    "lte": _ordering(operator.le),
    "in": _in,
    "contains": _contains,
}


def evaluate(condition: Condition, facts: Facts) -> bool:
    if "all" in condition:
        return all(evaluate(sub, facts) for sub in condition["all"])
    if "any" in condition:
        return any(evaluate(sub, facts) for sub in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], facts)

    op, expected = _single_operator(condition)
    value = facts.get(condition["fact"])
    if op == "exists":
        return (value is not None) is bool(expected)
    if value is None:
        return False
    return _OPERATORS[op](value, expected)


def validate(condition: Any, path: str = "$") -> None:
    """Lève RuleSyntaxError si la condition ne respecte pas la grammaire."""
    if not isinstance(condition, Mapping):
        raise RuleSyntaxError(f"{path} : une condition doit être un objet")

    logical = _LOGICAL_KEYS & condition.keys()
    if logical:
        if len(condition) != 1:
            raise RuleSyntaxError(f"{path} : '{next(iter(logical))}' doit être seul dans l'objet")
        key = next(iter(logical))
        if key == "not":
            validate(condition["not"], f"{path}.not")
            return
        children = condition[key]
        if not isinstance(children, list):
            raise RuleSyntaxError(f"{path}.{key} : une liste de conditions est attendue")
        for index, child in enumerate(children):
            validate(child, f"{path}.{key}[{index}]")
        return

    if not isinstance(condition.get("fact"), str) or not condition["fact"]:
        raise RuleSyntaxError(f"{path} : 'fact' (nom du fait) est obligatoire")
    op, expected = _single_operator(condition, path)
    if op == "exists" and not isinstance(expected, bool):
        raise RuleSyntaxError(f"{path} : 'exists' attend true ou false")
    if op == "in" and not isinstance(expected, list):
        raise RuleSyntaxError(f"{path} : 'in' attend une liste")


def _single_operator(condition: Condition, path: str = "$") -> tuple[str, Any]:
    operators = [key for key in condition if key != "fact"]
    if len(operators) != 1 or (operators[0] not in _OPERATORS and operators[0] != "exists"):
        raise RuleSyntaxError(
            f"{path} : un seul opérateur parmi {sorted([*_OPERATORS, 'exists'])} est attendu"
        )
    return operators[0], condition[operators[0]]
