"""
rety — Python type checker cross-comparison tool.

Library use:

    from rety import ALL_ADAPTERS, run_checkers
    from rety.align import align

    adapters = [ALL_ADAPTERS[name]() for name in ("mypy", "pyright")]
    results = run_checkers(adapters, paths=["src"], cwd="/path/to/project")
    diagnostics = [d for r in results if r.succeeded for d in r.diagnostics]
    for cluster in align(diagnostics, root="/path/to/project"):
        print(cluster.checkers_present, cluster.confidence, cluster.file)
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("rety")
except PackageNotFoundError:  # source checkout that has not been installed
    __version__ = "0.0.0+unknown"

# `align` is deliberately not re-exported: a package attribute named
# `align` would shadow the rety.align submodule (`import rety.align as m`
# would return the function). Use `from rety.align import align`.
from rety.adapters import ALL_ADAPTERS, CheckerAdapter  # noqa: E402
from rety.runner import CheckerResult, run_checkers  # noqa: E402
from rety.schema import (  # noqa: E402
    ComparisonReport,
    Confidence,
    DiagnosticCluster,
    NormalizedDiagnostic,
    Severity,
)

__all__ = [
    "ALL_ADAPTERS",
    "CheckerAdapter",
    "CheckerResult",
    "ComparisonReport",
    "Confidence",
    "DiagnosticCluster",
    "NormalizedDiagnostic",
    "Severity",
    "__version__",
    "run_checkers",
]
