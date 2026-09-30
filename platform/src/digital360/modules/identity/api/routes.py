from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from digital360.core.config import Settings
from digital360.core.csrf import CSRF_COOKIE_NAME, new_csrf_token, require_csrf
from digital360.core.rate_limit import Limit, RateLimiter
from digital360.modules.identity.api.dependencies import (
    SESSION_COOKIE_NAME,
    get_auth_service,
    get_authenticated_user,
    get_client_info,
)
from digital360.modules.identity.api.schemas import (
    CsrfResponse,
    LoginRequest,
    ProfileUpdate,
    RegisterRequest,
    SessionResponse,
    UserOut,
)
from digital360.modules.identity.application.auth_service import (
    AuthenticatedUser,
    AuthService,
    ClientInfo,
    normalize_email,
)

LOGIN_PER_IP = Limit(max_hits=20, window_seconds=60)
LOGIN_PER_EMAIL = Limit(max_hits=5, window_seconds=15 * 60)
REGISTER_PER_IP = Limit(max_hits=10, window_seconds=60 * 60)

router = APIRouter(tags=["auth"])

Auth = Annotated[AuthService, Depends(get_auth_service)]
Client = Annotated[ClientInfo, Depends(get_client_info)]


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def _set_csrf_cookie(response: Response, settings: Settings) -> str:
    token = new_csrf_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=settings.session_absolute_days * 86400,
        domain=settings.session_cookie_domain,
        secure=settings.secure_cookies,
        httponly=False,  # lu par le JavaScript du frontend pour remplir X-CSRF-Token
        samesite="lax",
    )
    return token


def _set_session_cookie(
    response: Response, settings: Settings, token: str, expires_at: datetime
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        expires=expires_at,
        domain=settings.session_cookie_domain,
        secure=settings.secure_cookies,
        httponly=True,  # inaccessible au JavaScript : un XSS ne peut pas voler la session
        samesite="lax",
    )


@router.get("/auth/csrf", response_model=CsrfResponse)
async def get_csrf_token(request: Request, response: Response) -> CsrfResponse:
    """Pose le cookie `csrf_token` et renvoie sa valeur. À appeler au chargement du frontend."""
    return CsrfResponse(csrf_token=_set_csrf_cookie(response, _settings(request)))


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
    dependencies=[Depends(require_csrf)],
)
async def register(
    body: RegisterRequest, request: Request, response: Response, auth: Auth, client: Client
) -> SessionResponse:
    _limiter(request).hit(f"register:ip:{client.ip}", REGISTER_PER_IP)
    user, issued = await auth.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
        client=client,
    )
    settings = _settings(request)
    _set_session_cookie(response, settings, issued.token, issued.absolute_expires_at)
    return SessionResponse(
        user=UserOut.model_validate(user), csrf_token=_set_csrf_cookie(response, settings)
    )


@router.post("/auth/login", response_model=SessionResponse, dependencies=[Depends(require_csrf)])
async def login(
    body: LoginRequest, request: Request, response: Response, auth: Auth, client: Client
) -> SessionResponse:
    limiter = _limiter(request)
    email_key = f"login:email:{normalize_email(body.email)}"
    limiter.hit(f"login:ip:{client.ip}", LOGIN_PER_IP)
    limiter.hit(email_key, LOGIN_PER_EMAIL)
    user, issued = await auth.login(email=body.email, password=body.password, client=client)
    limiter.reset(email_key)
    settings = _settings(request)
    _set_session_cookie(response, settings, issued.token, issued.absolute_expires_at)
    return SessionResponse(
        user=UserOut.model_validate(user), csrf_token=_set_csrf_cookie(response, settings)
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def logout(request: Request, response: Response, auth: Auth) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await auth.logout(token)
    settings = _settings(request)
    response.delete_cookie(SESSION_COOKIE_NAME, domain=settings.session_cookie_domain)


@router.patch("/me", response_model=UserOut, dependencies=[Depends(require_csrf)])
async def update_profile(
    body: ProfileUpdate,
    auth: Auth,
    client: Client,
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> UserOut:
    user = await auth.update_profile(
        authenticated.user.id, full_name=body.full_name, phone=body.phone, client=client
    )
    return UserOut.model_validate(user)
