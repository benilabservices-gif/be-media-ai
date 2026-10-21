"""GET /me : profil, rôles et organisations de l'utilisateur connecté.

Route de composition : elle assemble identity (utilisateur, droits) et organizations (noms).
C'est la première requête du frontend après la connexion.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from digital360.core.permissions import ClientRole, Permission, StaffRole
from digital360.modules.identity.api.dependencies import get_authenticated_user
from digital360.modules.identity.api.schemas import UserOut
from digital360.modules.identity.application.auth_service import AuthenticatedUser
from digital360.modules.organizations.api.routes import Service

router = APIRouter(tags=["auth"])


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: ClientRole
    # Permissions effectives : l'UI masque les actions non permises (le serveur revérifie)
    permissions: list[Permission]


class MeResponse(BaseModel):
    user: UserOut
    staff_roles: list[StaffRole]
    staff_permissions: list[Permission]
    memberships: list[MembershipOut]


@router.get("/me", response_model=MeResponse)
async def get_me(
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    organizations: Service,
) -> MeResponse:
    principal = authenticated.principal
    names = await organizations.names_for_user(principal.user_id)
    memberships = [
        MembershipOut(
            organization_id=org_id,
            organization_name=names[org_id],
            role=role,
            permissions=sorted(principal.client_permissions(org_id)),
        )
        for org_id, role in principal.memberships.items()
        if org_id in names
    ]
    return MeResponse(
        user=UserOut.model_validate(authenticated.user),
        staff_roles=sorted(principal.staff_roles),
        staff_permissions=sorted(principal.staff_permissions()),
        memberships=sorted(memberships, key=lambda item: item.organization_name.lower()),
    )
