"""Per-function coverage from coverage.py JSON and istanbul JSON. Stdlib only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from crap_config import Target
from crap_model import Func, ToolError


class Report(Protocol):
    def has_file(self, file: str) -> bool: ...
    def function_coverage(self, func: Func, children: list[Func]) -> float: ...


class CoveragePyReport:
    """coverage.py `json` report (7.x): files[path].functions[qualname] with
    start_line and summary.percent_covered (statements + branches)."""

    def __init__(self, data: dict, root: str) -> None:
        prefix = "" if root in ("", ".") else root.rstrip("/") + "/"
        self._files = {prefix + path: entry for path, entry in data.get("files", {}).items()}

    def has_file(self, file: str) -> bool:
        return file in self._files

    def function_coverage(self, func: Func, children: list[Func]) -> float:
        entries = [e for e in self._files[func.file].get("functions", {}).values() if "start_line" in e]
        exact = [e for e in entries if e["start_line"] == func.start]
        inside = sorted((e for e in entries if func.start <= e["start_line"] <= func.end), key=lambda e: e["start_line"])
        chosen = exact or inside
        if not chosen:
            return 0.0
        return float(chosen[0]["summary"]["percent_covered"]) / 100.0


class IstanbulReport:
    """istanbul `coverage-final.json`: keys are absolute paths; statementMap/s
    and branchMap/b give per-location hit counts."""

    def __init__(self, data: dict, repo_root: Path) -> None:
        self._files: dict[str, dict] = {}
        root = repo_root.resolve()
        for key, entry in data.items():
            p = Path(entry.get("path", key))
            try:
                rel = p.resolve().relative_to(root).as_posix() if p.is_absolute() else p.as_posix()
            except ValueError:
                rel = p.as_posix()
            self._files[rel] = entry

    def has_file(self, file: str) -> bool:
        return file in self._files

    def function_coverage(self, func: Func, children: list[Func]) -> float:
        entry = self._files[func.file]

        def owned(loc: dict) -> bool:
            line = loc["start"]["line"]
            return func.start <= line <= func.end and not any(c.start <= line <= c.end for c in children)

        total = covered = 0
        for sid, loc in entry.get("statementMap", {}).items():
            if owned(loc):
                total += 1
                covered += 1 if entry.get("s", {}).get(sid, 0) > 0 else 0
        for bid, branch in entry.get("branchMap", {}).items():
            loc = branch.get("loc") or (branch.get("locations") or [None])[0]
            if loc and owned(loc):
                counts = entry.get("b", {}).get(bid, [])
                total += len(counts)
                covered += sum(1 for c in counts if c > 0)
        return 1.0 if total == 0 else covered / total


def load_report(target: Target, repo_root: Path) -> Report:
    path = repo_root / target.coverage_report
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ToolError(f"{target.name}: cannot read coverage report {target.coverage_report}: {exc}") from exc
    if target.coverage_format == "coverage.py":
        return CoveragePyReport(data, target.coverage_root)
    return IstanbulReport(data, repo_root)
