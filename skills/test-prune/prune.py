#!/usr/bin/env python3
"""test-prune: drive the fixer/reviewer prune loop and the strengthen loop over a target repo.

collect → (fixer) → review-input → (reviewer) → batch → apply (interactive) | prune (unattended);
strengthen-start → (strengthen agent, strengthen-commit per item) → strengthen-verify → (reviewer) → strengthen-finish.
The script owns every git, make and file operation; the agents only write JSON or test files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from prune_repo import BadInput, Fail, Repo, State, read_agent_json
from prune_review import batch, collect, review_input
from prune_runs import apply, prune
from prune_strengthen import strengthen_commit, strengthen_finish, strengthen_start, strengthen_verify

COMMANDS = {
    "collect": collect, "review-input": review_input, "batch": batch, "apply": apply, "prune": prune,
    "strengthen-start": strengthen_start, "strengthen-verify": strengthen_verify,
    "strengthen-commit": strengthen_commit, "strengthen-finish": strengthen_finish,
}


class Parser(argparse.ArgumentParser):
    """An argument error raises BadInput (exit 2, recorded in the run status) instead of exiting."""

    def error(self, message):
        raise BadInput(f"{self.prog}: {message}")


def _common():
    common = Parser(add_help=False)
    common.add_argument("--state", help="run state directory (default: <repo root>/.test-prune, git-ignored)")
    common.add_argument("--date", help="branch date YYYY-MM-DD (default: today, UTC)")
    return common


def build_parser():
    common = _common()
    parser = Parser(prog="prune.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_cmd = sub.add_parser("collect", parents=[common])
    collect_cmd.add_argument("--unattended", action="store_true")
    collect_cmd.add_argument("--protected-only", action="store_true")

    sub.add_parser("review-input", parents=[common]).add_argument("--proposals", required=True)
    sub.add_parser("batch", parents=[common]).add_argument("--reviews", required=True)
    sub.add_parser("apply", parents=[common])
    sub.add_parser("prune", parents=[common])
    sub.add_parser("strengthen-start", parents=[common])
    sub.add_parser("strengthen-commit", parents=[common]).add_argument("--item", required=True)
    sub.add_parser("strengthen-verify", parents=[common])
    sub.add_parser("strengthen-finish", parents=[common]).add_argument("--reviews", required=True)
    return parser


class Context:
    """What every command gets: the repo, the run state, the branch date and its arguments."""

    def __init__(self, args):
        self.args = args
        self.repo = Repo(Path.cwd())
        self.state = State(Path(args.state).resolve() if args.state else self.repo.default_state())
        self.date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.finished = False  # the run is complete: the strengthen loop ended (set by its commands)

    def validate(self) -> None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date):
            raise BadInput(f"--date must be YYYY-MM-DD, got {self.date!r}")

    def base(self, mode: str | None = None) -> dict:
        base = self.state.load("base.json")
        if mode and base["mode"] != mode:
            raise BadInput(f"`{self.args.command}` runs only in {mode} mode; this run was collected in {base['mode']} mode")
        return base

    def reviews(self) -> list:
        return read_agent_json(self.args.reviews, "reviews")

    def proposals(self) -> dict:
        return {"candidates": read_agent_json(self.args.proposals, "candidates"),
                "weak_spots": read_agent_json(self.args.proposals, "weak_spots")}


def _run(ctx: Context) -> tuple[int, str]:
    """The command's exit code and the summary its status reason comes from. Staying off the base branch
    (leave_on found changes the run did not make) is reported and fails a command that would pass."""
    try:
        ctx.validate()
        code, summary = COMMANDS[ctx.args.command](ctx), "\n\n".join(ctx.state.summaries)
    except BadInput as exc:
        print(f"test-prune: {exc}", file=sys.stderr)
        code, summary = 2, f"test-prune: {exc}"
    except Fail as exc:
        ctx.state.emit(f"test-prune: {exc}")
        code, summary = 1, f"test-prune: {exc}"
    except Exception as exc:  # a bug or an operational surprise still fails the run in status.json
        summary = f"test-prune: unexpected {type(exc).__name__}: {exc}"
        ctx.state.emit(summary)
        code = 1
    if ctx.repo.stranded:
        ctx.state.emit(ctx.repo.stranded)
        if code == 0:
            code, summary = 1, ctx.repo.stranded
    return code, summary


RUN = "run"  # the marker entry an unattended run stays red on until it finishes
INCOMPLETE = "test-prune: run incomplete: the strengthen loop has not finished"


def _open(commands: list) -> list:
    """The entries that still fail the run: every exit 1 but the run marker, and every exit 2 (or the run
    marker) its command has not since passed. On exit 2 the session fixes the input and reruns the
    command; the run marker passes when the strengthen loop ends."""
    def clears(c: dict) -> bool:
        return c["exit"] == 2 or c["command"] == RUN
    return [c for i, c in enumerate(commands) if c["exit"] and not (clears(c) and any(
        later["command"] == c["command"] and later["exit"] == 0 for later in commands[i + 1:]))]


def _earlier(previous, command: str) -> list:
    """The run's commands so far: none for `collect` (a new run) or a missing or unreadable status."""
    commands = previous.get("commands") if isinstance(previous, dict) and command != "collect" else None
    return commands if isinstance(commands, list) else []


