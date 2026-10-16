"""What the ticket threads and the end of run share: the Run, one agent with its one rerun, the gate, the ledger.

A step that cannot go on raises Blocked. The message says why, and `during` adds the step it happened in.
A CLI that fails with no report raises Halted: that is the setup of the run, not the ticket.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, TypeVar

import driver_agents
import driver_decisions
import driver_gate
import driver_merge
import driver_report
import driver_review
import driver_slots
import driver_state
from driver_slots import git, names

OUT = Path(".agent/implement")
FINAL = "final"  # the end of run has its reports and gate records under this name, as a ticket has under its number
_LEDGER = threading.Lock()
T = TypeVar("T")


class Blocked(Exception):
    """A ticket, or the end of run, cannot go on. The message says why."""


class Halted(Exception):
    """The CLI of an agent failed with no report, two times: a model the account cannot use, a CLI that is not
    signed in. The run starts no other agent and ends with exit 4; the ticket keeps its phase for the next run."""


@contextmanager
def during(step: str) -> Iterator[None]:
    """Put the step in front of a Blocked that leaves the block: `<step>: <why>`."""
    try:
        yield
    except Blocked as blocked:
        raise Blocked(f"{step}: {blocked}") from None


def ledger(sentence: str) -> int:
    """Append one sentence to ledger.md and return its line number."""
    with _LEDGER:
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "ledger.md").open("a+") as file:
            file.write(" ".join(sentence.split()) + "\n")
            file.seek(0)
            return sum(1 for _ in file)


def _owner(number: str) -> str:
    return "End of run" if number == FINAL else f"Ticket {number}"


def _gate_record(state: dict, number: str) -> dict:
    """The record of state.json that holds the green gates of `number`."""
    return state["run"]["final"] if number == FINAL else state["tickets"][number]


class Ask(NamedTuple):
    """The questions a ticket thread wants the operator to answer when its ticket blocks."""
    kind: str
    subjects: list[str]  # one question per subject
    report: str  # the evidence
    fields: dict  # what the ticket record gets with the stop


@dataclass
class Run:
    args: argparse.Namespace
    models: dict[str, list[str]]
    state: driver_state.State
    pr: str  # the PR branch
    kept: dict[str, int]  # ticket number -> the slot that holds its uncommitted work from an earlier run
    merging: threading.Lock = field(default_factory=threading.Lock)  # held for one landing: merges are serial
    asks: dict[str, Ask] = field(default_factory=dict)  # ticket number -> its pending stop
    halted: list[str] = field(default_factory=list)  # why the run starts no other agent; [] while it goes on

    def tip(self) -> str:
        return git(".", "rev-parse", f"refs/heads/{self.pr}").stdout.strip()

    def ask(self, number: str, kind: str, subjects: list[str], report: str, **fields) -> None:
        self.asks[number] = Ask(kind, subjects, report, fields)

    def agent(self, role: str, number: str, slot: str, values: dict[str, str], **options) -> dict:
        """Run one role and return its accepted report. `options` are those of `_attempts`."""
        if role == "test-writer":
            # Its report may change a locked file of a merged ticket only when it lists the file.
            merged = driver_merge.protected(self.state.read()["tickets"], number)
            options["check"] = {**(options.get("check") or {}), "merged": merged, "pr": self.tip()}
        report = _attempts(self, role, number, slot, values, **options)
        if role == "test-writer":
            report["locked"] = driver_report.locked_files(report["locked"])  # as validate read it
        if subject := driver_decisions.contradicted(role, report):
            # The implementer's commits stay: after the ruling the ticket goes on from them.
            evidence = OUT / number / f"{role}-{options.get('fix_round', 0)}.json"
            self.ask(number, "locked-test", [subject], str(evidence), tip=driver_slots.head(slot))
        if report.get("status") in ("BLOCKED", "NEEDS_CONTEXT"):
            concerns = "; ".join(report["concerns"]) or "no concern given"
            raise Blocked(f"the {role} reported {report['status']}: {concerns}")
        return report

    def read_only(self, number: str, slot: str, branch: str, tip: str, review: Callable[[], T]) -> T:
        """Run `review` (read-only agents) and return its result. A review that changes the slot runs once more."""
        for last in (False, True):
            error = ""
            try:
                result = review()
            except Blocked as blocked:
                error = str(blocked)
            except Halted:
                _restore_if_changed(number, slot, branch, tip, runs_again=False)
                raise
            left = _restore_if_changed(number, slot, branch, tip, runs_again=not (error or last))
            if error or not left:
                break
        if error or left:
            raise Blocked(error or left)
        return result

    def gate(self, number: str, slot: str, commit: str) -> None:
        """Gate `commit` in the slot, unless a green record names it already."""
        if record := self.red_gate(number, slot, commit):
            raise Blocked(f"the gate is red on {commit[:7]}, record {record}")

    def red_gate(self, number: str, slot: str, commit: str) -> str:
        """`gate`, for a caller that acts on a red gate: returns its record, or "" when the gate is green."""
        if commit in _gate_record(self.state.read(), number)["green_gates"]:
            return ""
        at = driver_slots.head(slot)
        if at != commit:
            raise Blocked(f"the slot is at {at[:7]}, not at {commit[:7]}: the gate did not run")
        green, record = driver_gate.run(self.args.gate, slot, OUT / number, self.pr)

        def count(state: dict) -> None:
            state["run"]["gates"] += 1
            if green:
                _gate_record(state, number)["green_gates"].append(commit)

        self.state.update(count)
        if not green:
            return str(record)
        ledger(f"{_owner(number)}: the gate is green on {commit[:7]}, record {record}.")
        return ""

    def restore_locked_files(self, number: str, slot: str) -> list[str]:
        """Before a test-writer correction: put each locked file the implementer changed back to its locked
        version in one commit of the driver, so the correction starts from the locked versions on a clean index and
        its commit carries no change of the implementer. Other work in the tree stays. Returns the paths."""
        tickets = self.state.read()["tickets"]
        paths = (driver_review.restore_locked(slot, tickets[number])
                 + driver_merge.restore(slot, self.tip(), driver_merge.protected(tickets, number)))
        # A path whose change was never committed is back at HEAD already, and `git commit -- <path>` refuses it.
        changed = names(git(slot, "diff", "--name-only", "-z", "HEAD", "--", *paths).stdout) if paths else []
        if changed:
            words = "implement-spec: restore the locked files before the correction"
            made = subprocess.run(["git", "-C", slot, "commit", "-q", "--no-verify", "-m", words, "--", *changed],
                                  capture_output=True, text=True, env={**os.environ, **driver_merge.identity("HEAD", slot)})
            if made.returncode:
                raise Blocked(f"the driver cannot commit the restored locked files: {made.stderr.strip()}")
            self.state.ticket(number, tip=driver_slots.head(slot))
        for path in paths:
            ledger(f"Ticket {number}: {path} was not the locked version at the correction: the driver restored it in "
                   "a commit, so the correction starts from the locked version.")
        return paths


def _restore_if_changed(number: str, slot: str, branch: str, tip: str, runs_again: bool) -> str:
    """After read-only agents: "" when the slot is as it was; else restore it and return what they left."""
    left = driver_review.disturbed(slot, branch, tip)
    if left:
        driver_review.restore(slot, branch, tip)
        where, what = ("slot 1", "the final review") if number == FINAL else ("the slot", "the review")
        again = f" and runs {what} again" if runs_again else ""
        ledger(f"{_owner(number)}: a read-only role broke its rule: {left}; the driver restored {where} to "
               f"{tip[:7]}{again}.")
    return left


def _reset_if_moved(slot: str, branch: str, tip: str) -> str:
    """After an agent: "" when the slot is on `branch` and still holds `tip`; else reset it and say what was wrong."""
    head = git(slot, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    if git(slot, "rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode:
        problem = f"branch {branch} is missing"
    elif head != branch:
        problem = f"HEAD is on {head or 'a detached commit'} instead of {branch}"
    elif git(slot, "merge-base", "--is-ancestor", tip, "HEAD").returncode:
        problem = f"{branch} no longer holds {tip[:7]}"
    else:
        return ""
    git(slot, "checkout", "-q", "-f", "-B", branch, tip)
    return f"{problem}; the driver reset the slot to {tip[:7]}"


def _timeout_lines(role: str, minutes: float, context: dict, alone: Callable[[], bool]) -> str:
    """The lines the rerun prompt gets after a timeout. The tree stays, but a read-only role has no work to keep:
    its slot is restored, unless another agent still works there."""
    values = {"minutes": f"{minutes:g}", "tip": context["start"]}
    if role not in driver_agents.READ_ONLY:
        return driver_agents.render("timeout", values)
    if alone():
        driver_review.restore(context["repo"], context["branch"], context["start"])
        return driver_agents.render("timeout-restored", values)
    return driver_agents.render("timeout-shared", values)


def _rejection(role: str, code: int | None, minutes: float, report: Path, context: dict, alone: Callable[[], bool],
               detached: bool) -> tuple[str, str]:
    """(why the attempt is rejected, the lines the rerun prompt gets); ("", ...) when the report is accepted."""
    branch, tip = context["branch"], context["start"]
    # Read-only agents share a slot, and a rebase is on a detached HEAD: there the driver has no branch to reset.
    owns_branch = role not in driver_agents.READ_ONLY and not detached
    if owns_branch and (moved := _reset_if_moved(context["repo"], branch, tip)):
        return moved, driver_agents.render("moved", {"branch": branch, "tip": tip})
    if code is None:
        return f"timed out after {minutes:g} minutes", _timeout_lines(role, minutes, context, alone)
    error = "; ".join(driver_report.validate(context["template"], str(report), context))
    return error, driver_agents.render("rerun", {"error": error})


def _first_prompt(template: str, values: dict[str, str], notes: tuple | list, report: str) -> str:
    """The template of the role, then the lines of each `notes` template and the ending of the prompt again."""
    text = driver_agents.render(template, {**values, "report": report})
    if notes:
        text += "".join(driver_agents.render(note, values) for note in notes) + driver_agents.tail(text, report)
    return text


def _record_attempt(run: Run, role: str, number: str, entry: str, seconds: float, log: Path, error: str) -> None:
    cost = driver_agents.cost(log)
    used = {"ticket": number, "role": role, "entry": entry, "seconds": round(seconds, 1), "cost": float(cost or 0)}
    run.state.update(lambda state: state["run"]["agents"].append(used))
    spent = f", cost {cost} USD" if cost else ""
    ledger(f"{_owner(number)}: {role} ran on {entry} for {seconds:.1f}s{spent}: {error or 'report accepted'}.")


def _command(run: Run, role: str, entry: str, prompt: str, report: str) -> list[str]:
    if run.args.runner:  # `--runner` is for tests
        return [run.args.runner, role, prompt, report]
    return driver_agents.command(role, entry, report)


def _attempts(run: Run, role: str, number: str, slot: str, values: dict[str, str], notes: tuple | list = (),
              entry: str = "", fix_round: int = 0, check: dict | None = None, template: str = "",
              alone: Callable[[], bool] = lambda: True, detached: bool = False) -> dict:
    """Run one role in its slot, with at most one rerun, and return the accepted report.

    notes      templates whose lines the first prompt gets after the role's own
    entry      the model entry, when it is not the first of the role's list
    fix_round  the K of the files `<role>-<K>.json`, `.log` and `.prompt.md`
    check      more context for the report validation (driver_report.validate)
    template   the prompt and schema, when it is not the one named after the role
    alone      whether no other agent works in the slot now
    detached   the agent works on a detached HEAD
    """
    folder, entry = OUT / number, entry or run.models[role][0]
    folder.mkdir(parents=True, exist_ok=True)
    report, log, prompt = (folder / f"{role}-{fix_round}{suffix}" for suffix in (".json", ".log", ".prompt.md"))
    report_in_slot = os.path.relpath(report, slot)  # the agent works in its slot
    context = {"repo": slot, "branch": values["branch"], "start": values["start_sha"], "log": str(log), "round": 0,
               "template": template or role, **(check or {})}
    argv = _command(run, role, entry, os.path.relpath(prompt, slot), report_in_slot)
    text = _first_prompt(context["template"], values, notes, report_in_slot)
    minutes, error, failed = run.args.timeout[role], "", ""
    for _ in range(2):
        prompt.write_text(text)
        report.unlink(missing_ok=True)
        start = time.monotonic()
        code = driver_agents.launch(argv, prompt, log, minutes * 60, slot)
        seconds = time.monotonic() - start
        error, rerun_lines = _rejection(role, code, minutes, report, context, alone, detached)
        _record_attempt(run, role, number, entry, seconds, log, error)
        if not error:
            return json.loads(report.read_text())
        # A CLI that exits with an error and no report did not do the role: the run setup is wrong, not the ticket.
        failed = "" if code in (None, 0) or report.exists() else (
            f"the {role} on {entry} exited {code} with no report: {driver_agents.last_words(log)}")
        # The rerun prompt is the first prompt, the rejection, then the same ending again.
        text += rerun_lines + driver_agents.tail(text, report_in_slot)
        if code is None:
            minutes *= 2
    if failed:
        raise Halted(failed)
    raise Blocked(error)
