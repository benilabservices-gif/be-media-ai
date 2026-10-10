"""Dépendances FastAPI d'authentification et d'autorisation, réutilisées par tous les modules."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request

from digital360.core.errors import AppError
from digital360.core.permissions import Permission, Principal
from digital360.core.tenancy import TenantContext
from digital360.modules.identity.application.auth_service import (
    AuthenticatedUser,
    AuthService,
    ClientInfo,
)

SESSION_COOKIE_NAME = "d360_session"


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


def get_client_info(request: Request) -> ClientInfo:
    user_agent = request.headers.get("user-agent")
    return ClientInfo(
        ip=request.client.host if request.client else None,
        user_agent=user_agent[:500] if user_agent else None,
    )


async def get_authenticated_user(
    request: Request, service: Annotated[AuthService, Depends(get_auth_service)]
) -> AuthenticatedUser:
    cached: AuthenticatedUser | None = getattr(request.state, "authenticated_user", None)
    if cached is not None:
        return cached
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise AppError("UNAUTHENTICATED", "Connexion requise.", status=401)
    authenticated = await service.authenticate(token)
    if authenticated is None:
        raise AppError("SESSION_EXPIRED", "Votre session a expiré. Reconnectez-vous.", status=401)
    request.state.authenticated_user = authenticated
    return authenticated


async def get_principal(
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> Principal:
    return authenticated.principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_staff_permission(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Routes /admin : permission staff exigée, valable sur toutes les organisations."""

    async def dependency(principal: CurrentPrincipal) -> Principal:
        if permission not in principal.staff_permissions():
            raise AppError("FORBIDDEN", "Accès réservé à l'équipe BENILAB.", status=403)
        return principal

    return dependency


@dataclass(frozen=True)
class OrganizationAccess:
    principal: Principal
    tenant: TenantContext


def require_org_permission(permission: Permission) -> Callable[..., Awaitable[OrganizationAccess]]:
    """Routes /orgs/{org_id} : l'utilisateur doit être membre et avoir la permission.

    Un non-membre reçoit 404 et non 403 : il ne doit pas apprendre que l'organisation existe.
    """

    async def dependency(
        principal: CurrentPrincipal, org_id: Annotated[uuid.UUID, Path()]
    ) -> OrganizationAccess:
        permissions = principal.client_permissions(org_id)
        if not permissions:
            raise AppError("NOT_FOUND", "Organisation introuvable.", status=404)
        if permission not in permissions:
            raise AppError("FORBIDDEN", "Votre rôle ne permet pas cette action.", status=403)
        return OrganizationAccess(
            principal=principal,
            tenant=TenantContext(organization_id=org_id, user_id=principal.user_id),
        )

    return dependency
