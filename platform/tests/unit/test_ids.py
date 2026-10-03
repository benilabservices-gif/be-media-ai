import time

from digital360.core.ids import uuid7


def test_should_generate_version_7_rfc_variant_uuid() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_should_embed_current_millisecond_timestamp() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000

    assert before <= value.int >> 80 <= after


def test_should_sort_by_creation_time_across_milliseconds() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()

    assert first < second


def test_should_be_unique() -> None:
    assert len({uuid7() for _ in range(10_000)}) == 10_000
