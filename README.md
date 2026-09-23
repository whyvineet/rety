# rety

> [!WARNING]
> **Disclaimer:** The current codebase is largely written with the assistance of AI and is still in active development. You may encounter abnormal behavior or bugs. This disclaimer will be removed once all code has been fully verified and stabilized.


**One codebase. Multiple type checkers. One honest view.**

`rety` runs [mypy](https://mypy-lang.org/), [Pyright](https://github.com/microsoft/pyright), [Pyrefly](https://pyrefly.org/), and [ty](https://github.com/astral-sh/ty) on the same Python code, normalizes their diagnostics into a shared schema, and shows you where they agree, where they disagree, and — as precisely as the data allows — why.

```text
$ rety check src/

  src/api.py ────────────────────────────────────────────────
  3/4  * HIGH  L42  in handle_request  [Call]
      mypy      error    42:18  Argument 1 to "process" has incompatible type "str"; expected "int" [arg-type]
      pyright   error    42:18  Argument of type "str" cannot be assigned to parameter "x" of type "int" [reportArgumentType]
      pyrefly   error    42:18  Expected `int`, got `str` [bad-argument-type]

  1/4  - LOW   L87  in validate  [Call]
      ty        error    87:5   Object of type `None` is not callable [call-non-callable]

  2 cluster(s)  1 seen by multiple checkers  1 seen by only one checker
```

---

## The problem rety solves

Python typing has no single ground truth. The four major type checkers implement the same specification (PEP 484 and friends) with materially different inference philosophies, conformance levels, and diagnostic vocabularies. They will disagree even on correct code, and for legitimate reasons.

rety doesn't tell you which checker is right. It turns four independent opinions about the same code into one navigable, honest picture of where they agree, where they disagree, and how confident the alignment is. The "why" — "these three diagnostics all attach to the same call expression" — is the part that's actually useful.

Concrete use cases:
- **Checker migration** (e.g., mypy → Pyrefly): see exactly which mypy errors have Pyrefly counterparts before committing to the switch
- **Cross-checker validation**: run two checkers as mutual cross-checks without fully replacing either
- **Empirical research**: study checker disagreement patterns across a corpus without trusting any single tool's output

---

## Install

```bash
pipx install rety
# or
uv tool install rety
```

rety does **not** install the type checkers itself. You need the checkers you want to run already installed and on `$PATH` (`pip install mypy pyright pyrefly ty` covers all four). Any selected checker that is missing is skipped with a notice on stderr that names it and shows the install command; pass `--require-all` to fail instead.

---

## Usage

```bash
# Run all available checkers on a directory
rety check src/

# Run specific checkers only
rety check --checker mypy,pyright src/

# Machine-readable JSON output
rety check --format json src/ > report.json
rety check --format json --output report.json src/

# Looser line matching (useful if checkers report adjacent lines)
rety check --line-tolerance 1 src/

# Show alignment signals per cluster (why it was grouped)
rety check --verbose src/mymodule.py

# Fail if any selected checker is not installed
rety check --require-all --checker mypy,pyright src/

# Gate CI: exit 1 if any checker reported an error (or `any` diagnostic)
rety check --fail-on error src/

# Give slow checkers more (or unlimited) time; default is 600 seconds each
rety check --timeout 0 src/
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | Run completed and the `--fail-on` threshold was not met |
| 1    | The `--fail-on` threshold was met |
| 2    | Usage error, no checker available, or `--require-all` not satisfied |

By default (`--fail-on none`) rety exits 0 whenever the run itself succeeded, regardless of what the checkers found.

---

## How it works

1. **Run** — each checker is invoked as a subprocess, concurrently, with its own native config discovery intact (`mypy.ini`, `pyrightconfig.json`, `pyrefly.toml`, `ty.toml`) relative to the directory where you ran `rety`.
2. **Normalize** — each checker's output is parsed into a shared `NormalizedDiagnostic` schema: mypy's JSON-lines, Pyright's and Pyrefly's JSON documents, ty's `concise` text. Positions are unified to 1-indexed lines and columns (Pyright and mypy report 0-indexed columns; rety converts). The parsers are tested against real captured output; the checker versions they were verified against are pinned in [tests/CHECKER_VERSIONS.md](tests/CHECKER_VERSIONS.md).
3. **Align** — diagnostics are grouped per file by line-range overlap. Two clusters on adjacent lines are merged only when a diagnostic from each sits inside the *same instance* of a multi-line statement or call (the classic "mypy blames the call, Pyright blames the argument two lines down" case). Two unrelated statements that merely look alike are never merged.
4. **Report** — every cluster shows `N/M` (checkers that reported here / checkers that ran), a confidence badge, and, with `--verbose`, the exact signal that produced the grouping.

Confidence reflects cross-checker evidence only:

| Badge    | Meaning |
|----------|---------|
| `* HIGH` | Two different checkers report exactly the same line range |
| `~ MED`  | Two different checkers report overlapping line ranges |
| `- LOW`  | Joined only through a shared enclosing statement, `--line-tolerance`, or a single checker reported here |

rety never collapses a cluster to a "same issue: yes/no" verdict. The output is always "N/M checkers found something here, here's what each said, here's how confident the grouping is."

---

## Development

```bash
uv sync --all-groups          # installs rety in editable mode plus mypy, pytest, ruff
uv run pytest -q              # unit tests; no type checker needs to be installed
uv run mypy src               # strict
uv run ruff check src tests

# Run rety against all four real checkers without installing them globally
uv run --with pyright --with pyrefly --with ty rety check --require-all tests/fixtures/basic_errors.py
```

The adapter tests read real captured checker output from `tests/fixtures/captured/`. When a checker release changes its output format, recapture with the commands in [tests/CHECKER_VERSIONS.md](tests/CHECKER_VERSIONS.md) and update the pinned versions there.

---

## License

[MIT](LICENSE)
