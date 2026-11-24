"""
rety coordinate normalization and source position conversions.

Different Python type checkers report diagnostic columns using different coordinate
systems and indexing conventions:
    - mypy:    0-indexed, UTF-8 byte offsets (start and exclusive end)
    - Pyright: 0-indexed, UTF-16 code units (LSP character offsets, start and exclusive end)
    - pyrefly: 1-indexed, Unicode code points (start and exclusive end)
    - ty:      1-indexed, Unicode code points (start position only in concise format)

Canonical coordinate convention:
    - Line numbers:    1-indexed (preserves standard editor and Python conventions).
    - Column numbers:  0-based Unicode code-point offsets (internally).
    - End columns:     0-based exclusive Unicode code-point offsets, or None when omitted.
    - Source positions: measured against the original source line text.

Public schema convention:
    - NormalizedDiagnostic exposes `start_col` and `end_col` as 1-indexed Unicode
      code-point offsets for backward compatibility with schema_version 1, CLI
      formatters, editors, and JSON consumers.
    - Canonical 0-based offsets are accessible via `canonical_start_col`,
      `canonical_end_col`, or `CanonicalPosition.from_diagnostic(diag)`.
    - If source text is unavailable (e.g. deleted file, synthetic test path),
      original coordinates are preserved and `column_uncertain` is set to True.
"""

from __future__ import annotations

import io
import tokenize
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rety.schema import NormalizedDiagnostic


class ColumnUnit(StrEnum):
    """The unit of measurement used by a checker to report column positions."""

    BYTE = "byte"  # UTF-8 byte offsets (mypy)
    UTF16 = "utf16"  # UTF-16 code units / LSP character offsets (Pyright)
    CODEPOINT = "codepoint"  # Unicode code points / character offsets (pyrefly, ty)


class IndexBase(IntEnum):
    """The indexing origin used by a checker."""

    ZERO = 0  # 0-indexed columns (mypy, Pyright)
    ONE = 1  # 1-indexed columns (pyrefly, ty)


#: Verified coordinate conventions for supported checkers.
CHECKER_COORDINATES: dict[str, tuple[ColumnUnit, IndexBase]] = {
    "mypy": (ColumnUnit.BYTE, IndexBase.ZERO),
    "pyright": (ColumnUnit.UTF16, IndexBase.ZERO),
    "pyrefly": (ColumnUnit.CODEPOINT, IndexBase.ONE),
    "ty": (ColumnUnit.CODEPOINT, IndexBase.ONE),
}


@dataclass(frozen=True)
class CanonicalPosition:
    """
    Canonical source position representation.

    All columns are 0-indexed Unicode code-point offsets with exclusive end.
    All lines are 1-indexed.
    """

    line: int
    col: int | None = None
    end_line: int | None = None
    end_col: int | None = None
    is_uncertain: bool = False

    @property
    def start_col_1indexed(self) -> int | None:
        """1-indexed start column for human display and public schema."""
        return self.col + 1 if self.col is not None else None

    @property
    def end_col_1indexed(self) -> int | None:
        """1-indexed exclusive end column for human display and public schema."""
        return self.end_col + 1 if self.end_col is not None else None

    @classmethod
    def from_diagnostic(cls, diag: NormalizedDiagnostic) -> CanonicalPosition:
        """Extract canonical 0-based position from a NormalizedDiagnostic."""
        col0 = diag.start_col - 1 if diag.start_col is not None else None
        end_col0 = diag.end_col - 1 if diag.end_col is not None else None
        return cls(
            line=diag.start_line,
            col=col0,
            end_line=diag.end_line,
            end_col=end_col0,
            is_uncertain=diag.column_uncertain,
        )


def utf8_col_to_codepoint(line: str, byte_offset: int) -> int:
    """
    Convert a 0-based UTF-8 byte offset on `line` to a 0-based code-point offset.

    - Clamps negative offsets to 0.
    - Clamps offsets beyond the encoded length to len(line).
    - If the byte offset falls in the middle of a multi-byte sequence, it rounds
      down to the start of that character boundary.
    """
    if byte_offset <= 0 or not line:
        return 0
    if line.isascii():
        return min(byte_offset, len(line))
    encoded = line.encode("utf-8")
    if byte_offset >= len(encoded):
        return len(line)
    return len(encoded[:byte_offset].decode("utf-8", errors="ignore"))


