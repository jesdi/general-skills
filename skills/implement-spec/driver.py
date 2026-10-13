#!/usr/bin/env python3
"""implement-spec driver: validate the ticket set and the environment before any agent.

    sh run.sh --gate COMMAND [--tickets DIR] [--models FILE] [--dry-run]

Run it in the PR worktree. Messages go to stdout.
Exit 0 = valid, 3 = usage or validation error. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import driver_tickets

CLIS = {"anthropic": "claude", "openai": "codex"}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        print(f"implement-spec: {message}")
        raise SystemExit(3)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _worktree_errors() -> list[str]:
    errors = []
    # The trailing slash lets a `.agent/` pattern match before the directory exists.
    if _git("check-ignore", "-q", ".agent/").returncode != 0:
        errors.append(".agent is not gitignored")
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        errors.append(f"git status failed: {status.stderr.strip()}")
    return errors + [f"uncommitted change: {line[3:]}" for line in status.stdout.splitlines()]


def _model_errors(path: str) -> list[str]:
    """The CLI of each role's first model entry must be on PATH."""
    try:
        models = json.loads(Path(path).read_text())
        firsts = {role: entries[0] for role, entries in models.items()}
    except (OSError, ValueError, AttributeError, LookupError, TypeError) as exc:
        return [f"--models {path}: {exc!r}"]
    errors = []
    for role, entry in firsts.items():
        # A bare entry is anthropic. Ticket 02 owns the entry grammar and unknown providers.
        cli = CLIS.get(str(entry).split("/")[0] if "/" in str(entry) else "anthropic")
        # The correctness pass folds into the reviewer when its CLI is absent (requirement 13).
        if cli and role != "correctness" and not shutil.which(cli):
            errors.append(f"{role}: {cli} is not on PATH (first model entry {entry})")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="implement-spec", description="Validate tickets and preconditions.")
    parser.add_argument("--tickets", default=".agent/tickets", help="directory of NN-name.md ticket files")
    parser.add_argument("--gate", help="the repo's check command (required)")
    parser.add_argument("--models", help="JSON file: one ordered list of provider/model@effort per role")
    parser.add_argument("--dry-run", action="store_true", help="validate, print the frontier width, change nothing")
    args = parser.parse_args(argv)

    errors = [] if args.gate else ["--gate is required: the command the driver runs as the gate"]
    errors += _worktree_errors()
    errors += _model_errors(args.models) if args.models else []
    tickets, ticket_errors = driver_tickets.parse(args.tickets)
    errors += ticket_errors
    for error in errors:
        print(error)
    if errors:
        return 3
    print(f"max frontier width: {driver_tickets.max_frontier_width(tickets)}")
    if not args.dry_run:
        print("implement-spec: only --dry-run is implemented")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
