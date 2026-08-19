"""UUIDv7 primary keys are time-sortable and RFC 9562 shaped."""

from __future__ import annotations

import time

from app.core.ids import uuid7, uuid7_timestamp_ms


def test_uuid7_has_correct_version_and_variant():
    value = uuid7()
    assert value.version == 7
    # RFC 9562 variant is the two high bits 10 of octet 8.
    assert value.bytes[8] >> 6 == 0b10


def test_uuid7_values_are_unique():
    values = {uuid7() for _ in range(1000)}
    assert len(values) == 1000


def test_uuid7_sorts_by_creation_time():
    first = uuid7()
    time.sleep(0.005)
    second = uuid7()
    assert str(first) < str(second)


def test_uuid7_embeds_a_plausible_timestamp():
    before = int(time.time() * 1000)
    value = uuid7()
    after = int(time.time() * 1000)
    embedded = uuid7_timestamp_ms(value)
    assert before - 1000 <= embedded <= after + 1000
