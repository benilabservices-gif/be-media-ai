from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from digital360.core.db import check_database

ReadinessCheck = Callable[[], Awaitable[dict[str, str]]]

router = APIRouter(prefix="/health", tags=["system"])


def get_readiness_checks(request: Request) -> dict[str, ReadinessCheck]:
    """Vérifications exécutées par la sonde de readiness (surchargées dans les tests)."""
    engine = request.app.state.engine
    alembic_ini_path = request.app.state.settings.alembic_ini_path
    return {"database": lambda: check_database(engine, alembic_ini_path)}


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Le processus répond. Ne dépend d'aucun service externe."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    checks: Annotated[dict[str, ReadinessCheck], Depends(get_readiness_checks)],
) -> JSONResponse:
    """L'instance peut recevoir du trafic : base joignable et migrations à jour."""
    results = {name: await check() for name, check in checks.items()}
    is_ready = all(result["status"] == "ok" for result in results.values())
    return JSONResponse(
        {"status": "ok" if is_ready else "error", "checks": results},
        status_code=200 if is_ready else 503,
    )