def utf16_col_to_codepoint(line: str, u16_offset: int) -> int:
    """
    Convert a 0-based UTF-16 code unit offset on `line` to a 0-based code-point offset.

    - Clamps negative offsets to 0.
    - Clamps offsets beyond the UTF-16 code unit length to len(line).
    - Characters outside the BMP (e.g. emojis > U+FFFF) occupy 2 UTF-16 code units.
      Offsets splitting a surrogate pair round down to that character.
    """
    if u16_offset <= 0 or not line:
        return 0
    if line.isascii():
        return min(u16_offset, len(line))
    cur_u16 = 0
    for i, ch in enumerate(line):
        ch_len = 2 if ord(ch) > 0xFFFF else 1
        if cur_u16 + ch_len > u16_offset:
            return i
        cur_u16 += ch_len
    return len(line)


def codepoint_to_utf8_col(line: str, cp_offset: int) -> int:
    """Convert a 0-based Unicode code-point offset on `line` to a 0-based UTF-8 byte offset."""
    if cp_offset <= 0 or not line:
        return 0
    if line.isascii():
        return min(cp_offset, len(line))
    return len(line[:cp_offset].encode("utf-8"))


def codepoint_to_utf16_col(line: str, cp_offset: int) -> int:
    """
    Convert a 0-based Unicode code-point offset on `line` to a 0-based UTF-16
    code unit offset.
    """
    if cp_offset <= 0 or not line:
        return 0
    if line.isascii():
        return min(cp_offset, len(line))
    return sum(2 if ord(c) > 0xFFFF else 1 for c in line[:cp_offset])


def normalize_column(
    line: str | None,
    col: int | None,
    unit: ColumnUnit,
    base: IndexBase = IndexBase.ZERO,
) -> tuple[int | None, bool]:
    """
    Normalize an input column to a 0-based Unicode code-point offset.

    Args:
        line: The text of the source line, or None if the source is unavailable.
        col: The raw column value reported by the checker, or None if missing.
        unit: The coordinate unit used by the checker (BYTE, UTF16, CODEPOINT).
        base: The index base used by the checker (ZERO, ONE).

    Returns:
        A tuple of `(canonical_col, is_uncertain)`.
        - `canonical_col` is 0-indexed Unicode code-point offset, or None.
        - `is_uncertain` is True if conversion could not be verified reliably
          (e.g. line was missing for non-codepoint unit, or offset was out of range).
    """
    if col is None or isinstance(col, bool):
        return None, False

    try:
        col_int = int(col)
    except (ValueError, TypeError):
        return None, True

    # Values below base mean missing/unknown.
    # Note: 0 is valid for 0-indexed (first character), but invalid for 1-indexed.
    if col_int < base.value:
        return None, True

    native_0 = col_int - base.value

    # If line is missing:
    if line is None:
        if unit == ColumnUnit.CODEPOINT:
            # Code points need no character-width conversion; only index base adjustment.
            return native_0, False
        # For byte or UTF-16 units without source text, we cannot accurately convert non-ASCII.
        # Preserve original 0-based offset and explicitly mark as uncertain.
        return native_0, True

    # When line is present:
    if native_0 < 0:
        return None, True

    if unit == ColumnUnit.BYTE:
        encoded = line.encode("utf-8")
        is_oob = native_0 > len(encoded)
        cp = utf8_col_to_codepoint(line, native_0)
        return cp, is_oob

    if unit == ColumnUnit.UTF16:
        # Check out-of-bounds for UTF-16
        u16_len = len(line) if line.isascii() else sum(2 if ord(c) > 0xFFFF else 1 for c in line)
        is_oob = native_0 > u16_len
        cp = utf16_col_to_codepoint(line, native_0)
        return cp, is_oob

    if unit == ColumnUnit.CODEPOINT:
        is_oob = native_0 > len(line)
        return min(native_0, len(line)), is_oob

    return native_0, True


