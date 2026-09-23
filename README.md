# rety

> [!WARNING]
> **Disclaimer:** The current codebase is largely written with the assistance of AI and is still in active development. You may encounter abnormal behavior or bugs. This disclaimer will be removed once all code has been fully verified and stabilized.


**One codebase. Multiple type checkers. One honest view.**

`rety` runs [mypy](https://mypy-lang.org/), [Pyright](https://github.com/microsoft/pyright), [Pyrefly](https://pyrefly.org/), and [ty](https://github.com/astral-sh/ty) on the same Python code, normalizes their diagnostics into a shared schema, and shows you where they agree, where they disagree, and — as precisely as the data allows — why.

```text
$ rety check src/

  src/api.py ────────────────────────────────────────────────
  3/4 ● HIGH  L42  in handle_request [Call]
       mypy     error   Argument 1 to "process" has incompatible type "str"; expected "int"  [arg-type]
       pyright  error   Argument of type "str" cannot be assigned to parameter "x" of type "int"  [reportArgumentType]
       pyrefly  error   Expected `int`, got `str`  [bad-argument-type]

  1/4 ○ LOW   L87  in validate
       ty       error   Object of type `None` cannot be called  [call-non-callable]

  2 cluster(s) total · 1 seen by multiple checkers
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

rety does **not** install the type checkers itself. You need the checkers you want to run already installed and on `$PATH`. rety will tell you exactly which ones are missing and how to install them.

---

## Usage

```bash
# Run all available checkers on a directory
rety check src/

# Run specific checkers only
rety check --checker mypy,pyright src/

# Machine-readable JSON output
rety check --format json src/ > report.json

# Looser line matching (useful if checkers report adjacent lines)
rety check --line-tolerance 1 src/

# Show alignment signals per cluster
rety check --verbose src/mymodule.py

# Fail if any selected checker is not installed
rety check --require-all --checker mypy,pyright src/
```

---

## How it works

1. **Run** — each checker is invoked as a subprocess with its own native config discovery intact (`mypy.ini`, `pyrightconfig.json`, `pyrefly.toml`, `ty.toml`)
2. **Normalize** — each checker's output (JSON-lines for mypy, JSON for Pyright and Pyrefly, `concise` text for ty) is parsed into a shared `NormalizedDiagnostic` schema
3. **Align** — diagnostics are clustered by file and line-range overlap, with stdlib AST context used to catch near-misses (e.g., mypy blaming the call site, Pyright blaming the argument)
4. **Report** — clusters are shown with a confidence score (`HIGH`/`MED`/`LOW`) and the alignment signal that produced the cluster

rety never collapses a cluster to a "same issue: yes/no" verdict. The output is always "N/M checkers found something here, here's what each said, here's how confident the grouping is."

---

## License

[MIT](LICENSE)
