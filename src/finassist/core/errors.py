"""Closed set of errors that cross module boundaries. Messages are safe to show."""

from __future__ import annotations

__all__ = ["FinError", "NotFound", "Ambiguous", "ValidationFailed", "ImportRejected", "Conflict"]


class FinError(Exception):
    code = "error"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class NotFound(FinError):
    code = "not_found"


class Ambiguous(FinError):
    code = "ambiguous"


class ValidationFailed(FinError):
    code = "invalid"


class ImportRejected(FinError):
    code = "import_rejected"


class Conflict(FinError):
    code = "conflict"