def normalize_checker_columns(
    file_path: str | None,
    checker: str,
    start_line: int,
    start_col: int | None,
    end_line: int | None = None,
    end_col: int | None = None,
    lines: list[str] | None = None,
    base: IndexBase | None = None,
    get_line_func: Callable[[str, int], str | None] | None = None,
) -> tuple[int | None, int | None, bool]:
    """
    Convert checker-reported positions into 1-indexed normalized columns.

    Args:
        file_path: Absolute path to the source file, or None if unknown.
        checker: Checker name ("mypy", "pyright", "pyrefly", "ty").
        start_line: 1-indexed start line.
        start_col: Checker-native start column.
        end_line: 1-indexed end line (defaults to start_line if omitted).
        end_col: Checker-native end column.
        lines: Pre-loaded lines of the file, if available.
        base: Explicit index base override (defaults to checker's native base).
        get_line_func: Custom line provider, defaults to `get_source_line`.

    Returns:
        `(start_col_1indexed, end_col_1indexed, is_uncertain)`
    """
    conv = CHECKER_COORDINATES.get(checker)
    if conv is None:
        # Unknown checker: pass through assuming 1-indexed, mark uncertain
        s_1 = int(start_col) if start_col is not None and not isinstance(start_col, bool) else None
        e_1 = int(end_col) if end_col is not None and not isinstance(end_col, bool) else None
        return s_1, e_1, True

    unit, default_base = conv
    effective_base = default_base if base is None else base

    # Retrieve source line text for start and end lines
    line_provider = get_line_func or get_source_line
    start_text: str | None = None
    end_text: str | None = None

    if lines is not None:
        if 1 <= start_line <= len(lines):
            start_text = lines[start_line - 1]
        effective_end_line = end_line if end_line is not None else start_line
        if 1 <= effective_end_line <= len(lines):
            end_text = lines[effective_end_line - 1]
    elif file_path:
        start_text = line_provider(file_path, start_line)
        effective_end_line = end_line if end_line is not None else start_line
        end_text = (
            start_text
            if effective_end_line == start_line
            else line_provider(file_path, effective_end_line)
        )

    # Normalize start column
    start_0, start_uncertain = normalize_column(start_text, start_col, unit, effective_base)

    # Normalize end column
    end_0: int | None = None
    end_uncertain = False
    if end_col is not None:
        end_0, end_uncertain = normalize_column(end_text, end_col, unit, effective_base)

    start_1 = start_0 + 1 if start_0 is not None else None
    end_1 = end_0 + 1 if end_0 is not None else None
    is_uncertain = start_uncertain or end_uncertain

    return start_1, end_1, is_uncertain


def normalize_diagnostic(
    diag: NormalizedDiagnostic,
    lines: list[str] | None = None,
) -> NormalizedDiagnostic:
    """
    Normalize coordinates of an existing NormalizedDiagnostic against source lines if needed.
    """
    if not diag.column_uncertain:
        return diag
    start_col, end_col, uncertain = normalize_checker_columns(
        file_path=diag.file,
        checker=diag.checker,
        start_line=diag.start_line,
        start_col=diag.start_col,
        end_line=diag.end_line,
        end_col=diag.end_col,
        lines=lines,
        # since it's already on NormalizedDiagnostic, it was stored 1-indexed
        base=IndexBase.ONE,
    )
    if (start_col, end_col, uncertain) != (diag.start_col, diag.end_col, diag.column_uncertain):
        return diag.model_copy(
            update={
                "start_col": start_col,
                "end_col": end_col,
                "column_uncertain": uncertain,
            }
        )
    return diag


_MAX_CACHE_FILES = 512
_source_cache: OrderedDict[str, list[str] | None] = OrderedDict()


def split_source_lines(source: str) -> list[str]:
    """
    Split Python source on standard line breaks without breaking on form feeds
    or U+2028, ensuring identical line count to tokenizer.
    """
    return source.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def decode_source(content: bytes) -> str:
    """
    Decode Python source bytes, honoring UTF-8 BOM and PEP 263 coding cookies.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
    except SyntaxError:
        encoding = "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def get_source_lines(file_path: str) -> list[str] | None:
    """
    Read and split lines of a source file, with LRU caching.
    Returns None if file cannot be read or found.
    """
    if file_path in _source_cache:
        _source_cache.move_to_end(file_path)
        return _source_cache[file_path]

    try:
        content = Path(file_path).read_bytes()
        lines = split_source_lines(decode_source(content))
    except OSError:
        lines = None

    _source_cache[file_path] = lines
    if len(_source_cache) > _MAX_CACHE_FILES:
        _source_cache.popitem(last=False)
    return lines


def get_source_line(file_path: str, lineno: int) -> str | None:
    """
    Get a single 1-indexed line from a file, or None if unavailable.
    """
    lines = get_source_lines(file_path)
    if lines is None or not (1 <= lineno <= len(lines)):
        return None
    return lines[lineno - 1]


def clear_source_cache() -> None:
    """Clear the cached source lines (used in tests)."""
    _source_cache.clear()
