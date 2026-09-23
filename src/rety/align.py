"""
rety diagnostic alignment engine.

Turns a flat list of NormalizedDiagnostic objects from multiple checkers into
a list of DiagnosticCluster objects, each representing a group of diagnostics
that likely refer to the same code issue.

Pipeline (five stages):
    1. Group by file — partition all diagnostics by their normalized file path.
    2. AST enrichment — parse each file once (cached by content hash) and build
       a per-line index of statement and Call nodes. Each diagnostic is tagged
       with its innermost enclosing node type, its enclosing function/class,
       and the set of "anchor" nodes (simple statements and Call expressions)
       whose span contains its position.
    3. Range-overlap clustering — greedy sweep: diagnostics whose line spans
       overlap (or are within line_tolerance lines) are merged.
    4. AST-anchor merge pass — merge two adjacent clusters only if a member of
       each lies inside the *same instance* of a multi-line anchor node. This
       is the canonical "mypy blames the call on line 20, Pyright blames the
       argument on line 21" case: both positions sit inside one Call node that
       spans lines 20-22. Two unrelated statements that merely share a node
       *type* are never merged.
    5. Confidence scoring — HIGH/MEDIUM/LOW from cross-checker pairwise
       evidence, with a human-readable alignment_signal per pair.

Design invariants:
    - Never collapses to a verdict. DiagnosticCluster has no "is_same_issue" field.
    - All clustering is deterministic and fully explainable via alignment_signals.
    - Every layer is independently testable: pass NormalizedDiagnostic objects
      directly; no subprocesses are needed.
    - AST enrichment is best-effort: if a file can't be parsed (syntax error,
      non-UTF-8, binary, missing), diagnostics still cluster by range and the
      enclosing_* fields stay None.

Performance:
    - One parse and one index build per unique file content, cached for the
      life of the process.
    - Per-diagnostic lookup scans only the nodes covering that one line, not
      the whole tree.
    - Clustering is O(D²) per file in the worst case; D per file is small.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rety.schema import (
    Confidence,
    DiagnosticCluster,
    NormalizedDiagnostic,
    make_cluster_id,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Node types that can anchor a cross-line merge. Simple statements and Call
# expressions span several lines only through brackets or continuations, so
# two positions inside the same instance are tightly related. Compound
# statements (If, For, FunctionDef, ...) are deliberately excluded: their span
# covers a whole body.
_ANCHOR_TYPES: frozenset[str] = frozenset(
    {
        "Call",
        "Assign",
        "AnnAssign",
        "AugAssign",
        "Return",
        "Expr",
        "Raise",
        "Assert",
        "Delete",
    }
)

_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Maximum line gap between two clusters for an anchor merge. Keeps a 40-line
# call with diagnostics at both ends from merging on anchor evidence alone.
_AST_MERGE_PROXIMITY = 3


# ---------------------------------------------------------------------------
# AST index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    """Source span of one AST node. Lines are 1-indexed; columns follow the
    ast module convention (0-indexed start, exclusive 0-indexed end)."""

    node_type: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    depth: int

    @property
    def is_anchor(self) -> bool:
        return self.node_type in _ANCHOR_TYPES

    @property
    def is_multiline(self) -> bool:
        return self.end_line > self.start_line

    def contains(self, line: int, col0: int | None) -> bool:
        """True if (line, col0) is inside this span. col0 None = line-only test."""
        if not (self.start_line <= line <= self.end_line):
            return False
        if col0 is None:
            return True
        if line == self.start_line and col0 < self.start_col:
            return False
        if line == self.end_line and col0 >= self.end_col:
            return False
        return True

    def label(self) -> str:
        return f"{self.node_type} L{self.start_line}-{self.end_line}"


@dataclass
class _FileIndex:
    """Per-line lookup tables built from one parsed file."""

    nodes_by_line: dict[int, list[_Span]]
    scopes_by_line: dict[int, list[tuple[int, str]]]  # (depth, name)


@dataclass(frozen=True)
class _Enriched:
    """A diagnostic plus the anchor spans that contain its position."""

    diag: NormalizedDiagnostic
    anchors: frozenset[_Span]


_index_cache: dict[str, _FileIndex | None] = {}


def _build_index(tree: ast.Module) -> _FileIndex:
    """Index every statement and Call node by the lines it covers."""
    nodes_by_line: dict[int, list[_Span]] = defaultdict(list)
    scopes_by_line: dict[int, list[tuple[int, str]]] = defaultdict(list)

    # Explicit stack instead of recursion: deeply nested expressions can
    # exceed the interpreter recursion limit.
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        for child in ast.iter_child_nodes(node):
            child_depth = depth + 1
            stack.append((child, child_depth))

            start = getattr(child, "lineno", None)
            end = getattr(child, "end_lineno", None)
            if start is None or end is None:
                continue

            if isinstance(child, (ast.stmt, ast.Call)):
                end_col = child.end_col_offset
                span = _Span(
                    node_type=type(child).__name__,
                    start_line=start,
                    start_col=child.col_offset,
                    end_line=end,
                    end_col=end_col if end_col is not None else child.col_offset,
                    depth=child_depth,
                )
                for line in range(start, end + 1):
                    nodes_by_line[line].append(span)

            if isinstance(child, _SCOPE_TYPES):
                for line in range(start, end + 1):
                    scopes_by_line[line].append((child_depth, child.name))

    return _FileIndex(dict(nodes_by_line), dict(scopes_by_line))


def _get_index(file_path: str) -> _FileIndex | None:
    """
    Parse and index a file, cached by SHA-256 of its content.

    Returns None if the file can't be read or parsed. Callers must handle None.
    """
    try:
        content = Path(file_path).read_bytes()
    except OSError:
        return None

    key = hashlib.sha256(content).hexdigest()
    if key in _index_cache:
        return _index_cache[key]

    index: _FileIndex | None
    try:
        tree = ast.parse(content.decode("utf-8", errors="replace"))
        index = _build_index(tree)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        index = None

    _index_cache[key] = index
    return index


def _lookup(
    index: _FileIndex,
    line: int,
    col1: int | None,
) -> tuple[str | None, str | None, frozenset[_Span]]:
    """
    Find the innermost node, the enclosing scope and the anchor spans for a
    position. col1 is 1-indexed (schema convention) or None.

    If the column is known but falls outside every node on that line (a
    checker pointing at leading whitespace, say), fall back to a line-only
    match rather than returning nothing.
    """
    spans = index.nodes_by_line.get(line, [])
    col0 = col1 - 1 if col1 is not None else None

    containing = [s for s in spans if s.contains(line, col0)]
    if not containing and col0 is not None:
        containing = [s for s in spans if s.contains(line, None)]

    innermost = max(containing, key=lambda s: s.depth, default=None)
    node_type = innermost.node_type if innermost is not None else None

    scopes = index.scopes_by_line.get(line, [])
    scope = max(scopes, key=lambda s: s[0])[1] if scopes else None

    anchors = frozenset(s for s in containing if s.is_anchor)
    return node_type, scope, anchors


# ---------------------------------------------------------------------------
# AST enrichment
# ---------------------------------------------------------------------------


def _enrich_diagnostics(
    diagnostics: list[NormalizedDiagnostic],
    file_path: str,
) -> list[_Enriched]:
    """
    Attach enclosing_node_type and enclosing_scope to each diagnostic and
    pair it with its anchor spans. Returns new objects (the model is frozen).

    If the file can't be indexed, the diagnostics are returned unchanged with
    no anchors.
    """
    index = _get_index(file_path)
    if index is None:
        return [_Enriched(diag, frozenset()) for diag in diagnostics]

    enriched: list[_Enriched] = []
    for diag in diagnostics:
        node_type, scope, anchors = _lookup(index, diag.start_line, diag.start_col)
        updated = diag.model_copy(
            update={"enclosing_node_type": node_type, "enclosing_scope": scope}
        )
        enriched.append(_Enriched(updated, anchors))
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
    True if d1 and d2 are in the same file and their line ranges overlap or
    are within `tolerance` lines of each other.
    """
    if d1.file != d2.file:
        return False
    s1, e1 = _effective_range(d1)
    s2, e2 = _effective_range(d2)
    return s1 <= e2 + tolerance and s2 <= e1 + tolerance


