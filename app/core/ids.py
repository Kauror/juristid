"""Time-sortable primary keys (UUIDv7, RFC 9562).

Identifiers are generated in Python rather than by the database so that the
value is known before INSERT, fixtures and imports are reproducible, and the
scheme does not depend on one database vendor's function set. Python 3.14 ships
``uuid.uuid7``; until the runtime baseline moves there we generate the same
layout ourselves. See docs/adr/0002-database-and-identifier-strategy.md.
"""

from __future__ import annotations

import os
import time
import uuid

_stdlib_uuid7 = getattr(uuid, "uuid7", None)


def uuid7() -> uuid.UUID:
    """Return a UUID version 7: 48-bit millisecond timestamp, then randomness."""
    if _stdlib_uuid7 is not None:
        return _stdlib_uuid7()

    milliseconds = time.time_ns() // 1_000_000
    random_bytes = os.urandom(10)

    value = bytearray(16)
    value[0:6] = milliseconds.to_bytes(6, "big")
    # Version 7 in the high nibble of byte 6, 12 random bits alongside it.
    value[6] = 0x70 | (random_bytes[0] & 0x0F)
    value[7] = random_bytes[1]
    # RFC 9562 variant (10xxxxxx) in byte 8, 62 random bits after it.
    value[8] = 0x80 | (random_bytes[2] & 0x3F)
    value[9:16] = random_bytes[3:10]

    return uuid.UUID(bytes=bytes(value))


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the embedded millisecond timestamp from a UUIDv7."""
    if value.version != 7:
        raise ValueError("Not a UUID version 7 value.")
    return int.from_bytes(value.bytes[0:6], "big")
