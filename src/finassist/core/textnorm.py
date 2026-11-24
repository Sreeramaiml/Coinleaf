"""Text normalisation primitives shared by ingest and classification."""

from __future__ import annotations

import re
import unicodedata

__all__ = ["collapse_ws", "norm_description", "norm_header"]


def collapse_ws(s: str) -> str:
    return " ".join(s.split())


def norm_description(s: str) -> str:
    """Stable form of a raw description for fingerprints: NFKC, upper case, single spaces."""
    return collapse_ws(unicodedata.normalize("NFKC", s)).upper()


def norm_header(s: str) -> str:
    """Header cell → comparison key: lower case, alphanumerics and single spaces only."""
    s = unicodedata.normalize("NFKC", s).replace("﻿", "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return collapse_ws(s)


_COMPANY_SUFFIXES = re.compile(
    r"\b(PRIVATE LIMITED|PVT\.? LTD\.?|LIMITED|LTD\.?|INDIA|INTERNET|INC\.?|LLP)\b\.?", re.IGNORECASE
)
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def _title(s: str) -> str:
    words = [w for w in re.split(r"\s+", s.strip()) if w]
    out = []
    for w in words:
        out.append(w if (any(c.isdigit() for c in w) or "." in w) else w[:1].upper() + w[1:].lower())
    return " ".join(out)


def payee_of(description: str) -> str:
    """A short display name for who was paid or who paid, read from a statement narration.
    Deterministic and display-only (nothing is stored); merchant normalisation with an
    alias table arrives in P2 (ARCH §8.1).

    UPI/<ref>/<NAME>/<vpa>/…            → Name
    IMPS/<type>/<ref>/<NAME>/…          → Name
    NEFT CR-<IFSC>-<NAME>-…             → Name
    ACH D- <NAME>-…                     → Name
    BIL/<channel>/<ref>/<NAME>/…        → Name
    MERCHANT*ORDER CITY CC (card)       → Merchant
    MERCHANT NAME CITY CC (card)        → Merchant Name
    """
    d = collapse_ws(description)
    u = d.upper()
    name = ""
    parts = d.split("/")
    if u.startswith("UPI/") and len(parts) > 2:
        name = parts[2]
    elif u.startswith("IMPS/") and len(parts) > 3 or u.startswith("BIL/") and len(parts) > 3:
        name = parts[3]
    elif u.startswith("NEFT"):
        bits = d.split("-")
        name = bits[2] if len(bits) > 2 else d
    elif u.startswith("ACH"):
        rest = re.sub(r"^ACH\s*[DC]?\s*-\s*", "", d, flags=re.IGNORECASE)
        name = rest.split("-")[0]
    elif u.startswith("INT.PD") or u.startswith("INT PD"):
        return "Savings interest"
    else:
        name = d.split("*")[0]
        tokens = name.split()
        if "*" not in d and len(tokens) >= 3 and _COUNTRY.match(tokens[-1]):
            tokens = tokens[:-2]  # drop "CITY CC" at the end of card narrations
        name = " ".join(tokens)
    name = _COMPANY_SUFFIXES.sub("", name)
    name = collapse_ws(name).strip(" -.,")
    return _title(name) if name else _title(d[:30])
