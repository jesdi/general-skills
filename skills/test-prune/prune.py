#!/usr/bin/env python3
"""test-prune: drive the fixer/reviewer prune loop and the strengthen loop over a target repo.

collect → (fixer) → review-input → (reviewer) → batch → apply (interactive) | prune (unattended);
strengthen-start → (strengthen agent) → strengthen-verify → (reviewer) → strengthen-finish.
The script owns every git, make and file operation; the agents only write JSON or test commits.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from prune_repo import LAST_SUMMARY, BadInput, Fail, Repo, State, emit, read_agent_json
from prune_review import batch, collect, review_input
from prune_runs import apply, prune
from prune_strengthen import strengthen_finish, strengthen_start, strengthen_verify

COMMANDS = {
    "collect": collect, "review-input": review_input, "batch": batch, "apply": apply, "prune": prune,
    "strengthen-start": strengthen_start, "strengthen-verify": strengthen_verify,
    "strengthen-finish": strengthen_finish,
}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", help="run state directory (default: <git dir>/test-prune)")
    common.add_argument("--date", help="branch date YYYY-MM-DD (default: today, UTC)")

    parser = argparse.ArgumentParser(prog="prune.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_cmd = sub.add_parser("collect", parents=[common])
    collect_cmd.add_argument("--unattended", action="store_true")
    collect_cmd.add_argument("--protected-only", action="store_true")

    sub.add_parser("review-input", parents=[common]).add_argument("--proposals", required=True)
    sub.add_parser("batch", parents=[common]).add_argument("--reviews", required=True)
    sub.add_parser("apply", parents=[common])
    sub.add_parser("prune", parents=[common])
    sub.add_parser("strengthen-start", parents=[common])
    sub.add_parser("strengthen-verify", parents=[common])
    sub.add_parser("strengthen-finish", parents=[common]).add_argument("--reviews", required=True)
    return parser


class Context:
    """What every command gets: the repo, the run state, the branch date and its arguments."""

    def __init__(self, args):
        self.args = args
        self.repo = Repo(Path.cwd())
        self.state = State(Path(args.state).resolve() if args.state else self.repo.git_dir() / "test-prune")
        self.date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
    try:
        ctx.validate()
        return COMMANDS[ctx.args.command](ctx), LAST_SUMMARY[0]
    except BadInput as exc:
        print(f"test-prune: {exc}", file=sys.stderr)
        return 2, f"test-prune: {exc}"
    except Fail as exc:
        emit(f"test-prune: {exc}")
        return 1, f"test-prune: {exc}"


def run_status(previous: dict | None, command: str, code: int, summary: str) -> dict:
    """The run's status, sticky across commands: the highest exit code seen and its reason, plus every
    command in order. `collect` starts a new run."""
    reason = next((line for line in summary.splitlines() if line.strip()), "")
    entry = {"command": command, "exit": code, "reason": reason}
    previous = previous if previous and command != "collect" else {"exit": 0, "commands": []}
    worst = entry if code >= previous["exit"] else {"exit": previous["exit"], "reason": previous["reason"]}
    return {"exit": worst["exit"], "reason": worst["reason"], "commands": previous["commands"] + [entry]}


def _record(ctx: Context, code: int, summary: str) -> None:
    if not ctx.state.path.is_dir():
        return
    path = ctx.state.path / "status.json"
    previous = json.loads(path.read_text()) if path.exists() else None
    ctx.state.save("status.json", run_status(previous, ctx.args.command, code, summary))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ctx = Context(args)
    except BadInput as exc:
        print(f"test-prune: {exc}", file=sys.stderr)
        return 2
    code, summary = _run(ctx)
    _record(ctx, code, summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
