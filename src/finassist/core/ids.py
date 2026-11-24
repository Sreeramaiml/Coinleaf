"""Identifiers: ULIDs (time-sortable, safe to show) and short query ids."""

from __future__ import annotations

import os
import time

__all__ = ["new_id", "new_query_id"]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_CROCKFORD[rem])
    return "".join(reversed(out))


def new_id() -> str:
    """A 26-character ULID: 48-bit millisecond timestamp + 80 random bits."""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, 10) + _encode(rnd, 16)


def new_query_id() -> str:
    """Short handle shown to the owner, e.g. q_7f3a9c."""
    return "q_" + os.urandom(3).hex()
