"""
JSON renderer for rety comparison reports.

Produces a stable, versioned JSON document suitable for CI consumption,
downstream scripting, and long-term storage. The schema_version field is
embedded so consumers can detect and reject version mismatches.

Output structure:
    {
        "schema_version": 1,
        "checkers_run": ["mypy", "pyright", ...],
        "checker_versions": {"mypy": "1.x.x", ...},
        "total_diagnostics": {"mypy": 12, ...},
        "clusters": [
            {
                "cluster_id": "abc123def456",
                "file": "/abs/path/to/file.py",
                "representative_range": [42, 42],
                "enclosing_node_type": "Call",
                "enclosing_scope": "handle_request",
                "checkers_present": ["mypy", "pyright"],
                "confidence": "high",
                "alignment_signals": ["exact range L42-42: mypy ↔ pyright"],
                "diagnostics": [...]
            }
        ]
    }

Stability guarantee:
    Field names and structure are stable within schema_version 1. Adding new
    optional fields to NormalizedDiagnostic or DiagnosticCluster is backward-
    compatible. Removing or renaming fields, or changing the semantics of existing
    fields, requires a schema_version bump.
"""

from __future__ import annotations

import json

from rety.schema import ComparisonReport


def render(report: ComparisonReport) -> str:
    """
    Serialize a ComparisonReport to a formatted JSON string.

    The output is deterministic for a given input (sorted keys within dicts
    are not guaranteed, but pydantic's model_dump preserves field declaration
    order, which is stable). Suitable for diffing in CI.

    Args:
        report: The comparison report to serialize.

    Returns:
        Pretty-printed JSON string (2-space indent, no trailing newline).
    """
    return json.dumps(
        report.model_dump(mode="json"),
        indent=2,
        ensure_ascii=False,
    )


def render_to_file(report: ComparisonReport, path: str) -> None:
    """
    Write a ComparisonReport as JSON to a file.

    Args:
        report: The comparison report to serialize.
        path:   Output file path. Will be created or overwritten.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(report))
        f.write("\n")  # trailing newline for POSIX compliance