# ---------------------------------------------------------------------------
# Greedy range-overlap clustering
# ---------------------------------------------------------------------------


def _cluster_by_range(
    items: list[_Enriched],
    tolerance: int,
) -> list[list[_Enriched]]:
    """
    Greedy single-pass clustering: sort by start_line, then add each
    diagnostic to the first cluster in which it overlaps (within tolerance)
    with at least one member. The sort is stable, so diagnostics on the same
    line keep their input order, which keeps output deterministic.
    """
    ordered = sorted(items, key=lambda e: e.diag.start_line)
    clusters: list[list[_Enriched]] = []

    for item in ordered:
        for cluster in clusters:
            if any(_ranges_overlap(item.diag, member.diag, tolerance) for member in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    return clusters


# ---------------------------------------------------------------------------
# AST-anchor merge pass
# ---------------------------------------------------------------------------


def _shared_anchor(e1: _Enriched, e2: _Enriched) -> _Span | None:
    """The innermost multi-line anchor node containing both positions, if any."""
    shared = [a for a in e1.anchors & e2.anchors if a.is_multiline]
    return max(shared, key=lambda a: a.depth) if shared else None


def _should_merge_by_ast(c1: list[_Enriched], c2: list[_Enriched]) -> bool:
    """
    True if some member of c1 and some member of c2 lie inside the same
    multi-line anchor node instance, and the clusters are close together.
    """
    max_line1 = max(_effective_range(e.diag)[1] for e in c1)
    min_line2 = min(e.diag.start_line for e in c2)
    if min_line2 - max_line1 > _AST_MERGE_PROXIMITY:
        return False

    return any(_shared_anchor(e1, e2) is not None for e1 in c1 for e2 in c2)


def _merge_clusters_by_ast(clusters: list[list[_Enriched]]) -> list[list[_Enriched]]:
    """
    Iteratively merge adjacent clusters that share an anchor node instance.
    Clusters arrive sorted by start_line; only neighbours are considered.
    """
    if len(clusters) <= 1:
        return clusters

    changed = True
    while changed:
        changed = False
        merged: list[list[_Enriched]] = []
        i = 0
        while i < len(clusters):
            if i + 1 < len(clusters) and _should_merge_by_ast(clusters[i], clusters[i + 1]):
                merged.append(clusters[i] + clusters[i + 1])
                i += 2
                changed = True
            else:
                merged.append(clusters[i])
                i += 1
        clusters = merged

    return clusters


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _score_cluster(
    members: list[_Enriched],
    tolerance: int,
) -> tuple[Confidence, list[str]]:
    """
    Assign a confidence level and produce alignment signals for a cluster.

    Confidence rules:
        HIGH   — at least one cross-checker pair with exactly matching line ranges
        MEDIUM — at least one cross-checker pair with overlapping ranges (not exact)
        LOW    — cross-checker pairs were joined only through a shared anchor
                 node, line tolerance, or other members; or the cluster holds
                 diagnostics from a single checker only

    Only pairs from *different* checkers count. Two diagnostics from the same
    checker on the same line say nothing about agreement between checkers, so
    they never raise confidence and never produce a pairwise signal.

    Alignment signals explain exactly how each cross-checker pair was matched.
    """
    signals: list[str] = []

    checkers = sorted({e.diag.checker for e in members})
    if len(checkers) == 1:
        if len(members) == 1:
            signals.append(f"single diagnostic from {checkers[0]}")
        else:
            signals.append(
                f"{len(members)} diagnostics from {checkers[0]} only; "
                f"no other checker reported here"
            )
        return Confidence.LOW, signals

    has_exact = False
    has_overlap = False

    for e1, e2 in itertools.combinations(members, 2):
        d1, d2 = e1.diag, e2.diag
        if d1.checker == d2.checker:
            continue  # same-checker pairs carry no cross-checker evidence

        r1 = _effective_range(d1)
        r2 = _effective_range(d2)
        pair = f"{d1.checker} L{d1.start_line} ↔ {d2.checker} L{d2.start_line}"

        if r1 == r2:
            has_exact = True
            signals.append(f"exact range L{r1[0]}-{r1[1]}: {d1.checker} ↔ {d2.checker}")
        elif _ranges_overlap(d1, d2, tolerance=0):
            has_overlap = True
            signals.append(
                f"overlapping ranges: {d1.checker} L{r1[0]}-{r1[1]} "
                f"↔ {d2.checker} L{r2[0]}-{r2[1]}"
            )
        elif (anchor := _shared_anchor(e1, e2)) is not None:
            signals.append(f"same {anchor.label()}: {pair}")
        elif tolerance and _ranges_overlap(d1, d2, tolerance):
            signals.append(f"within {tolerance} line(s): {pair}")
        else:
            signals.append(f"linked through other members: {pair}")

    # A checker with two diagnostics on one line yields the same pairwise
    # signal twice; keep the first occurrence so --verbose stays readable.
    signals = list(dict.fromkeys(signals))

    if has_exact:
        return Confidence.HIGH, signals
    if has_overlap:
        return Confidence.MEDIUM, signals
    return Confidence.LOW, signals


# ---------------------------------------------------------------------------
# Cluster assembly
# ---------------------------------------------------------------------------


def _most_common(values: list[str]) -> str | None:
    return max(set(values), key=values.count) if values else None


def _build_cluster(
    members: list[_Enriched],
    file_path: str,
    tolerance: int,
) -> DiagnosticCluster:
    """Construct a DiagnosticCluster from a list of enriched members."""
    diags = [e.diag for e in members]
    start_line = min(d.start_line for d in diags)
    end_line = max(_effective_range(d)[1] for d in diags)

    confidence, signals = _score_cluster(members, tolerance)

    return DiagnosticCluster(
        cluster_id=make_cluster_id(file_path, start_line, end_line),
        file=file_path,
        representative_range=(start_line, end_line),
        enclosing_node_type=_most_common(
            [d.enclosing_node_type for d in diags if d.enclosing_node_type]
        ),
        enclosing_scope=_most_common([d.enclosing_scope for d in diags if d.enclosing_scope]),
        diagnostics=diags,
        checkers_present=sorted({d.checker for d in diags}),
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

    Deterministic: the same input always produces the same output.
    Independently testable: pass synthetic NormalizedDiagnostic objects
    directly to exercise clustering without any checker subprocess.

    Args:
        diagnostics:    All normalized diagnostics from all checkers.
        line_tolerance: How many lines apart two diagnostics can be and still
                        be considered range-overlapping. Default 0 (overlap or
                        touching only).

    Returns:
        List of DiagnosticCluster, sorted by file path then representative
        start line. Empty if diagnostics is empty.
    """
    if not diagnostics:
        return []

    # Stage 1: group by file
    by_file: dict[str, list[NormalizedDiagnostic]] = defaultdict(list)
    for diag in diagnostics:
        by_file[diag.file].append(diag)

    all_clusters: list[DiagnosticCluster] = []

    for file_path, file_diags in sorted(by_file.items()):
        # Stage 2: AST enrichment (one parse per file content, cached)
        enriched = _enrich_diagnostics(file_diags, file_path)

        # Stage 3: range-overlap clustering
        raw_clusters = _cluster_by_range(enriched, tolerance=line_tolerance)

        # Stage 4: AST-anchor merge pass (adjacent-line near-misses)
        merged_clusters = _merge_clusters_by_ast(raw_clusters)

        # Stage 5: build DiagnosticCluster objects with confidence scores
        for members in merged_clusters:
            if members:
                all_clusters.append(_build_cluster(members, file_path, line_tolerance))

    all_clusters.sort(key=lambda c: (c.file, c.representative_range[0]))
    return all_clusters


def clear_ast_cache() -> None:
    """
    Clear the module-level AST index cache.

    Useful in tests that write different content to the same path. Not needed
    in production: the cache is keyed by file content, so it is always correct.
    """
    _index_cache.clear()
