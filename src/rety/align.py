"""
rety diagnostic alignment engine.

Turns a flat list of NormalizedDiagnostic objects from multiple checkers into
a list of DiagnosticCluster objects, each representing a group of diagnostics
that likely refer to the same code issue.

Pipeline (four stages):
    1. Group by file — partition all diagnostics by their normalized file path.
    2. AST enrichment — parse each file's AST once (cached by content hash),
       attach enclosing_node_type and enclosing_scope to every diagnostic.
    3. Range-overlap clustering — greedy sweep-line algorithm: diagnostics whose
       line spans overlap (or are within line_tolerance lines) are merged into
       the same cluster.
    4. AST-node merge pass — merge separately-formed clusters if their members
       share the same enclosing AST node type and are within a small fixed
       proximity. This catches the canonical "mypy blames the call site, Pyright
       blames the argument" case, where ranges are adjacent but not overlapping.
    5. Confidence scoring — each cluster is scored HIGH/MEDIUM/LOW based on which
       signals produced it, with a list of human-readable alignment_signals.

Design invariants:
    - Never collapses to a verdict. DiagnosticCluster has no "is_same_issue" field.
    - All clustering is deterministic and fully explainable via alignment_signals.
    - Every layer is independently testable: stages 2-5 can be unit-tested by
      constructing NormalizedDiagnostic objects directly (no subprocesses needed).
    - AST enrichment is best-effort: if a file can't be parsed (syntax error,
      non-UTF-8, binary), diagnostics still cluster by range, and
      enclosing_node_type / enclosing_scope remain None.

Performance:
    - AST parsing is cached per file content hash. One parse per unique file
      content, regardless of how many diagnostics reference that file.
    - Clustering is O(D²) in diagnostics per file in the worst case, but D is
      at most a few thousand total, and per-file D is usually tens to low hundreds.
      This is not a performance bottleneck for the target repo size (MVP scope:
      hundreds to low thousands of files).
    - The alignment engine never reads files for checkers' already-computed
      diagnostics — it only reads files to enrich with AST context.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Optional

from rety.schema import (
    Confidence,
    DiagnosticCluster,
    NormalizedDiagnostic,
    make_cluster_id,
)

# ---------------------------------------------------------------------------
# AST cache — per-process, keyed by file content hash
# ---------------------------------------------------------------------------

_ast_cache: dict[str, Optional[ast.Module]] = {}


def _get_ast(file_path: str) -> Optional[ast.Module]:
    """
    Parse and cache the AST for a file, keyed by SHA-256 of its content.

    Returns None if the file can't be read or parsed (syntax error, encoding
    error). Callers must handle None gracefully.
    """
    try:
        content = Path(file_path).read_bytes()
    except OSError:
        return None

    content_hash = hashlib.sha256(content).hexdigest()
    if content_hash in _ast_cache:
        return _ast_cache[content_hash]

    try:
        tree = ast.parse(content.decode("utf-8", errors="replace"))
        _ast_cache[content_hash] = tree
    except SyntaxError:
        _ast_cache[content_hash] = None

    return _ast_cache[content_hash]


def _find_enclosing_info(
    tree: ast.Module,
    line: int,
) -> tuple[Optional[str], Optional[str]]:
    """
    Find the most specific AST node containing `line`, and the innermost scope.

    Returns:
        (enclosing_node_type, enclosing_scope_name)
        Both may be None if line is not covered by any node with positions.

    Algorithm:
        Walk the entire AST once. For each node with lineno/end_lineno that
        contains `line`, track the smallest span (most specific / innermost node).
        Separately track function/class definitions containing `line` — the
        smallest such span is the enclosing scope.
    """
    best_node: Optional[ast.AST] = None
    best_node_span = float("inf")

    scope_candidates: list[tuple[float, str]] = []  # (span, name)

    for node in ast.walk(tree):
        node_start: Optional[int] = getattr(node, "lineno", None)
        node_end: Optional[int] = getattr(node, "end_lineno", None)

        if node_start is None or node_end is None:
            continue
        if not (node_start <= line <= node_end):
            continue

        span = float(node_end - node_start)

        # Track the most specific (smallest span) node
        if span < best_node_span:
            best_node_span = span
            best_node = node

        # Track scopes (functions and classes)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope_candidates.append((span, node.name))

    node_type: Optional[str] = type(best_node).__name__ if best_node else None
    scope_name: Optional[str] = (
        min(scope_candidates, key=lambda x: x[0])[1] if scope_candidates else None
    )

    return node_type, scope_name


# ---------------------------------------------------------------------------
# AST enrichment
# ---------------------------------------------------------------------------


def _enrich_diagnostics(
    diagnostics: list[NormalizedDiagnostic],
    file_path: str,
) -> list[NormalizedDiagnostic]:
    """
    Attach enclosing_node_type and enclosing_scope to each diagnostic.

    Parses the AST once for the file, then enriches all diagnostics for that
    file in a single pass. Returns a new list (NormalizedDiagnostic is frozen).

    If the file can't be parsed, returns the original list unchanged.
    """
    tree = _get_ast(file_path)
    if tree is None:
        return diagnostics

    enriched: list[NormalizedDiagnostic] = []
    for diag in diagnostics:
        node_type, scope = _find_enclosing_info(tree, diag.start_line)
        enriched.append(
            diag.model_copy(update={
                "enclosing_node_type": node_type,
                "enclosing_scope": scope,
            })
        )
    return enriched


# ---------------------------------------------------------------------------
# Range overlap
# ---------------------------------------------------------------------------


def _effective_range(diag: NormalizedDiagnostic) -> tuple[int, int]:
    """Return (start_line, end_line) treating missing end_line as single-line."""
    return diag.start_line, diag.end_line if diag.end_line is not None else diag.start_line


def _ranges_overlap(
    d1: NormalizedDiagnostic,
    d2: NormalizedDiagnostic,
    tolerance: int,
) -> bool:
    """
    Return True if d1 and d2 are in the same file and their line ranges overlap
    or are within `tolerance` lines of each other.

    tolerance=0: strictly overlapping or touching ranges
    tolerance=N: ranges within N lines of each other (useful for checkers that
                 report the same issue on adjacent lines)
    """
    if d1.file != d2.file:
        return False
    s1, e1 = _effective_range(d1)
    s2, e2 = _effective_range(d2)
    # Standard interval overlap: s1 <= e2+tol AND s2 <= e1+tol
    return s1 <= e2 + tolerance and s2 <= e1 + tolerance


# ---------------------------------------------------------------------------
# Greedy range-overlap clustering
# ---------------------------------------------------------------------------


def _cluster_by_range(
    diagnostics: list[NormalizedDiagnostic],
    tolerance: int,
) -> list[list[NormalizedDiagnostic]]:
    """
    Greedy single-pass clustering: sort by start_line, then group diagnostics
    into clusters where each diagnostic overlaps (within tolerance) with at
    least one existing member of the cluster.

    Runs in O(D²) worst case per file, but D per file is small in practice.
    """
    sorted_diags = sorted(diagnostics, key=lambda d: d.start_line)
    clusters: list[list[NormalizedDiagnostic]] = []

    for diag in sorted_diags:
        placed = False
        for cluster in clusters:
            # Check overlap against any member of this cluster
            if any(_ranges_overlap(diag, member, tolerance) for member in cluster):
                cluster.append(diag)
                placed = True
                break
        if not placed:
            clusters.append([diag])

    return clusters


# ---------------------------------------------------------------------------
# AST-node merge pass
# ---------------------------------------------------------------------------

# Maximum line distance between two clusters to consider merging via AST node.
# This is intentionally small and not user-configurable: it catches the
# "blame the call site vs. blame the argument" case (adjacent lines), not
# semantically unrelated diagnostics that happen to be in the same function.
_AST_MERGE_PROXIMITY = 3


def _should_merge_by_ast(
    c1: list[NormalizedDiagnostic],
    c2: list[NormalizedDiagnostic],
) -> bool:
    """
    Return True if c1 and c2 should be merged via shared AST node context.

    Conditions (all must hold):
    1. Both clusters have at least one diagnostic with enclosing_node_type.
    2. They share at least one enclosing_node_type value.
    3. The gap between the two clusters' line ranges is ≤ _AST_MERGE_PROXIMITY.

    This catches cases like:
        mypy: L20 [Call] — blames the call expression
        Pyright: L21 [Arg] — blames the specific argument
    Both are in a Call node, and are 1 line apart → merge with LOW confidence.
    """
    nodes1 = {d.enclosing_node_type for d in c1 if d.enclosing_node_type}
    nodes2 = {d.enclosing_node_type for d in c2 if d.enclosing_node_type}

    if not nodes1 or not nodes2:
        return False
    if not (nodes1 & nodes2):
        return False

    # Clusters are sorted by start_line, so c2 starts after c1
    max_line1 = max(_effective_range(d)[1] for d in c1)
    min_line2 = min(d.start_line for d in c2)

    return (min_line2 - max_line1) <= _AST_MERGE_PROXIMITY


def _merge_clusters_by_ast(
    clusters: list[list[NormalizedDiagnostic]],
) -> list[list[NormalizedDiagnostic]]:
    """
    Iteratively merge clusters that should be joined via AST node context.

    Clusters are already sorted by start_line from the range-clustering step.
    We only attempt to merge adjacent clusters (c[i] and c[i+1]) to avoid
    accidentally merging distant diagnostics.
    """
    if len(clusters) <= 1:
        return clusters

    changed = True
    while changed:
        changed = False
        new_clusters: list[list[NormalizedDiagnostic]] = []
        i = 0
        while i < len(clusters):
            if i + 1 < len(clusters) and _should_merge_by_ast(clusters[i], clusters[i + 1]):
                merged = clusters[i] + clusters[i + 1]
                new_clusters.append(merged)
                i += 2
                changed = True
            else:
                new_clusters.append(clusters[i])
                i += 1
        clusters = new_clusters

    return clusters


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _score_cluster(members: list[NormalizedDiagnostic]) -> tuple[Confidence, list[str]]:
    """
    Assign a confidence level and produce alignment signals for a cluster.

    Confidence rules:
        HIGH   — at least one pair with exactly matching line ranges
        MEDIUM — at least one pair with overlapping ranges (but not exact)
        LOW    — all merges were via AST node context, or single-checker cluster

    Alignment signals explain exactly how each pair was matched.
    """
    signals: list[str] = []

    if len(members) == 1:
        signals.append(f"single diagnostic from {members[0].checker}")
        return Confidence.LOW, signals

    has_exact = False
    has_overlap = False

    for d1, d2 in itertools.combinations(members, 2):
        r1 = _effective_range(d1)
        r2 = _effective_range(d2)

        if r1 == r2:
            has_exact = True
            signals.append(
                f"exact range L{r1[0]}-{r1[1]}: {d1.checker} ↔ {d2.checker}"
            )
        elif _ranges_overlap(d1, d2, tolerance=0):
            has_overlap = True
            signals.append(
                f"overlapping ranges: {d1.checker} L{r1[0]}-{r1[1]} ↔ {d2.checker} L{r2[0]}-{r2[1]}"
            )
        else:
            # Must have been merged via AST node
            node = d1.enclosing_node_type or d2.enclosing_node_type or "unknown"
            signals.append(
                f"same enclosing {node}: {d1.checker} L{d1.start_line} ↔ {d2.checker} L{d2.start_line}"
            )

    if has_exact:
        return Confidence.HIGH, signals
    elif has_overlap:
        return Confidence.MEDIUM, signals
    else:
        return Confidence.LOW, signals


# ---------------------------------------------------------------------------
# Cluster assembly
# ---------------------------------------------------------------------------


def _build_cluster(members: list[NormalizedDiagnostic], file_path: str) -> DiagnosticCluster:
    """Construct a DiagnosticCluster from a list of member diagnostics."""
    start_line = min(d.start_line for d in members)
    end_line = max(_effective_range(d)[1] for d in members)

    checkers = sorted({d.checker for d in members})
    confidence, signals = _score_cluster(members)

    # Enclosing context: use the most common non-None value
    node_types = [d.enclosing_node_type for d in members if d.enclosing_node_type]
    enclosing_node = (
        max(set(node_types), key=node_types.count) if node_types else None
    )

    scopes = [d.enclosing_scope for d in members if d.enclosing_scope]
    enclosing_scope = (
        max(set(scopes), key=scopes.count) if scopes else None
    )

    return DiagnosticCluster(
        cluster_id=make_cluster_id(file_path, start_line, end_line),
        file=file_path,
        representative_range=(start_line, end_line),
        enclosing_node_type=enclosing_node,
        enclosing_scope=enclosing_scope,
        diagnostics=members,
        checkers_present=checkers,
        confidence=confidence,
        alignment_signals=signals,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def align(
    diagnostics: list[NormalizedDiagnostic],
    line_tolerance: int = 0,
) -> list[DiagnosticCluster]:
    """
    Align diagnostics from multiple checkers into clusters.

    This is the main public function of the alignment engine. It is deterministic:
    the same input always produces the same output. It is independently testable:
    pass synthetic NormalizedDiagnostic objects directly to test clustering logic
    without any checker subprocess.

    Args:
        diagnostics:    All normalized diagnostics from all checkers. May include
                        diagnostics from different checkers for the same file.
        line_tolerance: How many lines apart two diagnostics can be and still be
                        considered range-overlapping. Default 0 (exact overlap or
                        touching). Set to 1 or 2 for codebases where checkers
                        habitually report the same issue on adjacent lines.

    Returns:
        List of DiagnosticCluster, sorted by file path then representative
        start line. May be empty if diagnostics is empty.
    """
    if not diagnostics:
        return []

    # Stage 1: group by file
    by_file: dict[str, list[NormalizedDiagnostic]] = defaultdict(list)
    for diag in diagnostics:
        by_file[diag.file].append(diag)

    all_clusters: list[DiagnosticCluster] = []

    for file_path, file_diags in sorted(by_file.items()):
        # Stage 2: AST enrichment (one parse per file, cached)
        enriched = _enrich_diagnostics(file_diags, file_path)

        # Stage 3: range-overlap clustering
        raw_clusters = _cluster_by_range(enriched, tolerance=line_tolerance)

        # Stage 4: AST-node merge pass (catches adjacent-range near-misses)
        merged_clusters = _merge_clusters_by_ast(raw_clusters)

        # Stage 5: build DiagnosticCluster objects with confidence scores
        for members in merged_clusters:
            if not members:
                continue
            all_clusters.append(_build_cluster(members, file_path))

    # Sort: file path first, then start line within file
    all_clusters.sort(key=lambda c: (c.file, c.representative_range[0]))

    return all_clusters


def clear_ast_cache() -> None:
    """
    Clear the module-level AST cache.

    Useful in tests to ensure isolation between test cases that exercise
    different file contents at the same path. Not needed in production
    (the cache is process-scoped and file-content-keyed, so it's always correct).
    """
    _ast_cache.clear()
