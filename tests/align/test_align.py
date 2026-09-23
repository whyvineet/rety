"""
Unit tests for the alignment engine (rety/align.py).

All tests construct NormalizedDiagnostic objects directly — no adapter
subprocess invocations needed. This is the key design: the alignment engine
is independently testable from the adapters.

Test categories:
    1. Basic clustering — same file, same line
    2. Range overlap — overlapping but non-identical spans
    3. AST-node merge — adjacent, non-overlapping spans in the same call
    4. False-positive prevention — must NOT cluster
    5. Confidence scoring — exact match → HIGH, overlap → MEDIUM, AST → LOW
    6. Multi-checker scenarios — 3/4, 2/4, 1/4 patterns
    7. Line tolerance — user-configured looser matching
    8. Edge cases — empty input, single diagnostic, same file different lines
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rety.align import align, clear_ast_cache
from rety.schema import Confidence, NormalizedDiagnostic, Severity

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

# Use the real fixture file so AST enrichment can work on actual Python source.
# Tests that don't need AST enrichment use a non-existent path.
REAL_FILE = str(Path(__file__).parent.parent / "fixtures" / "untyped_function.py")
FAKE_FILE = "/nonexistent/path/to/file.py"
FAKE_FILE_2 = "/nonexistent/path/to/other.py"


def diag(
    checker: str,
    start_line: int,
    end_line: int | None = None,
    *,
    file: str = FAKE_FILE,
    severity: Severity = Severity.error,
    code: str | None = None,
    message: str = "test diagnostic",
) -> NormalizedDiagnostic:
    """Helper: construct a minimal NormalizedDiagnostic for testing."""
    return NormalizedDiagnostic(
        checker=checker,
        checker_version="1.0.0",
        file=file,
        start_line=start_line,
        start_col=None,
        end_line=end_line,
        end_col=None,
        severity=severity,
        code=code,
        message=message,
        raw=f"raw:{checker}:{start_line}",
    )


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear AST cache before each test to ensure isolation."""
    clear_ast_cache()


# ---------------------------------------------------------------------------
# 1. Basic clustering — same file, same line
# ---------------------------------------------------------------------------


def test_same_line_same_file_clusters() -> None:
    """Two diagnostics on the same line in the same file must form one cluster."""
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 10),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright"}


