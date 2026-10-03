"""Machine à états générique et pure (ARCHITECTURE.md §9.1).

Elle décide si une transition est permise. La persistance (verrou optimiste, historique,
audit, événement) est dans `core.workflow`.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from digital360.core.actor import Actor, ActorType
from digital360.core.errors import AppError


@dataclass(frozen=True)
class TransitionContext:
    actor: Actor
    # Permissions effectives de l'acteur (vide pour SYSTEM et PROVIDER)
    permissions: frozenset[str] = frozenset()


# Une garde renvoie la liste des raisons qui bloquent la transition (vide = autorisée)
type Guard[E] = Callable[[E, TransitionContext], Sequence[str]]


@dataclass(frozen=True)
class Transition[S: StrEnum, E]:
    sources: frozenset[S]
    target: S
    actor_types: frozenset[ActorType]
    # Au moins une de ces permissions est exigée d'un acteur USER (vide = aucune exigence)
    permissions: frozenset[str] = frozenset()
    guards: tuple[Guard[E], ...] = field(default=())

    def allows(self, context: TransitionContext) -> bool:
        if context.actor.type not in self.actor_types:
            return False
        if context.actor.type is ActorType.USER and self.permissions:
            return bool(self.permissions & context.permissions)
        return True

    def blocking_reasons(self, entity: E, context: TransitionContext) -> tuple[str, ...]:
        return tuple(reason for guard in self.guards for reason in guard(entity, context))


def transition[S: StrEnum, E](
    sources: S | Iterable[S],
    target: S,
    *,
    actors: Iterable[ActorType] = (ActorType.USER,),
    permissions: Iterable[str] = (),
    guards: Iterable[Guard[E]] = (),
) -> Transition[S, E]:
    source_set = frozenset([sources]) if isinstance(sources, StrEnum) else frozenset(sources)
    return Transition(
        sources=source_set,
        target=target,
        actor_types=frozenset(actors),
        permissions=frozenset(permissions),
        guards=tuple(guards),
    )


class InvalidTransitionError(AppError):
    def __init__(self, machine: str, current: StrEnum, target: StrEnum, allowed: list[str]):
        super().__init__(
            "INVALID_TRANSITION",
            f"Passage de {current} à {target} impossible.",
            status=409,
            title="Transition invalide",
            errors=[{"machine": machine, "from": str(current), "allowed": allowed}],
        )


class TransitionForbiddenError(AppError):
    def __init__(self, current: StrEnum, target: StrEnum):
        super().__init__(
            "FORBIDDEN",
            f"Vous n'êtes pas autorisé à passer de {current} à {target}.",
            status=403,
        )


class TransitionGuardError(AppError):
    def __init__(self, reasons: Sequence[str]):
        super().__init__(
            "TRANSITION_GUARD_FAILED",
            "Des conditions préalables ne sont pas remplies.",
            status=422,
            title="Transition impossible",
            errors=[{"reason": reason} for reason in reasons],
        )


@dataclass(frozen=True)
class AvailableTransition[S: StrEnum]:
    target: S
    # Raisons des gardes non satisfaites : l'UI peut afficher le bouton grisé avec l'explication
    blocked_by: tuple[str, ...]


class StateMachine[S: StrEnum, E]:
    def __init__(self, name: str, transitions: Iterable[Transition[S, E]]) -> None:
        self.name = name
        self._index: dict[tuple[S, S], Transition[S, E]] = {}
        for item in transitions:
            for source in item.sources:
                key = (source, item.target)
                if key in self._index:
                    raise ValueError(
                        f"{name} : transition {source} → {item.target} déclarée deux fois"
                    )
                self._index[key] = item

    def targets_from(self, current: S) -> list[S]:
        return [target for (source, target) in self._index if source == current]

    def resolve(
        self, current: S, target: S, entity: E, context: TransitionContext
    ) -> Transition[S, E]:
        """Renvoie la transition si elle est permise, sinon lève l'erreur métier adaptée."""
        found = self._index.get((current, target))
        if found is None:
            allowed = [str(item) for item in self.targets_from(current)]
            raise InvalidTransitionError(self.name, current, target, allowed)
        if not found.allows(context):
            raise TransitionForbiddenError(current, target)
        reasons = found.blocking_reasons(entity, context)
        if reasons:
            raise TransitionGuardError(reasons)
        return found

    def available(
        self, current: S, entity: E, context: TransitionContext
    ) -> list[AvailableTransition[S]]:
        """Transitions que cet acteur peut déclencher depuis l'état courant."""
        return [
            AvailableTransition(target, item.blocking_reasons(entity, context))
            for (source, target), item in self._index.items()
            if source == current and item.allows(context)
        ]
