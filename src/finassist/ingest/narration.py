"""Narration helpers shared by parsers: payment method and reference extraction."""

from __future__ import annotations

import re

__all__ = ["infer_payment_method", "upi_reference"]

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*UPI[/-]", re.I), "upi"),
    (re.compile(r"\b(NEFT|IMPS|RTGS)\b", re.I), "neft_imps_rtgs"),
    (re.compile(r"\b(ACH|NACH|ECS)\b", re.I), "nach"),
    (re.compile(r"\b(ATM|ATW|NWD|CASH WDL)\b", re.I), "atm"),
    (re.compile(r"^\s*(POS|VIN|ECOM)\b", re.I), "card"),
    (re.compile(r"\b(NETBANKING|NET BANKING|BILLPAY|BIL/)\b", re.I), "netbanking"),
    (re.compile(r"\b(CHQ|CHEQUE|CLG)\b", re.I), "cheque"),
]

_UPI_REF = re.compile(r"^\s*UPI[/-](?:[A-Z0-9]+[/-])?(\d{10,14})\b", re.I)


def infer_payment_method(description: str, default: str = "other") -> str:
    for pattern, method in _RULES:
        if pattern.search(description):
            return method
    return default


def upi_reference(description: str) -> str | None:
    """The UPI transaction reference (RRN) embedded in a UPI narration, if any."""
    m = _UPI_REF.match(description)
    return m.group(1) if m else None
