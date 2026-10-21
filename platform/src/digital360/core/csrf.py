"""Protection CSRF par double cookie (« double submit »).

Le serveur pose un cookie `csrf_token` lisible par le JavaScript du frontend ; toute
requête modifiante doit renvoyer la même valeur dans l'en-tête `X-CSRF-Token`. Un site
tiers peut faire envoyer les cookies, mais pas les lire, donc pas forger l'en-tête.
C'est le mécanisme qu'utilise déjà le client de Kilo (digital360/js/api/client.js).
"""

import hmac
import secrets

from fastapi import Request

from digital360.core.errors import AppError

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def is_valid_csrf(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


async def require_csrf(request: Request) -> None:
    """Dépendance FastAPI à placer sur les routes qui utilisent les cookies de session."""
    if request.method in SAFE_METHODS:
        return
    if not is_valid_csrf(
        request.cookies.get(CSRF_COOKIE_NAME), request.headers.get(CSRF_HEADER_NAME)
    ):
        raise AppError(
            "CSRF_FAILED",
            "Jeton CSRF absent ou invalide. Appelez GET /api/v1/auth/csrf puis réessayez.",
            status=403,
        )
