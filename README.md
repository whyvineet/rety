# rety

**One codebase. Multiple type checkers. One view.**

## About

**rety** is an open-source tool for comparing how different Python type checkers analyze the same codebase.

It aims to run tools such as **mypy, Pyright, Pyrefly, and ty**, normalize their diagnostics, and highlight where they agree or disagree.

```text
Python code
    ↓
mypy ──────┐
Pyright ───┤
Pyrefly ───┤ → rety → comparison
ty ────────┘
```

## Status

🚧 **Pre-alpha: development not started yet.**

## Vision

> **Make differences between Python type checkers easy to see, understand, and investigate.**

## License

TBD
