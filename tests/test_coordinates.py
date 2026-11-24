"""
Tests for coordinate normalization across type checkers (rety/coordinates.py).

Covers:
    - ASCII controls
    - Accented characters (UTF-8 multi-byte)
    - Greek characters (non-ASCII BMP)
    - Supplementary Unicode characters (surrogate pairs in UTF-16, 4 bytes in UTF-8)
    - Mixed Unicode categories in the same line
    - Boundary conditions: column 0, first character, last character, EOL, empty lines, OOB
    - Tabs (counted as 1 source character without tab-width expansion)
    - Multiline diagnostics
    - Missing end columns
    - Alignment regression: verifying confidence is not downgraded on Unicode mismatch
    - Uncertainty tracking when source is unavailable or out-of-range
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.mypy import MypyAdapter
from rety.adapters.pyrefly import PyreflyAdapter
from rety.adapters.pyright import PyrightAdapter
from rety.adapters.ty import TyAdapter
from rety.align import align, clear_ast_cache
from rety.coordinates import (
    ColumnUnit,
    IndexBase,
    clear_source_cache,
    codepoint_to_utf8_col,
    codepoint_to_utf16_col,
    normalize_checker_columns,
    normalize_column,
    utf8_col_to_codepoint,
    utf16_col_to_codepoint,
)
from rety.schema import Confidence, NormalizedDiagnostic, RawInvocation, Severity


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_ast_cache()
    clear_source_cache()


def test_ascii_controls() -> None:
    """ASCII characters have identical 1-to-1 byte, UTF-16 code unit, and code-point offsets."""
    line = 'value = "hello"'
    hello_start = line.index("hello")
    hello_end = hello_start + len("hello")

    # Byte offsets
    assert codepoint_to_utf8_col(line, hello_start) == hello_start
    assert codepoint_to_utf8_col(line, hello_end) == hello_end
    assert utf8_col_to_codepoint(line, hello_start) == hello_start
    assert utf8_col_to_codepoint(line, hello_end) == hello_end

    # UTF-16 code units
    assert codepoint_to_utf16_col(line, hello_start) == hello_start
    assert codepoint_to_utf16_col(line, hello_end) == hello_end
    assert utf16_col_to_codepoint(line, hello_start) == hello_start
    assert utf16_col_to_codepoint(line, hello_end) == hello_end


def test_accented_characters_utf8() -> None:
    """Accented characters occupy 1 code point, 1 UTF-16 code unit, but 2 UTF-8 bytes."""
    line = 'value = "café"'
    # 'é' is at code point index 12 (0-indexed)
    e_cp = line.index("é")
    assert e_cp == 12

    # In UTF-8, 'café' has 5 bytes ('é' is \xc3\xa9, 2 bytes)
    # The quote after 'café' is at code point 13, but byte offset 14
    quote_cp = 13
    quote_byte = codepoint_to_utf8_col(line, quote_cp)
    assert quote_byte == 14
    assert utf8_col_to_codepoint(line, quote_byte) == quote_cp

    # Inside 'café': token after 'café' on a longer line
    long_line = 'c = "café"; target = 1'
    target_cp = long_line.index("target")
    target_byte = codepoint_to_utf8_col(long_line, target_cp)
    # One extra byte from 'é'
    assert target_byte == target_cp + 1
    assert utf8_col_to_codepoint(long_line, target_byte) == target_cp


def test_greek_characters_bmp() -> None:
    """Greek characters (BMP) occupy 1 code point, 1 UTF-16 code unit, and 2 UTF-8 bytes."""
    line = 'value = "αβ + 1"'
    alpha_cp = line.index("α")
    beta_cp = line.index("β")
    plus_cp = line.index("+")

    assert beta_cp == alpha_cp + 1
    assert plus_cp == alpha_cp + 3

    # UTF-8: α and β are 2 bytes each
    assert codepoint_to_utf8_col(line, alpha_cp) == alpha_cp
    assert codepoint_to_utf8_col(line, beta_cp) == alpha_cp + 2
    assert codepoint_to_utf8_col(line, plus_cp) == alpha_cp + 4 + 1  # 2 + 2 + 1 space

    # Convert back from UTF-8
    assert utf8_col_to_codepoint(line, codepoint_to_utf8_col(line, beta_cp)) == beta_cp
    assert utf8_col_to_codepoint(line, codepoint_to_utf8_col(line, plus_cp)) == plus_cp

    # UTF-16: BMP characters are 1 code unit each
    assert codepoint_to_utf16_col(line, beta_cp) == beta_cp
    assert codepoint_to_utf16_col(line, plus_cp) == plus_cp
    assert utf16_col_to_codepoint(line, beta_cp) == beta_cp
    assert utf16_col_to_codepoint(line, plus_cp) == plus_cp


def test_supplementary_unicode_characters_surrogate_pairs() -> None:
    """
    Supplementary characters (> U+FFFF, like 🚀) occupy 1 code point, 2 UTF-16 units,
    and 4 UTF-8 bytes.
    """
    line = 'value = "🚀value"'
    rocket_cp = line.index("\U0001f680")
    val_cp = line.index("value", rocket_cp)

    assert val_cp == rocket_cp + 1

    # UTF-8: 4 bytes for 🚀
    val_byte = codepoint_to_utf8_col(line, val_cp)
    assert val_byte == rocket_cp + 4
    assert utf8_col_to_codepoint(line, val_byte) == val_cp

    # UTF-16: 2 code units for surrogate pair \ud83d\ude80
    val_u16 = codepoint_to_utf16_col(line, val_cp)
    assert val_u16 == rocket_cp + 2
    assert utf16_col_to_codepoint(line, val_u16) == val_cp

    # An offset pointing in between surrogate halves maps to the character itself
    assert utf16_col_to_codepoint(line, rocket_cp + 1) == rocket_cp


def test_mixed_unicode_categories() -> None:
    """
    Mixed categories: accented (2 bytes UTF-8), emoji (4 bytes UTF-8, 2 UTF-16),
    Greek (2 bytes UTF-8).
    """
    line = 'x = "café + 🚀 + α"; target = 1'
    target_cp = line.index("target")

    # In UTF-8:
    # 'é' adds +1 byte over ASCII
    # '🚀' adds +3 bytes over ASCII
    # 'α' adds +1 byte over ASCII
    # Total byte shift: +5 bytes
    target_byte = codepoint_to_utf8_col(line, target_cp)
    assert target_byte == target_cp + 5
    assert utf8_col_to_codepoint(line, target_byte) == target_cp

    # In UTF-16:
    # 'é' is BMP (1 unit) -> shift 0
    # '🚀' is non-BMP (2 units) -> shift +1
    # 'α' is BMP (1 unit) -> shift 0
    # Total UTF-16 shift: +1 unit
    target_u16 = codepoint_to_utf16_col(line, target_cp)
    assert target_u16 == target_cp + 1
    assert utf16_col_to_codepoint(line, target_u16) == target_cp


def test_tabs_remain_single_character() -> None:
    """Tabs must remain single source characters without visual expansion."""
    line = "\t\tx = 'hello'"
    hello_cp = line.index("hello")

    # Each tab is 1 char, 1 byte in UTF-8, 1 unit in UTF-16
    assert hello_cp == 7  # 2 tabs + 5 chars ("x = '")
    assert codepoint_to_utf8_col(line, hello_cp) == 7
    assert codepoint_to_utf16_col(line, hello_cp) == 7
    assert utf8_col_to_codepoint(line, 7) == 7
    assert utf16_col_to_codepoint(line, 7) == 7


def test_boundary_column_zero() -> None:
    """Column 0 in 0-indexed systems refers to the first character (code point 0)."""
    line = "x = 1"
    assert utf8_col_to_codepoint(line, 0) == 0
    assert utf16_col_to_codepoint(line, 0) == 0

    col0, uncertain = normalize_column(line, 0, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col0 == 0
    assert not uncertain

    col0_u16, uncertain_u16 = normalize_column(line, 0, ColumnUnit.UTF16, IndexBase.ZERO)
    assert col0_u16 == 0
    assert not uncertain_u16


def test_boundary_first_and_last_character() -> None:
    line = "α = '🚀'"
    # first char α is index 0
    assert utf8_col_to_codepoint(line, 0) == 0
    assert utf16_col_to_codepoint(line, 0) == 0

    # last char is closing quote ' at code point 6
    last_cp = len(line) - 1
    last_byte = codepoint_to_utf8_col(line, last_cp)
    last_u16 = codepoint_to_utf16_col(line, last_cp)

    assert utf8_col_to_codepoint(line, last_byte) == last_cp
    assert utf16_col_to_codepoint(line, last_u16) == last_cp


def test_boundary_end_of_line_position() -> None:
    """Exclusive end position at end of line (len(line))."""
    line = "x: int = 'café'"
    eol_cp = len(line)
    eol_byte = len(line.encode("utf-8"))
    eol_u16 = len(line)

    assert utf8_col_to_codepoint(line, eol_byte) == eol_cp
    assert utf16_col_to_codepoint(line, eol_u16) == eol_cp

    col0, uncertain = normalize_column(line, eol_byte, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col0 == eol_cp
    assert not uncertain


def test_boundary_empty_source_line() -> None:
    """Empty line has length 0."""
    empty = ""
    assert utf8_col_to_codepoint(empty, 0) == 0
    assert utf16_col_to_codepoint(empty, 0) == 0
    assert utf8_col_to_codepoint(empty, 5) == 0
    assert utf16_col_to_codepoint(empty, 5) == 0

    col0, uncertain = normalize_column(empty, 0, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col0 == 0
    assert not uncertain

    # Out of range on empty line is flagged uncertain
    col_oob, is_unc = normalize_column(empty, 5, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col_oob == 0
    assert is_unc


def test_boundary_out_of_range_offsets() -> None:
    """Offsets beyond the end of the line clamp to len(line) and mark uncertainty."""
    line = "x = 'café'"
    # Byte length is 11, character length is 10
    col_cp, uncertain = normalize_column(line, 999, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col_cp == len(line)
    assert uncertain

    col_u16, uncertain_u16 = normalize_column(line, 999, ColumnUnit.UTF16, IndexBase.ZERO)
    assert col_u16 == len(line)
    assert uncertain_u16


def test_missing_and_negative_columns() -> None:
    line = "x = 1"
    # None column
    col, unc = normalize_column(line, None, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col is None
    assert not unc

    # Negative column in 0-indexed is below base
    col_neg, unc_neg = normalize_column(line, -1, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col_neg is None
    assert unc_neg

    # Column 0 in 1-indexed is below base (base is 1)
    col_0_one, unc_0_one = normalize_column(line, 0, ColumnUnit.CODEPOINT, IndexBase.ONE)
    assert col_0_one is None
    assert unc_0_one


def test_uncertainty_when_source_unavailable() -> None:
    """When source file is not found, byte/UTF16 coordinates are preserved and marked uncertain."""
    # Byte offset 15 without source line cannot know Unicode shift
    col, unc = normalize_column(None, 15, ColumnUnit.BYTE, IndexBase.ZERO)
    assert col == 15
    assert unc is True

    # Code points without source line can still be reliably adjusted for base
    col_cp, unc_cp = normalize_column(None, 12, ColumnUnit.CODEPOINT, IndexBase.ONE)
    assert col_cp == 11
    assert unc_cp is False


def test_multiline_diagnostic_normalization() -> None:
    """start_col is normalized against start_line; end_col against end_line."""
    lines = [
        "first = '🚀'",  # line 1: non-BMP emoji
        "second = 'café'",  # line 2: accented
    ]

    # mypy coordinates:
    # line 1: column 9 (byte 9, after 🚀)
    # line 2: column 15 (byte 15, after café)
    start_1, end_1, unc = normalize_checker_columns(
        file_path=None,
        checker="mypy",
        start_line=1,
        start_col=9,
        end_line=2,
        end_col=15,
        lines=lines,
    )

    # Line 1: first = '🚀'
    # 'first = '' is 9 bytes -> code point 9. 1-indexed: 10
    # Line 2: second = 'café'
    # 'second = 'café'' is 15 bytes -> code point 14. 1-indexed: 15
    assert start_1 == 10
    assert end_1 == 15
    assert not unc


def test_all_adapters_produce_identical_columns_for_unicode(tmp_path: Path) -> None:
    """Verify mypy, pyright, pyrefly, and ty normalize to the exact same (line, col) range."""
    # In this line:
    # prefix: prefix_len code points
    # target: 'bad'
    source = 'a = "café + 🚀 + α"; target = "bad"\n'
    src_file = tmp_path / "test_diag.py"
    src_file.write_text(source, encoding="utf-8")

    token = '"bad"'
    target_start_cp = source.index(token)  # 0-indexed code point
    target_end_cp = target_start_cp + len(token)  # 0-indexed exclusive code point
    expected_start_1 = target_start_cp + 1  # 1-indexed
    expected_end_1 = target_end_cp + 1  # 1-indexed

    # Calculate native offsets
    mypy_start_byte = codepoint_to_utf8_col(source, target_start_cp)
    mypy_end_byte = codepoint_to_utf8_col(source, target_end_cp)

    pyright_start_u16 = codepoint_to_utf16_col(source, target_start_cp)
    pyright_end_u16 = codepoint_to_utf16_col(source, target_end_cp)

    pyrefly_start_1 = target_start_cp + 1
    pyrefly_end_1 = target_end_cp + 1

    ty_start_1 = target_start_cp + 1

    # 1. mypy
    mypy_raw = json.dumps(
        {
            "file": str(src_file),
            "line": 1,
            "column": mypy_start_byte,
            "end_line": 1,
            "end_column": mypy_end_byte,
            "message": "Type mismatch",
            "code": "arg-type",
            "severity": "error",
        }
    )
    mypy_diags = MypyAdapter().parse(RawInvocation("mypy", 1, mypy_raw, "", 10.0, "2.3.1"))
    assert len(mypy_diags) == 1
    assert mypy_diags[0].start_col == expected_start_1
    assert mypy_diags[0].end_col == expected_end_1

    # 2. pyright
    pyright_raw = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": str(src_file),
                    "severity": "error",
                    "message": "Type mismatch",
                    "range": {
                        "start": {"line": 0, "character": pyright_start_u16},
                        "end": {"line": 0, "character": pyright_end_u16},
                    },
                    "rule": "reportArgumentType",
                }
            ]
        }
    )
    pyright_diags = PyrightAdapter().parse(
        RawInvocation("pyright", 1, pyright_raw, "", 10.0, "1.1.414")
    )
    assert len(pyright_diags) == 1
    assert pyright_diags[0].start_col == expected_start_1
    assert pyright_diags[0].end_col == expected_end_1

    # 3. pyrefly
    pyrefly_raw = json.dumps(
        {
            "errors": [
                {
                    "path": str(src_file),
                    "line": 1,
                    "column": pyrefly_start_1,
                    "stop_line": 1,
                    "stop_column": pyrefly_end_1,
                    "name": "bad-argument",
                    "description": "Type mismatch",
                    "severity": "error",
                }
            ]
        }
    )
    pyrefly_diags = PyreflyAdapter().parse(
        RawInvocation("pyrefly", 1, pyrefly_raw, "", 10.0, "1.3.1")
    )
    assert len(pyrefly_diags) == 1
    assert pyrefly_diags[0].start_col == expected_start_1
    assert pyrefly_diags[0].end_col == expected_end_1

    # 4. ty
    ty_raw = f"{src_file}:1:{ty_start_1}: error[invalid-argument] Type mismatch\nFound 1 diagnostic"
    ty_diags = TyAdapter().parse(RawInvocation("ty", 1, ty_raw, "", 10.0, "0.0.83"))
    assert len(ty_diags) == 1
    assert ty_diags[0].start_col == expected_start_1

    # Alignment across all 4 checkers
    all_diags = [mypy_diags[0], pyright_diags[0], pyrefly_diags[0], ty_diags[0]]
    clusters = align(all_diags)
    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH
    assert set(clusters[0].checkers_present) == {"mypy", "pyright", "pyrefly", "ty"}


def test_alignment_regression_unicode_mismatch_produces_high_confidence(tmp_path: Path) -> None:
    """
    Two synthetic diagnostics from different checkers on a Unicode line.
    Without normalization, mypy's byte offset and Pyright's UTF-16 offset disagree,
    causing alignment to drop confidence to LOW.
    With normalization, canonical positions match and confidence is HIGH.
    """
    source = 'msg = "🚀"; val: int = "bad"\n'
    src_file = tmp_path / "unicode_mismatch.py"
    src_file.write_text(source, encoding="utf-8")

    token = '"bad"'
    cp_start = source.index(token)
    cp_end = cp_start + len(token)

    byte_start = codepoint_to_utf8_col(source, cp_start)
    byte_end = codepoint_to_utf8_col(source, cp_end)

    u16_start = codepoint_to_utf16_col(source, cp_start)
    u16_end = codepoint_to_utf16_col(source, cp_end)

    # Note that raw native offsets differ:
    # byte_start is 19, u16_start is 17, cp_start is 16
    assert byte_start != u16_start
    assert byte_start != cp_start

    # Build mypy diagnostic
    mypy_diag = MypyAdapter().parse(
        RawInvocation(
            "mypy",
            1,
            json.dumps(
                {
                    "file": str(src_file),
                    "line": 1,
                    "column": byte_start,
                    "end_line": 1,
                    "end_column": byte_end,
                    "message": "Incompatible types in assignment",
                    "code": "assignment",
                    "severity": "error",
                }
            ),
            "",
            10.0,
            "2.3.1",
        )
    )[0]

    # Build Pyright diagnostic
    pyright_diag = PyrightAdapter().parse(
        RawInvocation(
            "pyright",
            1,
            json.dumps(
                {
                    "generalDiagnostics": [
                        {
                            "file": str(src_file),
                            "severity": "error",
                            "message": "Type mismatch",
                            "range": {
                                "start": {"line": 0, "character": u16_start},
                                "end": {"line": 0, "character": u16_end},
                            },
                            "rule": "reportAssignmentType",
                        }
                    ]
                }
            ),
            "",
            10.0,
            "1.1.414",
        )
    )[0]

    # Verify normalized coordinates match
    assert mypy_diag.start_col == pyright_diag.start_col == cp_start + 1
    assert mypy_diag.end_col == pyright_diag.end_col == cp_end + 1
    assert mypy_diag.canonical_start_col == pyright_diag.canonical_start_col == cp_start
    assert mypy_diag.canonical_end_col == pyright_diag.canonical_end_col == cp_end

    # Align
    clusters = align([mypy_diag, pyright_diag])
    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH
    assert "exact range L1-1: mypy ↔ pyright" in clusters[0].alignment_signals


def test_alignment_uncertain_columns_do_not_downgrade_confidence() -> None:
    """
    If coordinates could not be normalized reliably (column_uncertain=True),
    they must not cause false disagreement or degrade confidence.
    """
    d1 = NormalizedDiagnostic(
        checker="mypy",
        checker_version="1.0",
        file="/missing/path.py",
        start_line=5,
        start_col=10,
        end_line=5,
        end_col=15,
        column_uncertain=True,
        severity=Severity.error,
        message="error 1",
        raw="{}",
    )
    d2 = NormalizedDiagnostic(
        checker="pyright",
        checker_version="1.0",
        file="/missing/path.py",
        start_line=5,
        start_col=12,  # different column value, but uncertain
        end_line=5,
        end_col=15,
        column_uncertain=True,
        severity=Severity.error,
        message="error 2",
        raw="{}",
    )

    clusters = align([d1, d2])
    assert len(clusters) == 1
    # Because columns are uncertain, they do not disagree, so exact range matching applies
    assert clusters[0].confidence == Confidence.HIGH


def test_real_mypy_execution_on_unicode_file(tmp_path: Path) -> None:
    """
    Run real mypy executable on source with non-ASCII characters and verify
    normalized columns.
    """
    adapter = MypyAdapter()
    if not adapter.is_available():
        pytest.skip("mypy is not installed")

    code = 'a = "🚀"; b: int = "bad"\nc = "café"; d: int = "bad"\ne = "α"; f: int = "bad"\n'
    src_file = tmp_path / "real_unicode.py"
    src_file.write_text(code, encoding="utf-8")

    raw = adapter.run([str(src_file)], cwd=str(tmp_path))
    diagnostics = adapter.parse(raw)

    assert len(diagnostics) == 3
    lines = code.splitlines()

    for d in diagnostics:
        line_text = lines[d.start_line - 1]
        assert d.start_col is not None
        assert d.end_col is not None
        # Extract the token using 1-indexed code-point slice: [start_col - 1 : end_col - 1]
        token = line_text[d.start_col - 1 : d.end_col - 1]
        assert token == '"bad"'
        assert d.canonical_start_col == d.start_col - 1
        assert d.canonical_end_col == d.end_col - 1
        assert not d.column_uncertain
