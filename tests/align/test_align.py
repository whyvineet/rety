"""
Unit tests for the alignment engine (rety/align.py).

All tests construct NormalizedDiagnostic objects directly; no adapter
subprocess is invoked. Tests that need AST context point at real fixture
files under tests/fixtures/; the rest use a path that does not exist, so
enrichment is skipped and only range logic is exercised.

Test categories:
    1. Basic clustering — same file, same line
    2. Range overlap — overlapping but non-identical spans
    3. AST-anchor merge — adjacent lines inside one multi-line statement
    4. False-positive prevention — must NOT cluster
    5. Confidence scoring — exact → HIGH, overlap → MEDIUM, anchor/single → LOW
    6. Multi-checker scenarios — 3/4, 2/4, 1/4 patterns
    7. Line tolerance — user-configured looser matching
    8. Edge cases — empty input, single diagnostic, ordering, stable IDs
    9. Confidence counts only cross-checker evidence
   10. AST enrichment — node type and scope detection
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rety.align import align, clear_ast_cache
from rety.schema import Confidence, NormalizedDiagnostic, Severity

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent.parent / "fixtures"
MULTILINE_FILE = str(FIXTURES / "multiline_call.py")
FAKE_FILE = "/nonexistent/path/to/file.py"
FAKE_FILE_2 = "/nonexistent/path/to/other.py"

_MULTILINE_LINES = Path(MULTILINE_FILE).read_text(encoding="utf-8").splitlines()


def _line_of(text: str, after: int = 0) -> int:
    """1-indexed line number of the first line equal to `text` after line `after`."""
    return _MULTILINE_LINES.index(text, after) + 1


def diag(
    checker: str,
    start_line: int,
    end_line: int | None = None,
    *,
    file: str = FAKE_FILE,
    col: int | None = None,
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
        start_col=col,
        end_line=end_line,
        end_col=None,
        severity=severity,
        code=code,
        message=message,
        raw=f"raw:{checker}:{start_line}",
    )


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear the AST index cache before each test to ensure isolation."""
    clear_ast_cache()


# ---------------------------------------------------------------------------
# 1. Basic clustering — same file, same line
# ---------------------------------------------------------------------------


def test_same_line_same_file_clusters() -> None:
    clusters = align([diag("mypy", 10), diag("pyright", 10)])

    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright"}


def test_same_line_three_checkers() -> None:
    clusters = align([diag("mypy", 5), diag("pyright", 5), diag("pyrefly", 5)])

    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright", "pyrefly"}


def test_same_line_all_four_checkers() -> None:
    clusters = align([diag("mypy", 42), diag("pyright", 42), diag("pyrefly", 42), diag("ty", 42)])

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright", "pyrefly", "ty"}
    assert c.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 2. Range overlap — overlapping but non-identical spans
# ---------------------------------------------------------------------------


def test_overlapping_spans_cluster_with_medium_confidence() -> None:
    """mypy L10-12 and Pyright L11-13 overlap on L11-12 → MEDIUM."""
    clusters = align([diag("mypy", 10, end_line=12), diag("pyright", 11, end_line=13)])

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright"}
    assert c.confidence == Confidence.MEDIUM


def test_exact_range_match_gives_high_confidence() -> None:
    clusters = align([diag("mypy", 20, end_line=25), diag("pyright", 20, end_line=25)])

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH


def test_touching_ranges_cluster_only_with_tolerance() -> None:
    """L10 and L11 do not overlap; they cluster only when tolerance >= 1."""
    diagnostics = [diag("mypy", 10), diag("pyright", 11)]

    assert len(align(diagnostics, line_tolerance=0)) == 2
    assert len(align(diagnostics, line_tolerance=1)) == 1


# ---------------------------------------------------------------------------
# 3. AST-anchor merge: call site vs. argument inside one multi-line Call
# ---------------------------------------------------------------------------


