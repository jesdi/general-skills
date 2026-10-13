#!/usr/bin/env python3
"""implement-spec driver: validate the ticket set and the environment, then run the agents of the first ticket.

    sh run.sh --gate COMMAND --spec FILE [--tickets DIR] [--models FILE] [--timeout ROLE=MINUTES] [--dry-run]

Run it in the PR worktree. Messages go to stdout.
Exit 0 = done, 1 = a ticket is BLOCKED, 3 = usage or validation error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import driver_agents
import driver_report
import driver_tickets

OUT = Path(".agent/implement")
# ponytail: `--only` reaches the roles the plain run does not launch yet; ticket 04 owns the review.
ONLY = ("test-writer", "implementer", "reviewer", "correctness")


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


def _timeout(text: str) -> tuple[str, float]:
    role, _, minutes = text.partition("=")
    if role not in driver_agents.TIMEOUTS:
        raise ValueError(role)
    return role, float(minutes)


def _ledger(sentence: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "ledger.md").open("a") as ledger:
        ledger.write(" ".join(sentence.split()) + "\n")


def _hygiene(branch: str, tip: str) -> str:
    """After an agent: the ticket branch exists, is checked out and still holds `tip`. If not, reset the slot."""
    head = _git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    if _git("rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode:
        problem = f"branch {branch} is missing"
    elif head != branch:
        problem = f"HEAD is on {head or 'a detached commit'} instead of {branch}"
    elif _git("merge-base", "--is-ancestor", tip, "HEAD").returncode:
        problem = f"{branch} no longer holds {tip[:7]}"
    else:
        return ""
    _git("checkout", "-q", "-f", "-B", branch, tip)
    return f"{problem}; the driver reset the slot to {tip[:7]}"


def _outcome(role: str, code: int | None, minutes: float, report: Path, context: dict) -> tuple[str, str]:
    """(why this attempt is rejected, the template lines the rerun prompt gets); ("", "") when accepted."""
    branch, tip = context["branch"], context["start"]
    if moved := _hygiene(branch, tip):
        return moved, driver_agents.render("moved", {"branch": branch, "tip": tip})
    if code is None:
        # A timeout is not an agent fault: the tree stays as it is.
        return f"timed out after {minutes:g} minutes", driver_agents.render("timeout", {"minutes": f"{minutes:g}"})
    error = "; ".join(driver_report.validate(role, str(report), context))
    return error, driver_agents.render("rerun", {"error": error})


def _agent(role: str, number: str, entry: str, values: dict[str, str], args) -> tuple[str, dict]:
    """Run one role, with at most one rerun. Returns ("", report) when accepted, else (the last error, {})."""
    folder = OUT / number
    folder.mkdir(parents=True, exist_ok=True)
    report, log, prompt = (folder / f"{role}-0{suffix}" for suffix in (".json", ".log", ".prompt.md"))
    context = {"repo": ".", "branch": values["branch"], "start": values["start_sha"], "log": str(log), "round": 0}
    argv = [args.runner, role, str(prompt), str(report)] if args.runner else driver_agents.command(role, entry, str(report))
    text = driver_agents.render(role, {**values, "report": str(report)})
    minutes, error = args.timeout[role], ""
    for _ in range(2):
        prompt.write_text(text)
        report.unlink(missing_ok=True)
        start = time.monotonic()
        code = driver_agents.launch(argv, prompt, log, minutes * 60)
        seconds = time.monotonic() - start
        error, lines = _outcome(role, code, minutes, report, context)
        cost = driver_agents.cost(log)
        _ledger(f"Ticket {number}: {role} ran on {entry} for {seconds:.1f}s{f', cost {cost} USD' if cost else ''}: "
                f"{error or 'report accepted'}.")
        if not error:
            return "", json.loads(report.read_text())
        # The rerun prompt is the first prompt, the rejection, then the same ending again (requirement 14).
        text += lines + driver_agents.tail(text, str(report))
        minutes *= 2 if code is None else 1
    return error, {}


def _stopped(role: str, report: dict) -> str:
    """A valid report whose agent did not finish blocks the ticket; it is not a completion."""
    if report.get("status") in ("BLOCKED", "NEEDS_CONTEXT"):
        return f"the {role} reported {report['status']}: {'; '.join(report['concerns']) or 'no concern given'}"
    return ""


def _run_ticket(ticket: driver_tickets.Ticket, models: dict[str, list[str]], args) -> int:
    """Ticket 03 owns the graph walk, the gate and the merge: this runs the agents of one ticket in slot 1."""
    branch = f"ticket/{ticket.number}"
    values = {"spec": args.spec, "ticket": str(Path(args.tickets) / ticket.file), "seam": ticket.seam,
              "pr_branch": _git("symbolic-ref", "--short", "HEAD").stdout.strip(), "branch": branch, "locked": ""}
    created = _git("checkout", "-q", "-b", branch)
    if created.returncode:
        print(f"implement-spec: cannot create {branch}: {created.stderr.strip()}")
        return 3
    for role in [args.only] if args.only else ["test-writer", "implementer"][ticket.refactor:]:
        values["start_sha"] = _git("rev-parse", "HEAD").stdout.strip()
        error, report = _agent(role, ticket.number, models[role][0], values, args)
        error = error or _stopped(role, report)
        if error:
            _ledger(f"Ticket {ticket.number}: BLOCKED at the {role}: {error}.")
            print(f"{ticket.file}: BLOCKED at the {role}: {error}")
            return 1
        if role == "test-writer":  # only its schema types `locked`
            values["locked"] = " ".join(report["locked"])
    return 0


def main(argv: list[str] | None = None) -> int:
    defaults = ", ".join(f"{role} {minutes}" for role, minutes in driver_agents.TIMEOUTS.items())
    parser = _Parser(prog="implement-spec", description="Validate tickets and preconditions, then run the agents.")
    parser.add_argument("--tickets", default=".agent/tickets", help="directory of NN-name.md ticket files")
    parser.add_argument("--gate", help="the repo's check command (required)")
    parser.add_argument("--spec", help="the spec file the prompts point at (required without --dry-run)")
    parser.add_argument("--models", help="JSON file: one ordered list of provider/model@effort per role, "
                        "on top of the skill's models.json")
    parser.add_argument("--timeout", action="append", type=_timeout, default=[], metavar="ROLE=MINUTES",
                        help=f"timeout of one role, repeatable; doubled on the one rerun (defaults: {defaults})")
    parser.add_argument("--dry-run", action="store_true", help="validate, print the frontier width, change nothing")
    parser.add_argument("--runner", help="test only: a script called as `<script> <role> <prompt> <report>` "
                        "in the slot, in place of the CLI")
    parser.add_argument("--only", choices=ONLY, help="test only: run this one role on the first ticket")
    args = parser.parse_args(argv)
    args.timeout = {**driver_agents.TIMEOUTS, **dict(args.timeout)}

    errors = [] if args.gate else ["--gate is required: the command the driver runs as the gate"]
    if not (args.spec or args.dry_run):
        errors.append("--spec is required: the spec file the prompts point at")
    errors += _worktree_errors()
    models, model_errors = driver_agents.load_models(args.models)
    tickets, ticket_errors = driver_tickets.parse(args.tickets)
    for error in errors + model_errors + ticket_errors:
        print(error)
    if errors or model_errors or ticket_errors:
        return 3
    print(f"max frontier width: {driver_tickets.max_frontier_width(tickets)}")
    return 0 if args.dry_run else _run_ticket(tickets[0], models, args)


if __name__ == "__main__":
    sys.exit(main())
