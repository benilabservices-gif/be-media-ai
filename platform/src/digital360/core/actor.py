from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    PROVIDER = "PROVIDER"


@dataclass(frozen=True)
class Actor:
    """Auteur d'une action : utilisateur, tâche système ou fournisseur externe (webhook)."""

    type: ActorType
    user_id: UUID | None = None
    # Nom du job ou code du fournisseur, pour savoir « quel » système a agi
    label: str | None = None

    @classmethod
    def user(cls, user_id: UUID) -> "Actor":
        return cls(ActorType.USER, user_id=user_id)

    @classmethod
    def system(cls, label: str) -> "Actor":
        return cls(ActorType.SYSTEM, label=label)

    @classmethod
    def provider(cls, code: str) -> "Actor":
        return cls(ActorType.PROVIDER, label=code)
