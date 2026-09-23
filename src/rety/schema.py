"""
rety normalized diagnostic schema.

This module defines the shared, versioned data model that all checker adapters
produce and all downstream stages (alignment, reporting) consume.

Schema versioning:
    SCHEMA_VERSION is a monotonically increasing integer. Bump it whenever
    NormalizedDiagnostic or DiagnosticCluster fields change in a
    backward-incompatible way. Comparison reports from different schema versions
    are not directly comparable — the JSON renderer embeds schema_version in its
    output precisely so downstream tools can detect and reject mismatches.

    Current version: 1

Field conventions:
    - All line numbers are 1-indexed. If a checker reports 0-indexed lines
      (Pyright), adapters convert to 1-indexed before constructing this model.
    - Missing end positions are represented as None, never as 0.
      A 0 end_col would silently degrade recall against checkers that do report
      end positions, by appearing to match at column 0.
    - `raw` holds the per-diagnostic source text (the original JSON sub-object
      for JSON-based checkers, the original text line for text-based checkers).
      It is never the full checker output document repeated per diagnostic.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Schema version — bump on any backward-incompatible field change
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Normalized diagnostic severity, mapped from each checker's vocabulary."""

    error = "error"
    warning = "warning"
    note = "note"
    information = "information"


class Confidence(str, Enum):
    """
    Confidence level for a DiagnosticCluster.

    HIGH   — two or more checkers report diagnostics with exactly matching
             source ranges (same start_line, same end_line).
    MEDIUM — two or more checkers report diagnostics with overlapping (but not
             identical) source ranges.
    LOW    — two or more checkers report diagnostics that were merged via shared
             AST enclosing-node context rather than direct range overlap; or a
             cluster containing only a single checker's diagnostic.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Raw invocation result (not a pydantic model — no need for validation here)
# ---------------------------------------------------------------------------


class RawInvocation:
    """
    The raw result of invoking a single checker subprocess.

    Attributes:
        checker:      Checker name ("mypy", "pyright", "pyrefly", "ty").
        returncode:   Subprocess exit code. Non-zero is normal when diagnostics
                      are found — adapters must not treat it as an error.
        stdout:       Full stdout text from the checker.
        stderr:       Full stderr text from the checker.
        duration_ms:  Wall-clock time for the subprocess, in milliseconds.
        version:      Detected checker version string, or None if detection failed.
    """

    __slots__ = ("checker", "returncode", "stdout", "stderr", "duration_ms", "version")

    def __init__(
        self,
        checker: str,
        returncode: int,
        stdout: str,
        stderr: str,
        duration_ms: float,
        version: Optional[str],
    ) -> None:
        self.checker = checker
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.version = version

    def __repr__(self) -> str:
        return (
            f"RawInvocation(checker={self.checker!r}, returncode={self.returncode}, "
            f"duration_ms={self.duration_ms:.1f}, version={self.version!r})"
        )


# ---------------------------------------------------------------------------
# NormalizedDiagnostic — the core schema unit
# ---------------------------------------------------------------------------


class NormalizedDiagnostic(BaseModel):
    """
    A single diagnostic from a single checker, normalized to a shared schema.

    Produced by checker adapters; consumed by the alignment engine and renderers.
    Never modified after construction (model is frozen).

    AST context fields (enclosing_node_type, enclosing_scope) are populated by
    the alignment engine's enrichment stage, not by adapters. Adapters always
    leave them as None.
    """

    model_config = {"frozen": True}

    schema_version: int = Field(default=SCHEMA_VERSION)

    # ---- Checker identity ----
    checker: str
    """Checker name: "mypy", "pyright", "pyrefly", or "ty"."""

    checker_version: Optional[str] = None
    """Detected checker version string (e.g. "1.11.2"), or None."""

    # ---- Location ----
    file: str
    """
    Absolute, normalized file path. Adapters must resolve relative paths
    (e.g., from mypy/ty output) against the invocation cwd.
    """

    start_line: int
    """1-indexed. Always present."""

    start_col: Optional[int] = None
    """1-indexed. None if not reported by the checker (never 0)."""

    end_line: Optional[int] = None
    """1-indexed. None if not reported (never 0). ty's concise format omits this."""

    end_col: Optional[int] = None
    """1-indexed. None if not reported (never 0). ty's concise format omits this."""

    # ---- Classification ----
    severity: Severity

    code: Optional[str] = None
    """
    Checker-native error/rule code (e.g. mypy's "arg-type", Pyright's
    "reportArgumentType", ty's rule name). None if the checker doesn't emit codes
    for this diagnostic.
    """

    code_family: Optional[str] = None
    """
    Cross-checker code family name from the error-code crosswalk table.
    Always None in v0.1 — populated by rety/crosswalk.py in v0.2.
    Locked here so the alignment engine interface doesn't need to change in v0.2.
    """

    message: str
    """Human-readable diagnostic message, as emitted by the checker."""

    # ---- Auditability ----
    raw: str
    """
    Original source text for this diagnostic:
    - JSON-lines checkers (mypy): the raw JSON line
    - JSON-document checkers (Pyright, Pyrefly): json.dumps of the per-diagnostic
      sub-object, NOT the full document repeated per row
    - Text-format checkers (ty): the original text line
    """

    # ---- AST context (populated by alignment engine, not by adapters) ----
    enclosing_node_type: Optional[str] = None
    """
    The type name of the innermost AST node containing start_line
    (e.g., "Call", "Assign", "Return", "FunctionDef"). None until enrichment.
    """

    enclosing_scope: Optional[str] = None
    """
    The name of the nearest enclosing function or class. None until enrichment,
    or None if the diagnostic is at module level.
    """

    @model_validator(mode="after")
    def _validate_no_zero_positions(self) -> "NormalizedDiagnostic":
        """Ensure 0 is never used to represent a missing position."""
        for field_name in ("start_col", "end_line", "end_col"):
            value = getattr(self, field_name)
            if value == 0:
                raise ValueError(
                    f"NormalizedDiagnostic.{field_name} must be None (not 0) "
                    f"when the position is unknown. "
                    f"Got 0 for checker={self.checker!r}, file={self.file!r}, "
                    f"start_line={self.start_line}."
                )
        return self