def test_multiline_call_merges_call_site_and_argument() -> None:
    """
    mypy blames the call expression on its first line, Pyright blames the
    offending argument two lines down. Both positions sit inside the same
    Call node → one cluster, LOW confidence, explained by a 'same Call' signal.
    """
    call_line = _line_of("process(")
    arg_line = _line_of("    42,")

    clusters = align(
        [
            diag("mypy", call_line, file=MULTILINE_FILE, message="incompatible arg"),
            diag("pyright", arg_line, file=MULTILINE_FILE, message="int not assignable"),
        ]
    )

    assert len(clusters) == 1
    c = clusters[0]
    assert set(c.checkers_present) == {"mypy", "pyright"}
    assert c.confidence == Confidence.LOW
    assert c.representative_range == (call_line, arg_line)
    assert any(s.startswith("same Call L") for s in c.alignment_signals)


def test_nested_call_argument_merges_with_outer_call() -> None:
    """A diagnostic inside a nested call still shares the outer Call anchor."""
    first_call = _line_of("process(")
    call_line = _line_of("process(", after=first_call)
    arg_line = _line_of("    list(map(str, [1, 2])),")

    clusters = align(
        [
            diag("mypy", call_line, file=MULTILINE_FILE, col=1),
            diag("pyright", arg_line, file=MULTILINE_FILE, col=10),  # inside map(...)
        ]
    )

    assert len(clusters) == 1
    assert any(s.startswith("same Call L") for s in clusters[0].alignment_signals)


def test_far_apart_lines_in_same_statement_do_not_merge(tmp_path: Path) -> None:
    """Diagnostics inside one Call but far apart in lines are not merged on anchor alone."""
    src = "process(\n" + "".join(f"    {i},\n" for i in range(10)) + ")\n"
    f = tmp_path / "long_call.py"
    f.write_text(src, encoding="utf-8")

    clusters = align([diag("mypy", 1, file=str(f)), diag("pyright", 11, file=str(f))])
    assert len(clusters) == 2


# ---------------------------------------------------------------------------
# 4. FALSE-POSITIVE PREVENTION — must NOT cluster
# ---------------------------------------------------------------------------


def test_different_files_same_line_do_not_cluster() -> None:
    clusters = align([diag("mypy", 10, file=FAKE_FILE), diag("pyright", 10, file=FAKE_FILE_2)])

    assert len(clusters) == 2
    for c in clusters:
        assert len(c.checkers_present) == 1


def test_same_file_non_overlapping_separate_lines_do_not_cluster() -> None:
    assert len(align([diag("mypy", 10), diag("pyright", 50)], line_tolerance=0)) == 2


def test_two_independent_diagnostics_same_file_do_not_cluster() -> None:
    clusters = align([diag("mypy", 10, message="in foo"), diag("mypy", 20, message="in bar")])
    assert len(clusters) == 2


def test_unrelated_adjacent_statements_of_same_kind_do_not_merge() -> None:
    """
    Two one-line assignments two lines apart share a node *type* (Assign)
    but are different statements. They must stay separate clusters.
    """
    first = _line_of("first = 1")
    third = _line_of("third = 3.0")

    clusters = align(
        [diag("mypy", first, file=MULTILINE_FILE), diag("pyright", third, file=MULTILINE_FILE)]
    )

    assert len(clusters) == 2
    assert all(len(c.checkers_present) == 1 for c in clusters)


def test_adjacent_lines_in_different_statements_do_not_merge() -> None:
    first = _line_of("first = 1")
    second = _line_of('second = "two"')

    clusters = align(
        [diag("mypy", first, file=MULTILINE_FILE), diag("ty", second, file=MULTILINE_FILE)]
    )

    assert len(clusters) == 2


# ---------------------------------------------------------------------------
# 5. Confidence scoring
# ---------------------------------------------------------------------------


def test_single_checker_diagnostic_is_low_confidence() -> None:
    clusters = align([diag("mypy", 10)])

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.LOW
    assert clusters[0].alignment_signals == ["single diagnostic from mypy"]


