"""The parser plug-in interface (ARCH §7.2, NFR-8)."""

from __future__ import annotations

from typing import ClassVar, Protocol

from finassist.ingest.models import ParseResult, SourceFile
from finassist.ingest.profiles import MappingProfile

__all__ = ["Parser"]


class Parser(Protocol):
    kind: ClassVar[str]

    def match_profile(self, file: SourceFile, profiles: list[MappingProfile]) -> MappingProfile | None:
        """The profile whose header layout this file has, or None."""
        ...

    def parse(self, file: SourceFile, profile: MappingProfile) -> ParseResult: ...