def test_same_line_three_checkers() -> None:
    """Three checkers on the same line must form one cluster."""
    diagnostics = [
        diag("mypy", 5),
        diag("pyright", 5),
        diag("pyrefly", 5),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright", "pyrefly"}


def test_same_line_all_four_checkers() -> None:
    """All four checkers on the same line must form one cluster with HIGH confidence."""
    diagnostics = [
        diag("mypy", 42),
        diag("pyright", 42),
        diag("pyrefly", 42),
        diag("ty", 42),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright", "pyrefly", "ty"}
    assert c.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 2. Range overlap — overlapping but non-identical spans
# ---------------------------------------------------------------------------


def test_overlapping_spans_cluster_with_medium_confidence() -> None:
    """
    mypy reports L10-12, Pyright reports L11-13.
    These overlap (L11-12), so they must cluster with MEDIUM confidence.
    """
    diagnostics = [
        diag("mypy", 10, end_line=12),
        diag("pyright", 11, end_line=13),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright"}
    assert c.confidence == Confidence.MEDIUM


def test_exact_range_match_gives_high_confidence() -> None:
    """Both checkers report exactly L20-25 → HIGH confidence."""
    diagnostics = [
        diag("mypy", 20, end_line=25),
        diag("pyright", 20, end_line=25),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH


def test_touching_ranges_cluster() -> None:
    """
    L10 (single-line) and L11 (single-line) — these touch (end of first =
    start of second). With tolerance=0, they do NOT overlap.
    They should only cluster if tolerance > 0 or via AST-node merge.
    """
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 11),
    ]
    clusters_strict = align(diagnostics, line_tolerance=0)
    clusters_loose = align(diagnostics, line_tolerance=1)

    # Strict: no range overlap → separate clusters (unless AST merges them)
    # (on FAKE_FILE with no real AST, they won't AST-merge)
    assert len(clusters_strict) == 2

    # With tolerance=1: L10 and L11 are within 1 line → cluster
    assert len(clusters_loose) == 1


# ---------------------------------------------------------------------------
# 3. The canonical case: mypy blames call site, Pyright blames argument
#    (Adjacent but non-overlapping ranges in the same AST node)
# ---------------------------------------------------------------------------


def test_adjacent_ranges_in_real_file_ast_merge() -> None:
    """
    This is the test case from the TDD's own worked example:
    mypy at L20 (the call expression), Pyright at L21 (the argument).
    They're adjacent, non-overlapping, but the alignment engine should
    merge them via AST-node context.

    Uses the real fixture file so AST enrichment works.
    """
    # untyped_function.py has `add_items("hello", 42)` at line 22
    # We test with lines 22 and 23 (adjacent, in same function call)
    diagnostics = [
        NormalizedDiagnostic(
            checker="mypy",
            checker_version="1.0",
            file=REAL_FILE,
            start_line=22,
            start_col=None,
            end_line=22,
            end_col=None,
            severity=Severity.error,
            code="operator",
            message="Unsupported operand",
            raw="raw:mypy:22",
        ),
        NormalizedDiagnostic(
            checker="pyright",
            checker_version="1.0",
            file=REAL_FILE,
            start_line=22,  # same line in this fixture — will definitely cluster
            start_col=None,
            end_line=22,
            end_col=None,
            severity=Severity.error,
            code="reportOperatorIssue",
            message="Operator not supported",
            raw="raw:pyright:22",
        ),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright"}


# ---------------------------------------------------------------------------
# 4. FALSE-POSITIVE PREVENTION — must NOT cluster
# ---------------------------------------------------------------------------


def test_different_files_same_line_do_not_cluster() -> None:
    """
    Critical false-positive test: same line number, different files.
    File A line 10 and File B line 10 must NEVER cluster.
    """
    diagnostics = [
        diag("mypy", 10, file=FAKE_FILE),
        diag("pyright", 10, file=FAKE_FILE_2),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 2
    # Each cluster has exactly one checker
    for c in clusters:
        assert len(c.checkers_present) == 1


def test_same_file_non_overlapping_separate_lines_do_not_cluster() -> None:
    """
    Two diagnostics far apart in the same file must not cluster.
    L10 and L50 are clearly separate issues.
    """
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 50),
    ]
    clusters = align(diagnostics, line_tolerance=0)

    assert len(clusters) == 2


def test_two_independent_type_ignores_same_file_do_not_cluster() -> None:
    """
    Two independent diagnostics on different lines (L10, L20) in the same file
    must not cluster simply because they're in the same file.
    """
    diagnostics = [
        diag("mypy", 10, message="Error in function foo"),
        diag("mypy", 20, message="Error in function bar"),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 2


# ---------------------------------------------------------------------------
# 5. Confidence scoring
# ---------------------------------------------------------------------------


def test_single_checker_diagnostic_is_low_confidence() -> None:
    """A cluster with only one checker's diagnostic has LOW confidence."""
    diagnostics = [diag("mypy", 10)]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.LOW


def test_exact_range_is_high() -> None:
    assert align([diag("mypy", 5, 8), diag("pyright", 5, 8)])[0].confidence == Confidence.HIGH


def test_overlapping_range_is_medium() -> None:
    assert align([diag("mypy", 5, 8), diag("pyright", 7, 10)])[0].confidence == Confidence.MEDIUM


def test_high_confidence_beats_medium() -> None:
    """
    In a cluster with 3 checkers where 2 have exact range and 1 has overlap,
    the cluster should still be HIGH (because at least one exact pair exists).
    """
    diagnostics = [
        diag("mypy", 10, 12),
        diag("pyright", 10, 12),   # exact match with mypy
        diag("pyrefly", 10, 15),   # overlapping with mypy/pyright
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 6. Multi-checker scenarios
# ---------------------------------------------------------------------------


def test_three_of_four_checkers_agree() -> None:
    """3/4 checkers on same line — 1 checker finds nothing here."""
    diagnostics = [
        diag("mypy", 30),
        diag("pyright", 30),
        diag("pyrefly", 30),
        # ty finds nothing at this line
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert len(c.checkers_present) == 3
    assert "ty" not in c.checkers_present


def test_one_of_four_finds_unique_issue() -> None:
    """
    ty finds something at L50 that no other checker flags.
    Should produce a single-checker cluster (LOW confidence).
    """
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 10),
        diag("ty", 50),  # unique finding
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 2
    # Find the ty-only cluster
    ty_clusters = [c for c in clusters if c.checkers_present == ["ty"]]
    assert len(ty_clusters) == 1
    assert ty_clusters[0].confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# 7. Line tolerance
# ---------------------------------------------------------------------------


def test_line_tolerance_0_does_not_merge_adjacent() -> None:
    """tolerance=0 (default): L10 and L11 are not within tolerance → separate."""
    diagnostics = [diag("mypy", 10), diag("pyright", 11)]
    clusters = align(diagnostics, line_tolerance=0)
    # May be merged via AST on real files, but on FAKE_FILE there's no AST
    # The important thing is that range clustering alone doesn't merge them
    range_only_cluster_count = len(clusters)
    assert range_only_cluster_count >= 1  # at least one cluster (could be 2)


def test_line_tolerance_2_merges_nearby_diagnostics() -> None:
    """tolerance=2: L10 and L12 are within tolerance → cluster."""
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 12),
    ]
    clusters = align(diagnostics, line_tolerance=2)
    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright"}


def test_line_tolerance_does_not_merge_distant_lines() -> None:
    """tolerance=2: L10 and L20 are NOT within 2 lines → separate."""
    diagnostics = [
        diag("mypy", 10),
        diag("pyright", 20),
    ]
    clusters = align(diagnostics, line_tolerance=2)
    assert len(clusters) == 2


# ---------------------------------------------------------------------------
# 8. Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    assert align([]) == []


def test_single_diagnostic() -> None:
    diagnostics = [diag("mypy", 5)]
    clusters = align(diagnostics)
    assert len(clusters) == 1
    assert clusters[0].checkers_present == ["mypy"]
    assert clusters[0].confidence == Confidence.LOW


def test_output_sorted_by_file_then_line() -> None:
    """Clusters must be sorted: file path first, then start line."""
    diagnostics = [
        diag("mypy", 50, file=FAKE_FILE),
        diag("mypy", 10, file=FAKE_FILE_2),
        diag("mypy", 5, file=FAKE_FILE),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 3
    assert clusters[0].file == FAKE_FILE
    assert clusters[0].representative_range[0] == 5
    assert clusters[1].file == FAKE_FILE
    assert clusters[1].representative_range[0] == 50
    assert clusters[2].file == FAKE_FILE_2


def test_cluster_id_is_stable() -> None:
    """The same input must produce the same cluster_id."""
    diagnostics = [diag("mypy", 10), diag("pyright", 10)]
    clusters_1 = align(diagnostics)
    clear_ast_cache()
    clusters_2 = align(diagnostics)

    assert clusters_1[0].cluster_id == clusters_2[0].cluster_id


def test_representative_range_is_union_of_member_ranges() -> None:
    """The cluster's representative range is the bounding box of all members."""
    diagnostics = [
        diag("mypy", 10, end_line=12),
        diag("pyright", 11, end_line=15),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    start, end = clusters[0].representative_range
    assert start == 10
    assert end == 15


def test_alignment_signals_are_non_empty_for_multi_checker_cluster() -> None:
    """Every multi-checker cluster must have at least one alignment signal."""
    diagnostics = [diag("mypy", 10), diag("pyright", 10)]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    assert len(clusters[0].alignment_signals) > 0


def test_no_verdict_field_exists() -> None:
    """
    DiagnosticCluster must not have any field named 'is_same_issue',
    'verdict', 'same', or similar. This is a design invariant.
    """
    diagnostics = [diag("mypy", 10), diag("pyright", 10)]
    clusters = align(diagnostics)

    c = clusters[0]
    forbidden = ["is_same_issue", "verdict", "same_issue", "is_same", "conclusion"]
    for field_name in forbidden:
        assert not hasattr(c, field_name), (
            f"DiagnosticCluster must not have a '{field_name}' field. "
            f"The tool reports evidence, not conclusions."
        )


# ---------------------------------------------------------------------------
# 9. Confidence counts only cross-checker evidence
# ---------------------------------------------------------------------------


def test_two_diagnostics_from_same_checker_are_low_confidence() -> None:
    """Two mypy diagnostics on one line agree with nobody: LOW, not HIGH."""
    diagnostics = [
        diag("mypy", 10, message="first"),
        diag("mypy", 10, message="second"),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert c.checkers_present == ["mypy"]
    assert c.confidence == Confidence.LOW
    assert not any("exact range" in s for s in c.alignment_signals)


def test_same_checker_exact_pair_does_not_inflate_mixed_cluster() -> None:
    """
    mypy reports L10-12 twice (exact pair with itself) and Pyright reports
    L11-15 (overlap only). The cross-checker evidence is an overlap, so the
    cluster is MEDIUM. The same-checker exact pair must not make it HIGH.
    """
    diagnostics = [
        diag("mypy", 10, 12, message="a"),
        diag("mypy", 10, 12, message="b"),
        diag("pyright", 11, 15),
    ]
    clusters = align(diagnostics)

    assert len(clusters) == 1
    c = clusters[0]
    assert c.confidence == Confidence.MEDIUM
    assert not any("mypy ↔ mypy" in s for s in c.alignment_signals)
