#!/usr/bin/env python3
"""crap-gate engine: CRAP score for every function a branch changed.

    sh run.sh [--config PATH] [--base REF] [--threshold-existing N]
                    [--threshold-new N] [--run | --no-run] [--all] [--json]

Exit 0 = no violations, 1 = violations, 2 = configuration/tool error.
Stdlib only; the typescript target additionally needs `node` and the
project's own `typescript` package.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Direct invocation remains useful for development, but should fail clearly
# before importing an analyzer that requires newer AST nodes.
if sys.version_info < (3, 10):
    message = "Use the bundled run.sh launcher; direct execution requires Python >= 3.10"
    print(f"crap: {message}", file=sys.stderr)
    if "--json" in sys.argv:
        print(json.dumps({"error": {"kind": "tooling", "message": message}}))
    sys.exit(2)

import crap_git
import crap_python
import crap_typescript
from crap_config import CODE_SUFFIXES, CONFIG_NAME, Config, Target, find_config, load, matches
from crap_coverage import Report, load_report
from crap_model import Func, ToolError
from crap_report import Result, TargetRun, render_json, render_text
from crap_score import score

BASE_LABEL = "@base"


# ── analysis ────────────────────────────────────────────────────────────────

def _analyze(target: Target, root: Path, items: list[tuple[str, str | None]]) -> dict[str, list[Func]]:
    """items: (label, source_text_or_None-for-working-tree). Returns label → funcs."""
    if target.complexity_tool == "python":
        out: dict[str, list[Func]] = {}
        for label, text in items:
            source = text if text is not None else (root / label.removesuffix(BASE_LABEL)).read_text()
            out[label] = crap_python.functions(source, label)
        return out
    with tempfile.TemporaryDirectory(prefix="crap-gate-") as tmp:
        files: list[tuple[Path, str]] = []
        for index, (label, text) in enumerate(items):
            if text is None:
                files.append((root / label, label))
            else:
                real = label.removesuffix(BASE_LABEL)
                p = Path(tmp) / f"{index}{Path(real).suffix}"
                p.write_text(text)
                files.append((p, label))
        return crap_typescript.functions(files, root / target.complexity_cwd)


def _intersects(func: Func, ranges: list[tuple[int, int]]) -> bool:
    return any(func.start <= hi and lo <= func.end for lo, hi in ranges)


def _score_target(
    target: Target,
    files: list[tuple[str, str | None]],
    cfg: Config,
    base: str,
    report: Report,
    all_functions: bool,
) -> list:
    items: list[tuple[str, str | None]] = []
    base_sources: dict[str, str | None] = {}
    for path, old in files:
        items.append((path, None))
        if not all_functions and old is not None:
            base_sources[path] = crap_git.show(cfg.root, base, old)
            if base_sources[path] is not None:
                items.append((path + BASE_LABEL, base_sources[path]))
    analysed = _analyze(target, cfg.root, items)

    scored = []
    for path, old in files:
        funcs = analysed[path]
        if all_functions:
            touched, base_names = None, {f.name for f in funcs}
        elif old is None or base_sources.get(path) is None:
            touched, base_names = None, set()
        else:
            touched = crap_git.touched_lines(cfg.root, base, path)
            base_names = {f.name for f in analysed[path + BASE_LABEL]}
        for func in funcs:
            if touched is not None and not _intersects(func, touched):
                continue
            kind = "existing" if func.name in base_names else "new"
            children = [g for g in funcs if func.contains(g)]
            if report.has_file(path):
                coverage, note = report.function_coverage(func, children), ""
            else:
                coverage, note = 0.0, "file absent from coverage report"
            scored.append(score(target.name, func, coverage, kind, cfg.thresholds, note))
    return scored


# ── coverage commands ───────────────────────────────────────────────────────

def _maybe_run_coverage(target: Target, root: Path, run_mode: str, needed: bool) -> TargetRun:
    report = root / target.coverage_report
    should_run = run_mode == "run" or (run_mode == "auto" and needed)
    if not should_run:
        if not report.exists():
            raise ToolError(f"{target.name}: report {target.coverage_report} is missing; run without --no-run")
        return TargetRun(target.name, target.coverage_report, ran=False, seconds=0.0)
    if not target.coverage_command:
        raise ToolError(f"{target.name}: coverage.command is not configured")
    log = root / ".crap" / f"{target.name}.log"
    log.parent.mkdir(exist_ok=True)
    before = report.stat().st_mtime_ns if report.exists() else None
    started = time.monotonic()
    # shell=True is deliberate: the command is the repository's own committed
    # config (same trust model as a Makefile or no-mistakes' commands.*), never
    # user input — it needs `cd … && …`, env assignments and pipes.
    with log.open("w") as fh:
        proc = subprocess.run(target.coverage_command, shell=True, cwd=root, stdout=fh, stderr=subprocess.STDOUT)
    seconds = time.monotonic() - started
    fresh = report.exists() and (before is None or report.stat().st_mtime_ns > before)
    if not fresh:
        raise ToolError(
            f"{target.name}: coverage command exited {proc.returncode} and wrote no report "
            f"({target.coverage_report}); see {log.relative_to(root)}"
        )
    if proc.returncode != 0:
        print(
            f"crap: {target.name}: coverage command exited {proc.returncode} (failing tests?) — "
            f"using the report it wrote; see {log.relative_to(root)}",
            file=sys.stderr,
        )
    return TargetRun(target.name, target.coverage_report, ran=True, seconds=seconds)


# ── engine ──────────────────────────────────────────────────────────────────

def run(cfg: Config, run_mode: str = "auto", all_functions: bool = False) -> Result:
    base = crap_git.merge_base(cfg.root, cfg.base)
    if all_functions:
        candidates: list[tuple[str, str | None]] = [(p, p) for p in crap_git.tracked_files(cfg.root)]
    else:
        candidates = crap_git.changed_files(cfg.root, base)

    per_target: dict[str, list[tuple[str, str | None]]] = {t.name: [] for t in cfg.targets}
    not_measured: list[str] = []
    for path, old in candidates:
        if Path(path).suffix not in CODE_SUFFIXES:
            continue
        owner = next((t for t in cfg.targets if matches(t, path)), None)
        if owner is None:
            if not all_functions:
                not_measured.append(path)
            continue
        per_target[owner.name].append((path, old))

    runs: list[TargetRun] = []
    functions = []
    for target in cfg.targets:
        files = per_target[target.name]
        if not files:
            continue
        runs.append(_maybe_run_coverage(target, cfg.root, run_mode, needed=True))
        report = load_report(target, cfg.root)
        functions.extend(_score_target(target, files, cfg, base, report, all_functions))

    return Result(base, cfg.base, cfg.thresholds, runs, functions, not_measured)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crap", description="CRAP score for every function a branch changed.")
    p.add_argument("--config", type=Path, help="path to .crap-gate.json (default: walk up from cwd)")
    p.add_argument("--base", help="git ref to diff against (default from config, else origin/main)")
    p.add_argument("--threshold-existing", type=float)
    p.add_argument("--threshold-new", type=float)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--run", dest="run_mode", action="store_const", const="run", help="always run coverage commands")
    mode.add_argument("--no-run", dest="run_mode", action="store_const", const="no-run", help="reuse existing reports")
    p.set_defaults(run_mode="auto")
    p.add_argument("--all", action="store_true", help="score every function in scope, not only changed ones")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = args.config.resolve() if args.config else find_config(Path.cwd())
        cfg = load(
            config_path,
            {"base": args.base, "threshold_existing": args.threshold_existing, "threshold_new": args.threshold_new},
        )
        toplevel = crap_git.toplevel(cfg.root).resolve()
        if toplevel != cfg.root:
            raise ToolError(
                f"{CONFIG_NAME} must live at the git repository root; it is in "
                f"{cfg.root}, but the root is {toplevel}"
            )
        result = run(cfg, run_mode=args.run_mode, all_functions=args.all)
    except ToolError as exc:
        print(f"crap: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": {"kind": "tooling", "message": str(exc)}}))
        return 2
    sys.stdout.write(render_json(result) if args.json else render_text(result))
    return 1 if result.summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
