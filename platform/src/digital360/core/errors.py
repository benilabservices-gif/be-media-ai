"""Erreurs métier et réponses au format RFC 9457 (application/problem+json).

Le champ `code` est stable : le frontend s'appuie dessus, jamais sur `title` ou `detail`.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"
ERROR_DOCS_BASE_URL = "https://docs.benilab360.com/errors/"

logger = logging.getLogger("digital360.errors")

# Codes stables des erreurs HTTP génériques levées par le framework
_HTTP_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "RATE_LIMITED",
}


class AppError(Exception):
    """Erreur métier attendue, convertie en problem+json par le gestionnaire global."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status: int = 400,
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.title = title or HTTPStatus(status).phrase
        self.errors = errors


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": ERROR_DOCS_BASE_URL + code.lower().replace("_", "-"),
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE)


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 — garanti par l'enregistrement du handler
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        errors=exc.errors,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "reason": error["type"],
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=400,
        code="VALIDATION_ERROR",
        title="Requête invalide",
        detail="Certains champs sont invalides.",
        errors=errors,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    status = exc.status_code
    return problem_response(
        request,
        status=status,
        code=_HTTP_STATUS_CODES.get(status, "HTTP_ERROR"),
        title=HTTPStatus(status).phrase,
        detail=str(exc.detail),
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Le détail technique reste dans les logs ; le client ne reçoit que le request_id
    logger.exception("erreur inattendue", exc_info=exc)
    return problem_response(
        request,
        status=500,
        code="INTERNAL_ERROR",
        title="Erreur interne",
        detail="Une erreur inattendue est survenue. L'équipe a été informée.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
