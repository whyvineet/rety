"""
rety report package.

Provides terminal and JSON renderers for ComparisonReport.
Both renderers are pure functions over the report model — no side effects
beyond I/O. Formatting concerns never leak into the alignment engine, and
schema concerns never leak backward from renderers into the internal model.
"""
