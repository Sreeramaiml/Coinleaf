"""Row fingerprints for idempotent re-import (FR-7, ARCH §7.4).

fingerprint = sha256(account | date | amount | normalised description | running balance |
occurrence index). The occurrence index numbers identical (date, amount, description,
balance) rows within one file, so two genuine identical purchases on the same day both
survive, while the same row seen again in an overlapping statement maps to the same
fingerprint. Assumption: statements cover whole days.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence

from finassist.core.textnorm import norm_description
from finassist.ingest.models import ParsedRow

__all__ = ["fingerprints"]


def fingerprints(account_id: str, rows: Sequence[ParsedRow]) -> list[str]:
    seen: Counter[tuple[str, ...]] = Counter()
    out = []
    for r in rows:
        base = (
            account_id,
            r.txn_date.isoformat(),
            str(r.amount_minor),
            norm_description(r.raw_description),
            "" if r.balance_minor is None else str(r.balance_minor),
        )
        occurrence = seen[base]
        seen[base] += 1
        out.append(hashlib.sha256("|".join((*base, str(occurrence))).encode()).hexdigest())
    return out
