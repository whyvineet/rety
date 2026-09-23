# rety fixture: dict_update_typing.py
#
# TypedDict key and value checking.
# TypedDict is well-specified but checker implementations differ on:
#   - Extra keys not in the TypedDict definition
#   - Missing required keys in literal dicts
#   - Merging TypedDicts (TypedDict inheritance)
#   - Indexing with non-literal string keys
#
# Expected disagreement pattern:
#   Line 24: c["timeout"] = 30 — "timeout" is not a key in Config TypedDict.
#            All checkers should flag this, but error messages differ.
#   Line 25: c["host"] = 42 — host is str, not int.
#            All should flag this.
#   Line 35: Incomplete TypedDict literal — missing required key.

from __future__ import annotations

from typing import TypedDict


class Config(TypedDict):
    host: str
    port: int


class ExtendedConfig(Config, total=False):
    debug: bool
    timeout: int


def use_config(c: Config) -> str:
    return f"{c['host']}:{c['port']}"


def manipulate_config() -> None:
    c: Config = {"host": "localhost", "port": 8080}

    # Extra key not in TypedDict — all checkers should flag
    c["timeout"] = 30    # 'timeout' not in Config

    # Wrong value type for existing key — all should flag
    c["host"] = 42       # str expected, got int

    # Correct update — should be fine
    c["port"] = 9090

    # Missing required key in literal — some checkers catch, some don't
    incomplete: Config = {"host": "localhost"}  # missing 'port'

    # Extended config with optional fields
    ext: ExtendedConfig = {"host": "localhost", "port": 8080, "debug": True}
    ext["timeout"] = 5000  # 'timeout' is in ExtendedConfig — should be fine


def dynamic_key(c: Config, key: str) -> object:
    # Non-literal key access — behavior varies
    return c[key]   # key is str, not Literal["host"] | Literal["port"]
