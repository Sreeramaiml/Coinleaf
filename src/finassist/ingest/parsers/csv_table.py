"""CSV statements driven by a mapping profile (FR-1).

The parser finds the header row (preamble lines before it are searched for the account
last4 and statement period), reads data rows, picks up opening/closing summary rows,
joins description continuation lines, and reports every row it cannot parse. Any row
error rejects the whole file upstream (PS §13: never partial commits).
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from charset_normalizer import from_bytes

from finassist.core.textnorm import norm_header
from finassist.ingest.models import ParseResult, RowError, SourceFile, StatementMeta
from finassist.ingest.parsers.table import parse_table, read_preamble
from finassist.ingest.profiles import MappingProfile

__all__ = ["CsvTableParser", "decode_text"]

_DELIMS = [",", ";", "\t", "|"]


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            raise ValueError("could not detect the file's text encoding") from None
        return str(best)


def _rows(text: str) -> list[list[str]]:
    sample = "\n".join(text.splitlines()[:40])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_DELIMS))
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


class CsvTableParser:
    kind: ClassVar[str] = "csv"

    def _header_index(self, rows: list[list[str]], profile: MappingProfile) -> int | None:
        want = [norm_header(h) for h in profile.headers]
        for i, row in enumerate(rows[:60]):
            cells = [norm_header(c) for c in row]
            while cells and not cells[-1]:
                cells.pop()
            if cells == want:
                return i
        return None

    def match_profile(self, file: SourceFile, profiles: list[MappingProfile]) -> MappingProfile | None:
        if file.kind != "text":
            return None
        try:
            rows = _rows(decode_text(file.data))
        except ValueError:
            return None
        for p in profiles:
            if p.file_kind == "csv" and self._header_index(rows, p) is not None:
                return p
        return None

    def parse(self, file: SourceFile, profile: MappingProfile) -> ParseResult:
        text = decode_text(file.data)
        rows = _rows(text)
        hi = self._header_index(rows, profile)
        if hi is None:
            return ParseResult(
                [], StatementMeta(), [RowError(0, f"header row for {profile.name} not found")], profile.name
            )
        headers = rows[hi]
        meta = StatementMeta()
        read_preamble(meta, profile, "\n".join(",".join(r) for r in rows[:hi]))
        numbered = [(n, row) for n, row in enumerate(rows[hi + 1 :], start=hi + 2)]  # 1-based line numbers
        return parse_table(numbered, headers, profile, meta)
