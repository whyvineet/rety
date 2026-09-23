# Checker Versions

This file records the exact versions of each type checker used to generate
the captured-output fixtures in `tests/fixtures/captured/<checker>/`.

The adapter parser tests in `tests/adapters/` assert against those captures,
so **the captures are only valid for the versions listed here.** A checker
release that changes its output shape will break those tests loudly, which is
the intended signal.

---

## Current pinned versions

| Checker | Version | Captured on | Output format used                                          |
|---------|---------|-------------|-------------------------------------------------------------|
| mypy    | 2.3.1   | 2026-09-24  | `--output=json` JSON-lines; columns 0-indexed               |
| pyright | 1.1.414 | 2026-09-24  | `--outputjson`; 0-indexed LSP ranges; absolute paths        |
| pyrefly | 1.3.1   | 2026-09-24  | `--output-format json`: `{"errors": [...]}`, rule in `name` |
| ty      | 0.0.83  | 2026-09-24  | `--output-format concise`: `path:line:col: sev[rule] msg`   |

Captures were taken on Windows from the repository root, so relative paths in
the mypy and ty captures use backslashes and the Pyright capture contains an
absolute `d:\...` path. Tests compare only file basenames, never full paths.

mypy and Pyrefly pick up this repository's `[tool.mypy] strict = true` when
run from the root. That is why `untyped_function.py` produces
missing-annotation errors in the captures that it would not produce under
default settings.

---

## How to update

When a checker version bump changes its output shape:

1. Recapture every fixture with the new version (commands below).
2. Update the assertions in `tests/adapters/test_<checker>.py` to match.
3. Update the table above.
4. Note the schema change in the adapter's module docstring.

```bash
# Run from the repository root. `uv run --with <tool>` fetches the checker
# into a temporary environment without installing it globally.
for f in untyped_function basic_errors; do
  uv run mypy --output=json --no-incremental --cache-dir="$(mktemp -d)" \
      tests/fixtures/$f.py > tests/fixtures/captured/mypy/$f.jsonl
  uv run --with pyright pyright --outputjson \
      tests/fixtures/$f.py > tests/fixtures/captured/pyright/$f.json
  uv run --with pyrefly pyrefly check --output-format json \
      tests/fixtures/$f.py > tests/fixtures/captured/pyrefly/$f.json
  uv run --with ty ty check --output-format concise \
      tests/fixtures/$f.py > tests/fixtures/captured/ty/$f.txt
done

# Versions
uv run mypy --version
uv run --with pyright pyright --version
uv run --with pyrefly pyrefly --version
uv run --with ty ty version
```

---

## Notes on checker-specific behaviors

### mypy
- JSON `column` and `end_column` are 0-indexed (the text output adds 1);
  rety converts them to 1-indexed. A negative column means unknown.
- rety passes `--no-incremental` and an ephemeral `--cache-dir` so a stale
  cache can never override `--output`.
- mypy 2.x emits syntax errors as JSON (`"code": "syntax"`). The plain-text
  fallback of mypy 1.x (bug #17660) is still tolerated with a warning.

### Pyright
- 0-indexed LSP-style `range`; rety converts to 1-indexed.
- `rule` is omitted (not null) for diagnostics that have no rule, such as
  unknown `# pyright:` directives.
- `--outputjson` suppresses progress output; stdout is one JSON document.
- Any comment line starting with `# pyright:` is parsed as a directive, so
  fixture comments must not start with a checker name and a colon.

### Pyrefly
- `code` is an internal integer (-2 in every observed diagnostic); the rule
  name is in `name`.
- `stop_line`/`stop_column` are the (1-indexed, exclusive) end position.
- Without a `pyrefly.toml`, Pyrefly imports settings from `[tool.mypy]` and
  says so on stderr.
- Native SARIF 2.1.0 output is available via `--output-format sarif`.

### ty
- Pre-1.0; `concise` is text, one diagnostic per line, followed by a
  `Found N diagnostics` or `All checks passed!` summary line on stdout.
- `ty version --output-format json` gives structured version info.
- `ty explain rule --output-format json <code>` is a future crosswalk data
  source.
