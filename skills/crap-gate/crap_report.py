"""Text and JSON rendering of an engine Result. Stdlib only."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from crap_model import Scored


@dataclass
class TargetRun:
    name: str
    report: str
    ran: bool
    seconds: float


@dataclass
class Result:
    base: str
    base_ref: str
    thresholds: dict[str, float]
    targets: list[TargetRun]
    functions: list[Scored]
    not_measured: list[str]

    @property
    def summary(self) -> dict[str, int]:
        return {
            "fail": sum(1 for s in self.functions if s.status == "FAIL"),
            "ok": sum(1 for s in self.functions if s.status == "ok"),
            "not_measured": len(self.not_measured),
        }


def _line(s: Scored) -> str:
    text = (
        f"{s.status:<4}  {s.func.file}:{s.func.start}  {s.func.name}  CC {s.func.cc}  "
        f"cov {round(s.coverage * 100)}%  CRAP {s.crap:.1f}  limit {s.limit:g} ({s.kind})"
    )
    if s.hint:
        text += f"  → {s.hint}"
    if s.note:
        text += f"  [{s.note}]"
    return text


def render_text(result: Result) -> str:
    lines = [
        f"CRAP gate  base {result.base[:7]} (merge-base HEAD..{result.base_ref})  "
        f"limits: existing > {result.thresholds['existing']:g}, new > {result.thresholds['new']:g}"
    ]
    for t in result.targets:
        state = f"ran, {t.seconds:.0f}s" if t.ran else "reused"
        lines.append(f"{t.name:<9} report {t.report}  ({state})")
    lines.append("")
    if result.functions:
        lines.extend(_line(s) for s in sorted(result.functions, key=lambda s: -s.crap))
    else:
        lines.append("no changed functions in scope")
    if result.not_measured:
        lines.append("")
        lines.append("Not measured: " + ", ".join(result.not_measured))
    summary = result.summary
    lines.append("")
    lines.append(f"Summary: {summary['fail']} FAIL, {summary['ok']} ok, {summary['not_measured']} not measured")
    return "\n".join(lines) + "\n"


def render_json(result: Result) -> str:
    functions = []
    for s in sorted(result.functions, key=lambda s: -s.crap):
        functions.append(
            {
                "target": s.target,
                "file": s.func.file,
                "name": s.func.name,
                "start": s.func.start,
                "end": s.func.end,
                "cc": s.func.cc,
                "coverage": round(s.coverage, 4),
                "crap": round(s.crap, 1),
                "kind": s.kind,
                "limit": s.limit,
                "status": s.status,
                "hint": s.hint,
                "note": s.note,
            }
        )
    payload = {
        "base": result.base,
        "base_ref": result.base_ref,
        "thresholds": result.thresholds,
        "targets": [asdict(t) for t in result.targets],
        "functions": functions,
        "not_measured": result.not_measured,
        "summary": result.summary,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
