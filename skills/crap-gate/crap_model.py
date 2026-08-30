"""Dataclasses shared by every crap-gate module. Stdlib only."""
from __future__ import annotations

from dataclasses import dataclass


class ToolError(Exception):
    """Configuration or tooling problem: the CLI exits 2 with the message."""


@dataclass(frozen=True)
class Func:
    file: str   # repo-relative path with forward slashes
    name: str   # qualified name: "outer.inner", "Class.method", "default"
    start: int  # 1-based first line of the declaration
    end: int    # 1-based last line, inclusive
    cc: int     # cyclomatic complexity, anonymous descendants folded in

    def contains(self, other: "Func") -> bool:
        return other is not self and self.start <= other.start and other.end <= self.end


@dataclass
class Scored:
    target: str
    func: Func
    coverage: float  # 0.0 .. 1.0
    crap: float
    kind: str        # "new" | "existing"
    limit: float
    status: str      # "FAIL" | "ok"
    hint: str        # cheapest way to pass; "" when ok
    note: str = ""   # e.g. "file absent from coverage report"
