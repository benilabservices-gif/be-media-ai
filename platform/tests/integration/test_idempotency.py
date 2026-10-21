import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.errors import AppError
from digital360.core.idempotency import complete, hash_request, reserve
from digital360.core.tenancy import staff_transaction

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

HASH_A = hash_request("POST", "/orders", b'{"product":"DIGITAL_START"}')
HASH_B = hash_request("POST", "/orders", b'{"product":"DIGITAL_GROWTH"}')


def _scope() -> str:
    return f"org:{uuid.uuid4()}:POST /orders"


async def test_should_return_stored_response_when_same_request_is_replayed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = _scope()
    async with staff_transaction(session_factory) as session:
        assert await reserve(session, scope, "key-1", HASH_A) is None
        await complete(session, scope, "key-1", 201, {"order_id": "o-1"})

    async with staff_transaction(session_factory) as session:
        stored = await reserve(session, scope, "key-1", HASH_A)

    assert stored is not None
    assert (stored.status_code, stored.body) == (201, {"order_id": "o-1"})


async def test_should_reject_same_key_with_different_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = _scope()
    async with staff_transaction(session_factory) as session:
        await reserve(session, scope, "key-1", HASH_A)
        await complete(session, scope, "key-1", 201, {})

    with pytest.raises(AppError) as error:
        async with staff_transaction(session_factory) as session:
            await reserve(session, scope, "key-1", HASH_B)

    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


async def test_should_report_conflict_while_first_request_is_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = _scope()
    async with staff_transaction(session_factory) as session:
        await reserve(session, scope, "key-1", HASH_A)

    with pytest.raises(AppError) as error:
        async with staff_transaction(session_factory) as session:
            await reserve(session, scope, "key-1", HASH_A)

    assert error.value.status == 409


async def test_should_release_key_when_processing_transaction_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = _scope()
    with pytest.raises(RuntimeError):
        async with staff_transaction(session_factory) as session:
            await reserve(session, scope, "key-1", HASH_A)
            raise RuntimeError("paiement refusé")

    async with staff_transaction(session_factory) as session:
        assert await reserve(session, scope, "key-1", HASH_A) is None


async def test_should_isolate_same_key_between_scopes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with staff_transaction(session_factory) as session:
        assert await reserve(session, _scope(), "shared-key", HASH_A) is None
        assert await reserve(session, _scope(), "shared-key", HASH_A) is None


@pytest.mark.parametrize("key", ["", "k" * 256])
async def test_should_reject_invalid_key_length(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> None:
    with pytest.raises(AppError) as error:
        async with staff_transaction(session_factory) as session:
            await reserve(session, _scope(), key, HASH_A)

    assert error.value.code == "VALIDATION_ERROR"