def test_exact_range_is_high() -> None:
    assert align([diag("mypy", 5, 8), diag("pyright", 5, 8)])[0].confidence == Confidence.HIGH


def test_overlapping_range_is_medium() -> None:
    assert align([diag("mypy", 5, 8), diag("pyright", 7, 10)])[0].confidence == Confidence.MEDIUM


def test_high_confidence_beats_medium() -> None:
    """One exact cross-checker pair makes the whole cluster HIGH."""
    clusters = align([diag("mypy", 10, 12), diag("pyright", 10, 12), diag("pyrefly", 10, 15)])

    assert len(clusters) == 1
    assert clusters[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 6. Multi-checker scenarios
# ---------------------------------------------------------------------------


def test_three_of_four_checkers_agree() -> None:
    clusters = align([diag("mypy", 30), diag("pyright", 30), diag("pyrefly", 30)])

    assert len(clusters) == 1
    assert len(clusters[0].checkers_present) == 3
    assert "ty" not in clusters[0].checkers_present


def test_one_of_four_finds_unique_issue() -> None:
    clusters = align([diag("mypy", 10), diag("pyright", 10), diag("ty", 50)])

    assert len(clusters) == 2
    ty_clusters = [c for c in clusters if c.checkers_present == ["ty"]]
    assert len(ty_clusters) == 1
    assert ty_clusters[0].confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# 7. Line tolerance
# ---------------------------------------------------------------------------


def test_line_tolerance_0_does_not_merge_adjacent() -> None:
    assert len(align([diag("mypy", 10), diag("pyright", 11)], line_tolerance=0)) == 2


def test_line_tolerance_2_merges_nearby_diagnostics() -> None:
    clusters = align([diag("mypy", 10), diag("pyright", 12)], line_tolerance=2)

    assert len(clusters) == 1
    assert set(clusters[0].checkers_present) == {"mypy", "pyright"}


def test_line_tolerance_does_not_merge_distant_lines() -> None:
    assert len(align([diag("mypy", 10), diag("pyright", 20)], line_tolerance=2)) == 2


def test_tolerance_merge_signal_names_the_tolerance() -> None:
    """A pair joined only by --line-tolerance says so; it is LOW confidence."""
    clusters = align([diag("mypy", 10), diag("pyright", 12)], line_tolerance=2)

    c = clusters[0]
    assert c.confidence == Confidence.LOW
    assert c.alignment_signals == ["within 2 line(s): mypy L10 ↔ pyright L12"]


def test_transitive_tolerance_link_is_labelled() -> None:
    """L10 and L14 are joined only through L12; the signal must not claim proximity."""
    clusters = align([diag("mypy", 10), diag("pyright", 12), diag("ty", 14)], line_tolerance=2)

    assert len(clusters) == 1
    signals = clusters[0].alignment_signals
    assert "linked through other members: mypy L10 ↔ ty L14" in signals
    assert "within 2 line(s): mypy L10 ↔ pyright L12" in signals


# ---------------------------------------------------------------------------
# 8. Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    assert align([]) == []


def test_single_diagnostic() -> None:
    clusters = align([diag("mypy", 5)])

    assert len(clusters) == 1
    assert clusters[0].checkers_present == ["mypy"]
    assert clusters[0].confidence == Confidence.LOW


def test_output_sorted_by_file_then_line() -> None:
    clusters = align(
        [
            diag("mypy", 50, file=FAKE_FILE),
            diag("mypy", 10, file=FAKE_FILE_2),
            diag("mypy", 5, file=FAKE_FILE),
        ]
    )

    assert [(c.file, c.representative_range[0]) for c in clusters] == [
        (FAKE_FILE, 5),
        (FAKE_FILE, 50),
        (FAKE_FILE_2, 10),
    ]


def test_cluster_id_is_stable() -> None:
    diagnostics = [diag("mypy", 10), diag("pyright", 10)]
    clusters_1 = align(diagnostics)
    clear_ast_cache()
    clusters_2 = align(diagnostics)

    assert clusters_1[0].cluster_id == clusters_2[0].cluster_id


def test_member_order_is_deterministic() -> None:
    """Diagnostics on the same line keep their input order inside the cluster."""
    clusters = align([diag("ty", 10), diag("mypy", 10), diag("pyright", 10)])

    assert [d.checker for d in clusters[0].diagnostics] == ["ty", "mypy", "pyright"]


def test_representative_range_is_union_of_member_ranges() -> None:
    clusters = align([diag("mypy", 10, end_line=12), diag("pyright", 11, end_line=15)])

    assert clusters[0].representative_range == (10, 15)


def test_alignment_signals_are_non_empty_for_multi_checker_cluster() -> None:
    clusters = align([diag("mypy", 10), diag("pyright", 10)])
    assert len(clusters[0].alignment_signals) > 0


def test_no_verdict_field_exists() -> None:
    """The cluster carries evidence, never a same-issue verdict."""
    c = align([diag("mypy", 10), diag("pyright", 10)])[0]
    for field_name in ["is_same_issue", "verdict", "same_issue", "is_same", "conclusion"]:
        assert not hasattr(c, field_name)


def test_unparseable_file_still_clusters_by_range(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")

    clusters = align([diag("mypy", 1, file=str(bad)), diag("pyright", 1, file=str(bad))])

    assert len(clusters) == 1
    assert clusters[0].enclosing_node_type is None
    assert clusters[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 9. Confidence counts only cross-checker evidence
# ---------------------------------------------------------------------------


def test_two_diagnostics_from_same_checker_are_low_confidence() -> None:
    """Two mypy diagnostics on one line agree with nobody: LOW, not HIGH."""
    clusters = align([diag("mypy", 10, message="first"), diag("mypy", 10, message="second")])

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
    clusters = align(
        [
            diag("mypy", 10, 12, message="a"),
            diag("mypy", 10, 12, message="b"),
            diag("pyright", 11, 15),
        ]
    )

    assert len(clusters) == 1
    c = clusters[0]
    assert c.confidence == Confidence.MEDIUM
    assert not any("mypy ↔ mypy" in s for s in c.alignment_signals)


# ---------------------------------------------------------------------------
# 10. AST enrichment
# ---------------------------------------------------------------------------


def test_enclosing_node_type_is_call_when_column_points_into_call() -> None:
    call_line = _line_of("process(")
    (c,) = align([diag("mypy", call_line, file=MULTILINE_FILE, col=1)])

    assert c.enclosing_node_type == "Call"
    assert c.enclosing_scope is None  # module level


def test_enclosing_node_type_without_column_prefers_innermost_node() -> None:
    call_line = _line_of("process(")
    (c,) = align([diag("ty", call_line, file=MULTILINE_FILE)])

    assert c.enclosing_node_type == "Call"


def test_enclosing_scope_is_function_name() -> None:
    body_line = _line_of("    pass")
    (c,) = align([diag("ty", body_line, file=MULTILINE_FILE, col=5)])

    assert c.enclosing_scope == "process"
    assert c.enclosing_node_type == "Pass"


def test_enrichment_is_stored_on_each_diagnostic() -> None:
    first = _line_of("first = 1")
    (c,) = align([diag("mypy", first, file=MULTILINE_FILE, col=1)])

    assert c.diagnostics[0].enclosing_node_type == "Assign"


def test_pairwise_signals_are_not_repeated() -> None:
    """Two mypy diagnostics on one line must not duplicate the mypy ↔ pyright signal."""
    clusters = align(
        [diag("mypy", 18, message="a"), diag("mypy", 18, message="b"), diag("pyright", 18)]
    )

    (c,) = clusters
    assert c.alignment_signals == ["exact range L18-18: mypy ↔ pyright"]
