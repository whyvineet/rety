<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/rety-logo-dark.svg">
  <img src="./assets/rety-logo-light.svg" alt="rety" width="260">
</picture>

**One codebase. Multiple type checkers. One honest view.**

[![PyPI](https://img.shields.io/pypi/v/rety?style=flat-square&color=green)](https://pypi.org/project/rety/)
[![Python](https://img.shields.io/pypi/pyversions/rety?style=flat-square)](https://pypi.org/project/rety/)
[![Downloads](https://img.shields.io/pypi/dm/rety?style=flat-square&color=green)](https://pepy.tech/projects/rety)
[![CI](https://img.shields.io/github/actions/workflow/status/whyvineet/rety/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/whyvineet/rety/actions)
[![License](https://img.shields.io/github/license/whyvineet/rety?style=flat-square)](LICENSE)

![mypy](https://img.shields.io/badge/mypy-supported-2a6db2?style=flat-square)
![pyright](https://img.shields.io/badge/pyright-supported-2a6db2?style=flat-square)
![pyrefly](https://img.shields.io/badge/pyrefly-supported-2a6db2?style=flat-square)
![ty](https://img.shields.io/badge/ty-supported-2a6db2?style=flat-square)

[Install](#install) · [Usage](#usage) · [Development](#development) · [License](#license)

</div>

> [!WARNING]
> **Work in progress:** This project is still under active development. You may hit bugs or unexpected behavior.

`rety` runs [mypy](https://mypy-lang.org/), [Pyright](https://github.com/microsoft/pyright), [Pyrefly](https://pyrefly.org/), and [ty](https://github.com/astral-sh/ty) on the same Python code, normalizes their diagnostics, and shows where they agree, where they disagree, and why.

```text
$ rety check src/

  src/api.py ────────────────────────────────────────────────
  3/4  * HIGH  L42  in handle_request  [Call]
      mypy      error    42:18  Argument 1 to "process" has incompatible type "str"; expected "int" [arg-type]
      pyright   error    42:18  Argument of type "str" cannot be assigned to parameter "x" of type "int" [reportArgumentType]
      pyrefly   error    42:18  Expected `int`, got `str` [bad-argument-type]
```

## Install

```bash
pipx install rety
# or
uv tool install rety
```

> [!TIP]
> `rety` doesn't bundle the checkers. Run `pip install mypy pyright pyrefly ty` to get all four.

## Usage

```bash
rety check src/                          # run all available checkers
rety check --checker mypy,pyright src/   # run specific checkers
rety check --format json src/            # machine-readable output
rety check --verbose src/                # show why diagnostics were grouped
rety check --fail-on error src/          # exit 1 on errors (for CI)
rety check --python .venv/bin/python src/  # make every checker use your project's environment
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Run completed and the `--fail-on` threshold was not met |
| `1`  | The `--fail-on` threshold was met |
| `2`  | Usage error, no checker available, or `--require-all` not satisfied |
| `3`  | A checker crashed, timed out, or rejected its config (the report is incomplete) |

By default (`--fail-on none`) rety exits `0` whenever the run itself succeeded, regardless of what the checkers found.

### Confidence scores

Confidence reflects cross-checker evidence only:

| Badge    | Meaning |
|----------|---------|
| `* HIGH` | Two different checkers report exactly the same line range |
| `~ MED`  | Two different checkers report overlapping line ranges, or share an enclosing statement/`--line-tolerance` *and* belong to the same code family |
| `- LOW`  | Joined only through a shared enclosing statement or `--line-tolerance` (different code families), or a single checker reported here |

## Use as a library

```python
from rety import ALL_ADAPTERS, run_checkers
from rety.align import align

adapters = [ALL_ADAPTERS[name]() for name in ("mypy", "pyright")]
results = run_checkers(adapters, paths=["src"], cwd="/path/to/project")
diagnostics = [d for r in results if r.succeeded for d in r.diagnostics]
for cluster in align(diagnostics, root="/path/to/project"):
    print(cluster.checkers_present, cluster.confidence, cluster.file)
```

## Development

```bash
uv sync --all-groups
uv run pytest -q
uv run mypy src
uv run ruff check src tests
```

## License

[MIT](LICENSE)
