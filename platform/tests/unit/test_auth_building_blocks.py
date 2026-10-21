import uuid

import pytest

from digital360.core.csrf import is_valid_csrf, new_csrf_token
from digital360.core.errors import AppError
from digital360.core.pagination import (
    PageParams,
    build_page_info,
    decode_cursor,
    encode_cursor,
)
from digital360.core.rate_limit import Limit, RateLimitedError, RateLimiter
from digital360.modules.identity.domain.passwords import password_problems

# ── Mots de passe ──


def test_should_accept_long_uncommon_password() -> None:
    assert password_problems("Maquis-du-Plateau-2026", email="awa@exemple.ci") == []


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("court", "too_short"),
        ("x" * 129, "too_long"),
        ("azerty1234", "too_common"),
        ("MotDePasse1", "too_common"),
        ("awa.kone.ci", "same_as_email"),
        ("aaaaaaaaaaaa", "single_character"),
    ],
)
def test_should_reject_weak_password(password: str, expected: str) -> None:
    assert expected in password_problems(password, email="awa.kone.ci@exemple.ci")


def test_should_report_all_problems_at_once() -> None:
    assert set(password_problems("aaaa", email="x@y.z")) == {"too_short", "single_character"}


# ── CSRF ──


def test_should_validate_matching_csrf_values() -> None:
    token = new_csrf_token()
    assert is_valid_csrf(token, token)


@pytest.mark.parametrize(
    ("cookie", "header"), [(None, "abc"), ("abc", None), ("", ""), ("abc", "abd")]
)
def test_should_reject_missing_or_mismatching_csrf(cookie: str | None, header: str | None) -> None:
    assert not is_valid_csrf(cookie, header)


def test_should_generate_unpredictable_csrf_tokens() -> None:
    assert len({new_csrf_token() for _ in range(100)}) == 100


# ── Limitation de débit ──


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_should_block_after_limit_then_allow_when_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    limit = Limit(max_hits=2, window_seconds=60)

    limiter.hit("login:1.2.3.4", limit)
    limiter.hit("login:1.2.3.4", limit)
    with pytest.raises(RateLimitedError) as error:
        limiter.hit("login:1.2.3.4", limit)
    assert error.value.status == 429
    assert error.value.headers == {"Retry-After": "61"}

    clock.now += 61
    limiter.hit("login:1.2.3.4", limit)


def test_should_count_keys_independently_and_support_reset() -> None:
    limiter = RateLimiter(FakeClock())
    limit = Limit(max_hits=1, window_seconds=60)

    limiter.hit("a", limit)
    limiter.hit("b", limit)
    limiter.reset("a")
    limiter.hit("a", limit)


# ── Pagination ──


def test_should_round_trip_cursor() -> None:
    value = uuid.uuid4()
    assert decode_cursor(encode_cursor(value)) == value


@pytest.mark.parametrize("cursor", ["pas-un-curseur!", "YWJj"])
def test_should_reject_malformed_cursor(cursor: str) -> None:
    with pytest.raises(AppError) as error:
        decode_cursor(cursor)
    assert error.value.code == "VALIDATION_ERROR"


def test_should_detect_next_page_from_extra_item() -> None:
    ids = [uuid.uuid4() for _ in range(3)]

    kept, page = build_page_info(ids, PageParams(limit=2, after=None))

    assert kept == 2
    assert page.has_more is True
    assert page.next_cursor == encode_cursor(ids[1])


def test_should_report_last_page_without_cursor() -> None:
    kept, page = build_page_info([uuid.uuid4()], PageParams(limit=2, after=None))

    assert (kept, page.has_more, page.next_cursor) == (1, False, None)
