import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel

from digital360.core.actor import Actor
from digital360.core.csrf import require_csrf
from digital360.core.errors import AppError
from digital360.core.pagination import PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.core.rate_limit import Limit, RateLimiter
from digital360.modules.diagnostics.api.schemas import (
    ActionPlanOut,
    CompleteDiagnosticRequest,
    DiagnosticHistory,
    DiagnosticPage,
    DiagnosticResultOut,
    DiagnosticSummaryOut,
    PassportItemOut,
    PassportOut,
    PlanItemOut,
    PlanItemUpdate,
    QuestionnaireOut,
    SaveAnswersRequest,
    SaveAnswersResponse,
    ScoreOut,
    StartDiagnosticRequest,
    StartDiagnosticResponse,
)
from digital360.modules.diagnostics.application.service import (
    DiagnosticResult,
    DiagnosticService,
)
from digital360.modules.diagnostics.infrastructure.models import DiagnosticStatus, PlanItemStatus
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    get_client_info,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.identity.application.auth_service import ClientInfo

START_PER_IP = Limit(max_hits=30, window_seconds=60 * 60)

public_router = APIRouter(prefix="/public", tags=["diagnostic"])
router = APIRouter(tags=["diagnostic"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_diagnostic_service(request: Request) -> DiagnosticService:
    service: DiagnosticService = request.app.state.diagnostic_service
    return service


Service = Annotated[DiagnosticService, Depends(get_diagnostic_service)]


def diagnostic_token(
    x_diagnostic_token: Annotated[str | None, Header(max_length=100)] = None,
) -> str:
    """Jeton du diagnostic, dans un en-tête pour ne jamais apparaître dans une URL ou un log."""
    if not x_diagnostic_token:
        raise AppError("NOT_FOUND", "Diagnostic introuvable.", status=404)
    return x_diagnostic_token


Token = Annotated[str, Depends(diagnostic_token)]


def to_result_out(result: DiagnosticResult) -> DiagnosticResultOut:
    return DiagnosticResultOut(
        id=result.id,
        status=result.status,
        completed_at=result.completed_at,
        score=ScoreOut.model_validate(result.score),
        passport=PassportOut(
            items=[PassportItemOut.model_validate(item) for item in result.passport_items]
        ),
        action_plan=ActionPlanOut(
            items=[PlanItemOut.model_validate(asdict(item)) for item in result.plan_items]
        ),
    )


# ── Public (sans compte) ──


@public_router.get("/questionnaire", response_model=QuestionnaireOut)
async def get_questionnaire(service: Service) -> QuestionnaireOut:
    return QuestionnaireOut.from_definition(await service.published_questionnaire())


@public_router.post(
    "/diagnostics", status_code=status.HTTP_201_CREATED, response_model=StartDiagnosticResponse
)
async def start_diagnostic(
    request: Request,
    service: Service,
    client: Annotated[ClientInfo, Depends(get_client_info)],
    body: StartDiagnosticRequest | None = None,
) -> StartDiagnosticResponse:
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.hit(f"diagnostic:start:{client.ip}", START_PER_IP)
    source = body.source.model_dump(exclude_none=True) if body and body.source else None
    started = await service.start(source or None)
    return StartDiagnosticResponse(
        id=started.id, token=started.token, questionnaire_version=started.questionnaire_version
    )


@public_router.put("/diagnostics/{diagnostic_id}/answers", response_model=SaveAnswersResponse)
async def save_answers(
    diagnostic_id: uuid.UUID, body: SaveAnswersRequest, token: Token, service: Service
) -> SaveAnswersResponse:
    saved = await service.save_answers(diagnostic_id, token, body.answers)
    return SaveAnswersResponse(answers=saved.answers, missing_required=saved.missing_required)


@public_router.post("/diagnostics/{diagnostic_id}/complete", response_model=DiagnosticResultOut)
async def complete_diagnostic(
    diagnostic_id: uuid.UUID,
    body: CompleteDiagnosticRequest,
    token: Token,
    service: Service,
    client: Annotated[ClientInfo, Depends(get_client_info)],
) -> DiagnosticResultOut:
    result = await service.complete(
        diagnostic_id,
        token,
        privacy=body.consents.privacy,
        marketing_email=body.consents.marketing_email,
        marketing_whatsapp=body.consents.marketing_whatsapp,
        ip=client.ip,
    )
    return to_result_out(result)


@public_router.get("/diagnostics/{diagnostic_id}/result", response_model=DiagnosticResultOut)
async def get_diagnostic_result(
    diagnostic_id: uuid.UUID, token: Token, service: Service
) -> DiagnosticResultOut:
    return to_result_out(await service.result(diagnostic_id, token))


# ── Espace client ──

ReadAccess = Annotated[
    OrganizationAccess, Depends(require_org_permission(Permission.DIAGNOSTIC_READ))
]


@router.get("/orgs/{org_id}/diagnostics", response_model=DiagnosticHistory)
async def list_organization_diagnostics(access: ReadAccess, service: Service) -> DiagnosticHistory:
    return DiagnosticHistory(
        data=[to_result_out(result) for result in await service.history(access.tenant)]
    )


class ClaimDiagnosticRequest(BaseModel):
    diagnostic_id: uuid.UUID


@router.post(
    "/orgs/{org_id}/diagnostics/claim",
    response_model=DiagnosticResultOut,
    dependencies=[Depends(require_csrf)],
)
async def claim_diagnostic(
    body: ClaimDiagnosticRequest,
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_UPDATE))
    ],
    service: Service,
    token: Token,
) -> DiagnosticResultOut:
    """Rattache à cette entreprise un diagnostic fait avant la connexion (compte existant).

    Jeton du diagnostic dans l'en-tête X-Diagnostic-Token, comme sur les routes publiques.
    """
    result = await service.claim_for_organization(
        access.tenant, Actor.user(access.principal.user_id), body.diagnostic_id, token
    )
    return to_result_out(result)


@router.get("/orgs/{org_id}/action-plan", response_model=ActionPlanOut)
async def get_action_plan(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.PASSPORT_READ))
    ],
    service: Service,
) -> ActionPlanOut:
    latest = await service.latest_for_organization(access.tenant)
    if latest is None:
        return ActionPlanOut(items=[])
    return to_result_out(latest).action_plan


@router.patch(
    "/orgs/{org_id}/action-plan/items/{item_id}",
    response_model=PlanItemOut,
    dependencies=[Depends(require_csrf)],
)
async def update_action_plan_item(
    item_id: uuid.UUID,
    body: PlanItemUpdate,
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_UPDATE))
    ],
    service: Service,
) -> PlanItemOut:
    item = await service.update_plan_item(
        access.tenant, Actor.user(access.principal.user_id), item_id, PlanItemStatus(body.status)
    )
    return PlanItemOut.model_validate(asdict(item))


# ── Admin ──


@admin_router.get("/diagnostics", response_model=DiagnosticPage)
async def admin_list_diagnostics(
    _: Annotated[Principal, Depends(require_staff_permission(Permission.DIAGNOSTIC_READ))],
    service: Service,
    params: Annotated[PageParams, Depends(page_params)],
    status_filter: Annotated[DiagnosticStatus | None, Query(alias="status")] = None,
) -> DiagnosticPage:
    summaries, page = await service.admin_list(params, status=status_filter)
    return DiagnosticPage(
        data=[DiagnosticSummaryOut.model_validate(asdict(item)) for item in summaries], page=page
    )
