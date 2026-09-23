"""
rety — Python type checker cross-comparison tool.
"""

from importlib.metadata import PackageNotFoundError, version as _package_version

try:
    __version__ = _package_version("rety")
except PackageNotFoundError:  # source checkout that has not been installed
    __version__ = "0.0.0+unknown"
