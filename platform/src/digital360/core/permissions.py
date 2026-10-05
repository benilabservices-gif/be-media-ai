"""RBAC : rôles → permissions (ARCHITECTURE.md §5).

Le code ne teste jamais un rôle, toujours une permission. Ce fichier est le seul endroit
où la correspondance est définie.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class Permission(StrEnum):
    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_UPDATE = "organization:update"
    MEMBERSHIP_MANAGE = "membership:manage"
    DIAGNOSTIC_READ = "diagnostic:read"
    PASSPORT_READ = "passport:read"
    PASSPORT_UPDATE = "passport:update"
    ORDER_CREATE = "order:create"
    INVOICE_READ = "invoice:read"
    PAYMENT_REFUND = "payment:refund"
    SUBSCRIPTION_MANAGE = "subscription:manage"
    WEBSITE_PROJECT_READ = "website_project:read"
    WEBSITE_PROJECT_TRANSITION = "website_project:transition"
    WEBSITE_PROJECT_CLIENT_REVIEW = "website_project:client_review"
    CHECKLIST_SUBMIT = "checklist:submit"
    DEPLOYMENT_EXECUTE = "deployment:execute"
    TASK_MANAGE = "task:manage"
    STAFF_MANAGE = "staff:manage"
    AUDIT_READ = "audit:read"
    CONFIG_MANAGE = "config:manage"
    # Closer 3.0 : verser les commissions, suspendre un closer, rattacher un client
    CLOSER_MANAGE = "closer:manage"
    # Suppression définitive d'une entreprise et de toutes ses données : ADMIN uniquement
    ORGANIZATION_DELETE = "organization:delete"


class ClientRole(StrEnum):
    CLIENT_OWNER = "CLIENT_OWNER"
    CLIENT_MEMBER = "CLIENT_MEMBER"


class StaffRole(StrEnum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    CONTENT_MANAGER = "CONTENT_MANAGER"
    DEVELOPER = "DEVELOPER"
    FINANCE = "FINANCE"


P = Permission

_CLIENT_MEMBER = frozenset(
    {
        P.ORGANIZATION_READ,
        P.DIAGNOSTIC_READ,
        P.PASSPORT_READ,
        P.WEBSITE_PROJECT_READ,
        P.CHECKLIST_SUBMIT,
    }
)

CLIENT_ROLE_PERMISSIONS: Mapping[ClientRole, frozenset[Permission]] = {
    ClientRole.CLIENT_MEMBER: _CLIENT_MEMBER,
    ClientRole.CLIENT_OWNER: _CLIENT_MEMBER
    | {
        P.ORGANIZATION_UPDATE,
        P.MEMBERSHIP_MANAGE,
        P.ORDER_CREATE,
        P.INVOICE_READ,
        P.SUBSCRIPTION_MANAGE,
        P.WEBSITE_PROJECT_CLIENT_REVIEW,
    },
}

_STAFF_BASE = frozenset({P.ORGANIZATION_READ})

STAFF_ROLE_PERMISSIONS: Mapping[StaffRole, frozenset[Permission]] = {
    StaffRole.ADMIN: frozenset(Permission),
    StaffRole.MANAGER: _STAFF_BASE
    | {
        P.ORGANIZATION_UPDATE,
        P.DIAGNOSTIC_READ,
        P.PASSPORT_READ,
        P.PASSPORT_UPDATE,
        P.ORDER_CREATE,
        P.INVOICE_READ,
        P.SUBSCRIPTION_MANAGE,
        P.WEBSITE_PROJECT_READ,
        P.WEBSITE_PROJECT_TRANSITION,
        P.CHECKLIST_SUBMIT,
        P.TASK_MANAGE,
        P.CLOSER_MANAGE,
    },
    StaffRole.CONTENT_MANAGER: _STAFF_BASE
    | {
        P.DIAGNOSTIC_READ,
        P.PASSPORT_READ,
        P.PASSPORT_UPDATE,
        P.WEBSITE_PROJECT_READ,
        P.CHECKLIST_SUBMIT,
        P.TASK_MANAGE,
    },
    StaffRole.DEVELOPER: _STAFF_BASE
    | {
        P.DIAGNOSTIC_READ,
        P.PASSPORT_READ,
        P.PASSPORT_UPDATE,
        P.WEBSITE_PROJECT_READ,
        P.WEBSITE_PROJECT_TRANSITION,
        P.DEPLOYMENT_EXECUTE,
        P.TASK_MANAGE,
    },
    StaffRole.FINANCE: _STAFF_BASE
    | {
        P.INVOICE_READ,
        P.PAYMENT_REFUND,
        P.SUBSCRIPTION_MANAGE,
        P.CLOSER_MANAGE,
    },
}


@dataclass(frozen=True)
class Principal:
    """Utilisateur authentifié, avec ses rôles staff et ses rôles par organisation."""

    user_id: UUID
    staff_roles: frozenset[StaffRole] = frozenset()
    memberships: Mapping[UUID, ClientRole] = field(default_factory=dict)

    @property
    def is_staff(self) -> bool:
        return bool(self.staff_roles)

    def staff_permissions(self) -> frozenset[Permission]:
        return frozenset().union(*(STAFF_ROLE_PERMISSIONS[role] for role in self.staff_roles))

    def client_permissions(self, organization_id: UUID) -> frozenset[Permission]:
        role = self.memberships.get(organization_id)
        return CLIENT_ROLE_PERMISSIONS[role] if role else frozenset()

    def can(self, permission: Permission, organization_id: UUID | None = None) -> bool:
        """Staff : permission globale. Client : permission dans l'organisation donnée."""
        if permission in self.staff_permissions():
            return True
        return organization_id is not None and permission in self.client_permissions(
            organization_id
        )