# ---------------------------------------------------------------------------
# DiagnosticCluster — the output unit of the alignment engine
# ---------------------------------------------------------------------------


class DiagnosticCluster(BaseModel):
    """
    A group of diagnostics from one or more checkers that the alignment engine
    believes refer to the same (or closely related) code issue.

    Design invariant: this model NEVER contains a "same_issue: bool" verdict.
    The cluster carries evidence (confidence, alignment_signals, diagnostics),
    not a conclusion. Interpreting the evidence is the user's job.

    The "N/M agree" framing uses:
      N = len(checkers_present)
      M = number of checkers actually run (from ComparisonReport.checkers_run)
    M is never hardcoded as 4 — it reflects which checkers were actually invoked.
    """

    cluster_id: str
    """
    Deterministic 12-character hex ID derived from file + representative range.
    Stable across rety invocations on the same code.
    """

    file: str
    """Absolute path of the file containing this cluster."""

    representative_range: tuple[int, int]
    """
    (start_line, end_line) of the cluster's bounding box, 1-indexed.
    Derived from the union of all member diagnostics' line ranges.
    """

    enclosing_node_type: Optional[str] = None
    """Most common enclosing AST node type among member diagnostics."""

    enclosing_scope: Optional[str] = None
    """Most common enclosing scope name among member diagnostics."""

    diagnostics: list[NormalizedDiagnostic]
    """All diagnostics in this cluster, from all checkers."""

    checkers_present: list[str]
    """
    Sorted list of checker names that contributed at least one diagnostic
    to this cluster. len(checkers_present) is the N in "N/M agree".
    """

    confidence: Confidence
    """
    Confidence level for the alignment — reflects how strong the evidence is
    that these diagnostics refer to the same issue. Not a correctness verdict.
    """

    alignment_signals: list[str]
    """
    Human-readable descriptions of the evidence that produced this cluster.
    Every cluster can be fully explained by its signals — no black-box grouping.
    """


# ---------------------------------------------------------------------------
# ComparisonReport — top-level output of a rety run
# ---------------------------------------------------------------------------


class ComparisonReport(BaseModel):
    """
    The complete output of a single rety invocation.

    Serialized as JSON by the json_report renderer. schema_version is embedded
    so downstream consumers can detect and reject version mismatches.
    """

    schema_version: int = Field(default=SCHEMA_VERSION)

    checkers_run: list[str]
    """Names of the checkers that were actually invoked (not all four, if fewer were available)."""

    checker_versions: dict[str, Optional[str]]
    """Detected version per checker. Value is None if version detection failed."""

    total_diagnostics: dict[str, int]
    """Raw diagnostic count per checker, before clustering."""

    clusters: list[DiagnosticCluster]
    """All clusters, sorted by file then representative start line."""


# ---------------------------------------------------------------------------
# Utility: stable cluster ID
# ---------------------------------------------------------------------------


def make_cluster_id(file: str, start_line: int, end_line: int) -> str:
    """
    Generate a stable, short cluster ID from file + representative range.
    12 hex chars (48 bits) — collision-resistant for any realistic diagnostic count.
    """
    payload = f"{file}:{start_line}:{end_line}".encode()
    return hashlib.sha256(payload).hexdigest()[:12]