def run_status(previous, command: str, code: int, summary: str, extra=()) -> dict:
    """The run's status, sticky across commands: the highest exit code still open and its reason (the
    latest on a tie), plus every command in order, then the extra entries. `collect` starts a new run; a
    missing or unreadable status starts fresh rather than failing the command."""
    reason = next((line for line in summary.splitlines() if line.strip()), "")
    commands = _earlier(previous, command) + [{"command": command, "exit": code, "reason": reason}]
    ran_prune = any(c["command"] == "prune" for c in commands)
    commands += [e for e in extra if e["exit"] or ran_prune]  # the run finishes only after prune ran
    worst = max(reversed(_open(commands)), key=lambda c: c["exit"], default={"exit": 0, "reason": reason})
    return {"exit": worst["exit"], "reason": worst["reason"], "commands": commands}


def _record(state: State, command: str, code: int, summary: str, extra=()) -> None:
    """Add the command to <state>/status.json, when the state dir is the run's (see State.owned)."""
    if not state.owned():
        return
    path = state.path / "status.json"
    try:
        previous = json.loads(path.read_text()) if path.exists() else None
    except (OSError, ValueError):
        previous = None
    state.save("status.json", run_status(previous, command, code, summary, extra))


def _markers(ctx: Context) -> list:
    """The run marker: opened by an unattended collect, passed when the strengthen loop ends after
    prune ran (see run_status)."""
    if ctx.args.command == "collect" and ctx.args.unattended:
        return [{"command": RUN, "exit": 1, "reason": INCOMPLETE}]
    return [{"command": RUN, "exit": 0, "reason": "run finished"}] if ctx.finished else []


def _state_arg(argv) -> str | None:
    """The --state value, read off argv without a parser (the arguments just failed to parse)."""
    for i, arg in enumerate(argv):
        if arg == "--state" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--state="):
            return arg[len("--state="):]
    return None


def _record_bad_arguments(argv, exc: BadInput) -> None:
    """An argument error is recorded as exit 2 when the command and the state dir can still be told."""
    command = next((a for a in argv if a in COMMANDS), None)
    state = _state_arg(argv)
    try:
        path = Path(state).resolve() if state else Repo(Path.cwd()).default_state()
    except BadInput:
        return
    if command:
        _record(State(path), command, 2, f"test-prune: {exc}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = build_parser().parse_args(argv)
        ctx = Context(args)
    except BadInput as exc:
        print(f"test-prune: {exc}", file=sys.stderr)
        _record_bad_arguments(argv, exc)
        return 2
    code, summary = _run(ctx)
    _record(ctx.state, args.command, code, summary, _markers(ctx))
    return code


if __name__ == "__main__":
    sys.exit(main())
