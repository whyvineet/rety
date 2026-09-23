# Checker Versions

This file records the exact pinned versions of each type checker used to
generate the golden-file test fixtures in `tests/golden/`.

**The golden files in `tests/golden/` are only valid for the versions listed here.**

A checker version bump that changes output shape will break golden tests loudly,
which is the intended signal. When updating a checker version:
1. Run all four checkers on the fixture set to capture new output.
2. Update `tests/fixtures/captured/` with fresh captures.
3. Update the expected clusters in `tests/golden/` to match the new behavior.
4. Update this file with the new versions.
5. Note any schema changes in the changelog entry for the adapter.

---

## Current pinned versions

> ⚠ These versions are placeholders. Run the commands below before generating
> golden files and replace with actual output.

```
# Run these commands and paste the output here:
mypy --version
pyright --version
pyrefly --version
ty version
```

| Checker  | Version | Last verified |
|----------|---------|---------------|
| mypy     | TBD     | TBD           |
| pyright  | TBD     | TBD           |
| pyrefly  | TBD     | TBD           |
| ty       | TBD     | TBD           |

---

## How to update

```bash
# Create a test venv with pinned checkers
uv venv .venv-checkers
uv pip install --python .venv-checkers mypy pyright pyrefly ty

# Capture versions
.venv-checkers/bin/mypy --version
.venv-checkers/bin/pyright --version
.venv-checkers/bin/pyrefly --version
.venv-checkers/bin/ty version

# Capture output for the untyped_function fixture (example)
.venv-checkers/bin/mypy --output=json --no-incremental --cache-dir=/tmp/rety-mypy-test \
    tests/fixtures/untyped_function.py \
    > tests/fixtures/captured/mypy/untyped_function.jsonl

.venv-checkers/bin/pyright --outputjson tests/fixtures/untyped_function.py \
    > tests/fixtures/captured/pyright/untyped_function.json

.venv-checkers/bin/pyrefly check --output-format json tests/fixtures/untyped_function.py \
    > tests/fixtures/captured/pyrefly/untyped_function.json

.venv-checkers/bin/ty check --output-format concise tests/fixtures/untyped_function.py \
    > tests/fixtures/captured/ty/untyped_function.txt
```

---

## Notes on checker-specific behaviors

### mypy
- `--no-incremental` is passed by rety to prevent stale-cache flag override (known bug in 1.20.x)
- Syntax errors may produce plain-text lines mixed into JSON output (known bug #17660)

### Pyright
- Uses 0-indexed LSP-style line/character ranges; rety converts to 1-indexed
- `--outputjson` suppresses interactive progress output

### Pyrefly
- Schema stability across monthly releases is unproven; capture output and verify field names
- Native SARIF 2.1.0 output available via `--output-format sarif` (relevant for v0.2)

### ty
- Pre-1.0 as of September 2026; output format may change between releases
- `ty version --output-format json` provides structured version info
- `ty explain rule --output-format json <code>` is the v0.2 crosswalk data source
- The `concise` format regex in `rety/adapters/ty.py` was derived from documentation,
  not from verified real output — Phase 1 must verify and update the regex
