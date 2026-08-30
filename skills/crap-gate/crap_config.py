"""Discover, validate and expose .crap-gate.json. Stdlib only."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from crap_model import ToolError

CONFIG_NAME = ".crap-gate.json"
CODE_SUFFIXES = {".py", ".ts", ".tsx"}
COMPLEXITY_TOOLS = {"python", "typescript"}
COVERAGE_FORMATS = {"coverage.py", "istanbul"}
DEFAULT_THRESHOLDS = {"existing": 15, "new": 9}
DEFAULT_BASE = "origin/main"


@dataclass(frozen=True)
class Target:
    name: str
    paths: list[str]
    exclude: list[str]
    complexity_tool: str
    complexity_cwd: str
    coverage_format: str
    coverage_report: str
    coverage_command: str
    coverage_root: str


@dataclass
class Config:
    root: Path
    thresholds: dict[str, float]
    base: str
    targets: list[Target]


def find_config(start: Path) -> Path:
    for directory in [start.resolve(), *start.resolve().parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ToolError(f"no {CONFIG_NAME} found in {start} or any parent directory")


def load(path: Path, overrides: dict) -> Config:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc

    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(raw.get("thresholds", {}))
    if overrides.get("threshold_existing") is not None:
        thresholds["existing"] = overrides["threshold_existing"]
    if overrides.get("threshold_new") is not None:
        thresholds["new"] = overrides["threshold_new"]
    for key in ("existing", "new"):
        if not isinstance(thresholds.get(key), (int, float)):
            raise ToolError(f"thresholds.{key} must be a number")

    base = overrides.get("base") or raw.get("base") or DEFAULT_BASE

    targets = [_target(entry, index) for index, entry in enumerate(raw.get("targets", []))]
    if not targets:
        raise ToolError("config needs at least one target")
    names = [t.name for t in targets]
    if len(set(names)) != len(names):
        raise ToolError(f"duplicate target name in {names}")

    return Config(root=path.parent.resolve(), thresholds=thresholds, base=base, targets=targets)


def _target(entry: dict, index: int) -> Target:
    name = entry.get("name") or f"target[{index}]"
    complexity = entry.get("complexity", {})
    coverage = entry.get("coverage", {})
    tool = complexity.get("tool")
    if tool not in COMPLEXITY_TOOLS:
        raise ToolError(f"{name}: complexity.tool must be one of {sorted(COMPLEXITY_TOOLS)}, got {tool!r}")
    fmt = coverage.get("format")
    if fmt not in COVERAGE_FORMATS:
        raise ToolError(f"{name}: coverage.format must be one of {sorted(COVERAGE_FORMATS)}, got {fmt!r}")
    if not coverage.get("report"):
        raise ToolError(f"{name}: coverage.report is required")
    if not entry.get("paths"):
        raise ToolError(f"{name}: paths must list at least one glob")
    return Target(
        name=name,
        paths=list(entry["paths"]),
        exclude=list(entry.get("exclude", [])),
        complexity_tool=tool,
        complexity_cwd=complexity.get("cwd", "."),
        coverage_format=fmt,
        coverage_report=coverage["report"],
        coverage_command=coverage.get("command", ""),
        coverage_root=coverage.get("root", "."),
    )


def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to a regex: `**/` spans directories, `*` and `?`
    never cross `/` (same rules as no-mistakes' ignore_patterns)."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^{out}$")


def matches(target: Target, file: str) -> bool:
    if any(_glob_re(p).match(file) for p in target.exclude):
        return False
    return any(_glob_re(p).match(file) for p in target.paths)
