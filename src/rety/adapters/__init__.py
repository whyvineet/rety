"""
rety adapter package.

Each module in this package implements CheckerAdapter for one type checker.
Import order here is not load order — adapters are instantiated by the CLI
based on the --checker flag.
"""

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.adapters.mypy import MypyAdapter
from rety.adapters.pyrefly import PyreflyAdapter
from rety.adapters.pyright import PyrightAdapter
from rety.adapters.ty import TyAdapter

__all__ = [
    "AdapterCapabilities",
    "CheckerAdapter",
    "MypyAdapter",
    "PyrightAdapter",
    "PyreflyAdapter",
    "TyAdapter",
]

#: Registry of all built-in adapters, keyed by checker name.
ALL_ADAPTERS: dict[str, type[CheckerAdapter]] = {
    "mypy": MypyAdapter,
    "pyright": PyrightAdapter,
    "pyrefly": PyreflyAdapter,
    "ty": TyAdapter,
}
