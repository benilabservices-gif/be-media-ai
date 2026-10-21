"""Pagination par curseur (ARCHITECTURE.md §13.1).

Les identifiants sont des UUIDv7, donc triés par date de création : le curseur est
simplement le dernier identifiant renvoyé, encodé pour rester opaque côté client.
"""

import base64
import binascii
import uuid
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

from digital360.core.errors import AppError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


class PageInfo(BaseModel):
    next_cursor: str | None
    has_more: bool
    limit: int


class PageParams(BaseModel):
    limit: int
    after: uuid.UUID | None


def encode_cursor(last_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(last_id.bytes).decode().rstrip("=")


def decode_cursor(cursor: str) -> uuid.UUID:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return uuid.UUID(bytes=base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError) as exc:
        raise AppError("VALIDATION_ERROR", "Curseur de pagination invalide.") from exc


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> PageParams:
    """Dépendance FastAPI : `?limit=25&cursor=...`."""
    return PageParams(limit=limit, after=decode_cursor(cursor) if cursor else None)


def build_page_info(ids: list[uuid.UUID], params: PageParams) -> tuple[int, PageInfo]:
    """À appeler avec limit + 1 identifiants : renvoie le nombre d'éléments à garder et PageInfo."""
    has_more = len(ids) > params.limit
    kept = min(len(ids), params.limit)
    next_cursor = encode_cursor(ids[kept - 1]) if has_more else None
    return kept, PageInfo(next_cursor=next_cursor, has_more=has_more, limit=params.limit)
