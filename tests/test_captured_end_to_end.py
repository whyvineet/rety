"""
End-to-end alignment tests over real captured checker output.

For every fixture in tests/fixtures/ that has captures from all four checkers
(tests/fixtures/captured/<checker>/), parse each capture with its adapter,
align the results, and compare a compact summary of the clusters against a
reviewed snapshot in tests/fixtures/expected/<fixture>.json.

A snapshot diff means alignment behaviour changed on real checker output.
If the change is intended, regenerate the snapshots and review the diff:

    RETY_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_captured_end_to_end.py
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

import pytest

from rety.adapters import ALL_ADAPTERS
from rety.align import align, clear_ast_cache
from rety.schema import DiagnosticCluster, NormalizedDiagnostic, RawInvocation

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = FIXTURES / "captured"
EXPECTED = FIXTURES / "expected"
REPO_ROOT = Path(__file__).parent.parent

_EXTENSIONS = {"mypy": ".jsonl", "pyright": ".json", "pyrefly": ".json", "ty": ".txt"}

FIXTURE_NAMES = sorted(
    path.stem
    for path in FIXTURES.glob("*.py")
    if all((CAPTURED / c / f"{path.stem}{ext}").exists() for c, ext in _EXTENSIONS.items())
)


def _parse_capture(checker: str, fixture: str) -> list[NormalizedDiagnostic]:
    """Parse one captured output; every diagnostic is about the fixture file."""
    stdout = (CAPTURED / checker / f"{fixture}{_EXTENSIONS[checker]}").read_text(encoding="utf-8")
    raw = RawInvocation(checker, 1, stdout, "", 0.0, "captured", cwd=str(REPO_ROOT))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an unrecognised line fails the test
        diagnostics = ALL_ADAPTERS[checker]().parse(raw)

    # Captures were taken on Windows (backslashes, a d:\ drive); pin every
    # diagnostic to this machine's fixture path so the test runs anywhere.
    target = str((FIXTURES / f"{fixture}.py").resolve())
    return [d.model_copy(update={"file": target}) for d in diagnostics]


def _summarize(cluster: DiagnosticCluster) -> dict[str, Any]:
    codes: dict[str, list[str | None]] = {}
    for d in cluster.diagnostics:
        codes.setdefault(d.checker, []).append(d.code)
    return {
        "lines": list(cluster.representative_range),
        "checkers": cluster.checkers_present,
        "confidence": cluster.confidence.value,
        "node": cluster.enclosing_node_type,
        "scope": cluster.enclosing_scope,
        "codes": codes,
    }


def test_every_fixture_has_captures() -> None:
    assert FIXTURE_NAMES == sorted(path.stem for path in FIXTURES.glob("*.py"))


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_alignment_of_captured_output_matches_snapshot(fixture: str) -> None:
    clear_ast_cache()
    diagnostics = [d for checker in _EXTENSIONS for d in _parse_capture(checker, fixture)]
    summary = [_summarize(c) for c in align(diagnostics)]

    snapshot = EXPECTED / f"{fixture}.json"
    if os.environ.get("RETY_UPDATE_SNAPSHOTS"):
        EXPECTED.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    assert summary == json.loads(snapshot.read_text(encoding="utf-8"))
