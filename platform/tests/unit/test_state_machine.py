from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

import pytest

from digital360.core.actor import Actor, ActorType
from digital360.core.state_machine import (
    InvalidTransitionError,
    StateMachine,
    TransitionContext,
    TransitionForbiddenError,
    TransitionGuardError,
    transition,
)


class Status(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ON_HOLD = "ON_HOLD"


@dataclass
class Doc:
    missing_items: list[str]


def all_items_received(doc: Doc, _: TransitionContext) -> Sequence[str]:
    return [f"missing:{item}" for item in doc.missing_items]


MACHINE: StateMachine[Status, Doc] = StateMachine(
    "doc",
    [
        transition(
            Status.DRAFT, Status.REVIEW, permissions={"doc:edit"}, guards=[all_items_received]
        ),
        transition(Status.REVIEW, Status.APPROVED, permissions={"doc:approve"}),
        transition(Status.REVIEW, Status.DRAFT, actors=[ActorType.USER, ActorType.SYSTEM]),
        transition([Status.DRAFT, Status.REVIEW], Status.ON_HOLD, actors=[ActorType.SYSTEM]),
    ],
)

EDITOR = TransitionContext(Actor.user(uuid4()), frozenset({"doc:edit"}))
SYSTEM = TransitionContext(Actor.system("test"))
READY = Doc(missing_items=[])


def test_should_resolve_allowed_transition() -> None:
    found = MACHINE.resolve(Status.DRAFT, Status.REVIEW, READY, EDITOR)

    assert found.target is Status.REVIEW


def test_should_raise_409_with_allowed_targets_when_transition_does_not_exist() -> None:
    with pytest.raises(InvalidTransitionError) as error:
        MACHINE.resolve(Status.DRAFT, Status.APPROVED, READY, EDITOR)

    assert error.value.status == 409
    assert error.value.errors is not None
    assert set(error.value.errors[0]["allowed"]) == {"REVIEW", "ON_HOLD"}


def test_should_raise_403_when_user_lacks_permission() -> None:
    with pytest.raises(TransitionForbiddenError):
        MACHINE.resolve(Status.REVIEW, Status.APPROVED, READY, EDITOR)


def test_should_raise_403_when_actor_type_is_not_allowed() -> None:
    with pytest.raises(TransitionForbiddenError):
        MACHINE.resolve(Status.DRAFT, Status.ON_HOLD, READY, EDITOR)


def test_should_not_require_permissions_from_system_actor() -> None:
    assert MACHINE.resolve(Status.REVIEW, Status.DRAFT, READY, SYSTEM).target is Status.DRAFT


def test_should_raise_422_with_all_guard_reasons() -> None:
    doc = Doc(missing_items=["logo", "services"])

    with pytest.raises(TransitionGuardError) as error:
        MACHINE.resolve(Status.DRAFT, Status.REVIEW, doc, EDITOR)

    assert error.value.errors == [{"reason": "missing:logo"}, {"reason": "missing:services"}]


def test_should_expand_multiple_sources_into_separate_transitions() -> None:
    assert MACHINE.resolve(Status.REVIEW, Status.ON_HOLD, READY, SYSTEM).target is Status.ON_HOLD


def test_should_list_available_transitions_for_actor_with_blocking_reasons() -> None:
    doc = Doc(missing_items=["logo"])

    available = MACHINE.available(Status.DRAFT, doc, EDITOR)

    assert [(item.target, item.blocked_by) for item in available] == [
        (Status.REVIEW, ("missing:logo",))
    ]


def test_should_reject_duplicate_transition_declaration() -> None:
    with pytest.raises(ValueError, match="deux fois"):
        StateMachine(
            "dup",
            [transition(Status.DRAFT, Status.REVIEW), transition(Status.DRAFT, Status.REVIEW)],
        )
