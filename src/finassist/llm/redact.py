"""Outbound redaction, P0 baseline (NFR-3, ARCH §14.3).

Applied to every string in every tool result before it is sent to a hosted model.
P0 removes account and card numbers, masked account numbers, IFSC codes, PAN-shaped
values, emails, UPI VPAs, phone numbers and long reference numbers. Accounts are
already referred to by nickname in tool output. P2 adds pseudonyms for person names
parsed from UPI/NEFT narrations.

Only strings are rewritten: integer amounts (`minor`) are figures the answer needs.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["redact_obj", "redact_text"]

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b[\w.-]{2,}@[a-zA-Z]{2,}\b"), "[VPA]"),
    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[IFSC]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN]"),
    (re.compile(r"(?:[Xx*]{2,}[\s-]?)+\d{3,4}\b"), "[ACCT]"),
    (re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{1,7}\b"), "[NUM]"),  # card-number shapes
    (re.compile(r"(?:\+91[\s-]?)\d{5}[\s-]?\d{5}\b"), "[PHONE]"),
    (re.compile(r"\d{8,}"), "[NUM]"),  # account numbers, UTR/RRN/ARN references, phone numbers
]


def redact_text(s: str) -> tuple[str, int]:
    n = 0
    for pattern, repl in _PATTERNS:
        s, k = pattern.subn(repl, s)
        n += k
    return s, n


def redact_obj(obj: Any) -> tuple[Any, int]:
    """Recursively redact every string value (dict keys are left alone)."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        total = 0
        out = {}
        for k, v in obj.items():
            out[k], n = redact_obj(v)
            total += n
        return out, total
    if isinstance(obj, (list, tuple)):
        total = 0
        items = []
        for v in obj:
            r, n = redact_obj(v)
            items.append(r)
            total += n
        return items, total
    return obj, 0
