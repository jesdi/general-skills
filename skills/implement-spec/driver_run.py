"""What the ticket threads and the end of run share: the Run, one agent with its one rerun, the gate, the ledger.

A step that cannot go on raises Blocked. The message says why, and `during` adds the step it happened in.
A CLI that fails with no report raises Halted: that is the setup of the run, not the ticket.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, NoReturn, TypeVar

import driver_agents
import driver_decisions
import driver_gate
import driver_locked
import driver_report
import driver_review
import driver_slots
import driver_state
from driver_decisions import FINAL, Resume
from driver_names import Names
from driver_slots import git

OUT = Path(".agent/implement")
FINAL_RESUME = Resume("pending")  # a `retry` of the end of run: it goes on from where its state is
_LEDGER = threading.Lock()
_ASKS = threading.Lock()  # the lenses of the final review run at the same time
STOPS = ("BLOCKED", "NEEDS_CONTEXT")
_COMMITS = ("test-writer", "implementer", "fixer")  # the ticket agents whose commits are the ticket's work
# The phase that runs a ticket agent again: the fixer's round runs again after a review of the tip it left.
_RETRY = {"test-writer": "test-writer", "implementer": "implement", "reviewer": "review", "fixer": "review",
          "conflict": "merge"}
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


_owner = driver_decisions.owner


def record_of(state: dict, number: str) -> dict:
    """The record of `number` in state.json: its ticket record, or `run.final` for the end of run. Each has its
    `phase`, which an answer sets, and its `green_gates`."""
    return state["run"]["final"] if number == FINAL else state["tickets"][number]


@dataclass(frozen=True)
class Call:
    """One agent of a ticket or of the end of run, as `Run.agent` runs it with its one rerun."""
    role: str
    number: str  # the ticket, or FINAL
    slot: str
    values: dict[str, str]  # the pointers of its prompt
    notes: tuple | list = ()  # templates whose lines the first prompt gets after the role's own
    entry: str = ""  # the model entry, when it is not the first of the role's list
    entries: list[str] | None = None  # the list `entry` is from, when it is not the role's
    fix_round: int = 0  # the K of the files `<role>-<K>.json`, `.log` and `.prompt.md`
    check: dict = field(default_factory=dict)  # more context for the report validation (driver_report.validate)
    template: str = ""  # the prompt and schema, when it is not the one named after the role
    alone: Callable[[], bool] = lambda: True  # whether no other agent works in the slot now
    detached: bool = False  # the agent works on a detached HEAD
    name: str = ""  # the name of its files, when it is not `<role>-<fix_round>`
    # The role that a `retry` of its `ticket-blocked` question runs again from the start commit, when the agent runs
    # for that role (an arbitration, a correction): its own work is not kept.
    retry: str = ""
    # A BLOCKED report that disputes a locked test is accepted, not a stop: `driver_dispute.agent` settles it.
    may_dispute: bool = False
    # Reads the locked-file check of its report when the agent ends; by default that of a ticket agent.
    locked: Callable[[], driver_locked.Locked | None] | None = None


class Ask(NamedTuple):
    """The questions a ticket thread, or the end of run, wants the operator to answer when it blocks."""
    kind: str
    subjects: list[str]  # one question per subject
    report: str  # the evidence
    resume: Resume  # the record gets its tip and round with the stop, and all of it with a `retry` or a `keep-test`


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

    @property
    def names(self) -> Names:
        return Names(self.pr)

    def tip(self) -> str:
        return git(".", "rev-parse", f"refs/heads/{self.pr}").stdout.strip()

    def ask(self, number: str, kind: str, subjects: list[str], report: str, resume: Resume) -> None:
        """The questions of `number`'s stop. The end of run adds those of each lens that stopped to one stop."""
        with _ASKS:
            if earlier := self.asks.get(number):
                subjects = earlier.subjects + subjects
            self.asks[number] = Ask(kind, subjects, report, resume)

    def block(self, number: str, line: int) -> None:
        """Make the record of `number` BLOCKED and add the questions it asked for, in one write of state.json. The
        ledger line `line` says why."""
        ask = self.asks.pop(number, None)

        def change(state: dict) -> None:
            held = record_of(state, number)
            if ask:
                held.update(ask.resume.fields())
                evidence = [ask.report, f"{OUT / 'ledger.md'}:{line}"]
                driver_decisions.stop(state["run"], ask.kind, number, ask.subjects, evidence, ask.resume)
            held["phase"] = "blocked"

        self.state.update(change)
        if ask and ask.subjects:
            ledger(f"{_owner(number)}: {driver_decisions.decides(ask.kind, len(ask.subjects))} in "
                   f"{OUT / 'decisions.json'}.")

    def agent(self, call: Call) -> dict:
        """Run one agent and return its accepted report."""
        if call.role == "test-writer":
            others = driver_locked.others(self.state.read()["tickets"], call.number)
            call = replace(call, values={**call.values, "protected": " ".join(others) or "none"})
        report = _attempts(self, call)
        if call.role == "test-writer":
            report["locked"] = driver_report.locked_files(report["locked"])  # as validate read it
        return report

    def locked(self, call: Call) -> driver_locked.Locked | None:
        """The locked-file check of the report of `call`, read when the agent ends."""
        if call.locked:
            return call.locked()
        return driver_locked.for_ticket(self.state.read()["tickets"], call.role, call.number, f"refs/heads/{self.pr}")

    def read_only(self, number: str, slot: str, branch: str, tip: str, review: Callable[[], T]) -> T:
        """Run `review` (read-only agents) and return its result. A review that changes the slot runs once more."""
        for last in (False, True):
            error, config = "", driver_review.shared_config(slot)
            try:
                result = review()
            except Blocked as blocked:
                error = str(blocked)
            except Halted:
                _restore_if_changed(number, slot, branch, tip, config, runs_again=False)
                raise
            left = _restore_if_changed(number, slot, branch, tip, config, runs_again=not (error or last))
            if error or not left:
                break
        if error or left:
            raise Blocked(error or left)
        return result

    def gate(self, number: str, slot: str, commit: str, tier: str) -> None:
        """Gate `commit` in the slot on `tier`, unless a green record of that tier names it already."""
        if record := self.red_gate(number, slot, commit, tier):
            raise Blocked(f"the {tier} gate is red on {commit[:7]}, record {record}")

    def red_gate(self, number: str, slot: str, commit: str, tier: str) -> str:
        """`gate`, for a caller that acts on a red gate: returns its record, or "" when the gate is green. The fast gate
        checks the ticket's locked files, as the state holds them now."""
        state = self.state.read()
        if commit in record_of(state, number)["green_gates"][tier]:
            return ""
        at = driver_slots.head(slot)
        if at != commit:
            raise Blocked(f"the slot is at {at[:7]}, not at {commit[:7]}: the {tier} gate did not run")
        command = self.args.fast_gate if tier == driver_gate.FAST else self.args.gate
        locked = state["tickets"][number]["locked"] if tier == driver_gate.FAST else []
        green, record, killed = driver_gate.run(command, tier, slot, OUT / number, self.pr, locked)
        for sentence in killed:
            ledger(f"{_owner(number)}: {sentence}.")

        def count(state: dict) -> None:
            state["run"]["gates"][tier] += 1
            if green:
                record_of(state, number)["green_gates"][tier].append(commit)

        self.state.update(count)
        if not green:
            return str(record)
        ledger(f"{_owner(number)}: the {tier} gate is green on {commit[:7]}, record {record}.")
        return ""


def _restore_if_changed(number: str, slot: str, branch: str, tip: str, config: driver_review.Config,
                        runs_again: bool) -> str:
    """After read-only agents: "" when the slot and the shared git config (`driver_review.shared_config` before
    them) are as they were; else restore both and return what they left."""
    # The config first: a bad core.worktree in it breaks the git commands of the slot check.
    try:
        changed = driver_review.restore_config(config)
    except driver_review.Unrestored as error:
        raise Blocked(str(error)) from error
    moved = driver_review.disturbed(slot, branch, tip)
    if moved:
        driver_review.restore(slot, branch, tip)
    left = "; ".join(filter(None, (changed, moved)))
    if left:
        where = ("slot 1" if number == FINAL else "the slot") + f" to {tip[:7]}"
        restored = " and ".join(filter(None, ("the shared git config" if changed else "", where if moved else "")))
        again = f" and runs {'the final review' if number == FINAL else 'the review'} again" if runs_again else ""
        ledger(f"{_owner(number)}: a read-only role broke its rule: {left}; the driver restored {restored}{again}.")
    return left


def _reset_if_moved(slot: str, branch: str, tip: str) -> str:
    """After an agent: "" when the slot is on `branch` and still holds `tip`; else reset it and say what was wrong."""
    head = driver_slots.on_branch(slot)
    if not driver_slots.branch_exists(branch):
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


def _stop(call: Call, report: Path) -> str:
    """Why the valid report of an agent is a stop: BLOCKED or NEEDS_CONTEXT, and no dispute of an agent that may
    dispute; else ""."""
    accepted = json.loads(report.read_text())
    if accepted["status"] not in STOPS or (call.may_dispute and driver_report.disputes(call.role, accepted, [])):
        return ""
    return f"it reported {accepted['status']}: {'; '.join(accepted['concerns']) or 'no concern given'}"


def _rejection(call: Call, code: int | None, minutes: float, report: Path, context: dict) -> tuple[str, str, bool]:
    """(why the attempt is rejected, the lines the rerun prompt gets, whether the rerun stays on the same entry);
    ("", ...) when the report is accepted. A stop is rejected too."""
    role, branch, tip = call.role, context["branch"], context["start"]
    # Read-only agents share a slot, and a rebase is on a detached HEAD: there the driver has no branch to reset.
    owns_branch = role not in driver_agents.READ_ONLY and not call.detached
    if owns_branch and (moved := _reset_if_moved(context["repo"], branch, tip)):
        return moved, driver_agents.render("moved", {"branch": branch, "tip": tip}), True
    if code is None:
        return f"timed out after {minutes:g} minutes", _timeout_lines(role, minutes, context, call.alone), True
    error = "; ".join(driver_report.validate(context["template"], str(report), context)) or _stop(call, report)
    return error, driver_agents.render("rerun", {"error": error}), False


def _rerun_entry(run: Run, role: str, entry: str, entries: list[str] | None) -> str:
    """The entry of the rerun after a stop or a rejection: the next one of the list, wrapping to the first."""
    entries = entries or run.models[role]
    return entries[(entries.index(entry) + 1) % len(entries)]


def _cli_failure(role: str, entry: str, code: int | None, report: Path, log: Path) -> str:
    """Why the run setup is wrong, when the CLI exited with an error and no report: it did not do the role; else ""."""
    if code in (None, 0) or report.exists():
        return ""
    return f"the {role} on {entry} exited {code} with no report: {driver_agents.last_words(log)}"


def retry_point(role: str, tip: str, fix_round: int) -> Resume:
    """Where a `retry` takes a ticket to run `role` again: a role that commits goes on from `tip`, and the round of a
    fixer does not count."""
    return Resume(_RETRY[role], tip if role in _COMMITS else None, fix_round - 1 if role == "fixer" else None)


def _ask_blocked(run: Run, call: Call, report: Path, error: str) -> None:
    """The `ticket-blocked` question of an agent whose rerun stopped or was rejected too. Its `retry` takes the ticket
    back to the phase that runs the role again, from the tip the agent left; an agent that ran for another role
    (`call.retry`) leaves no work, so that role runs again from the agent's start. The end of run goes on from where
    its state is."""
    tip = call.values["start_sha"] if call.retry else driver_slots.head(call.slot)
    resume = FINAL_RESUME if call.number == FINAL else retry_point(call.retry or call.role, tip, call.fix_round)
    run.ask(call.number, "ticket-blocked", [f"the {call.role}: {error}"], str(report), resume)


def _give_up(run: Run, call: Call, report: Path, error: str, failed: str, same: bool) -> NoReturn:
    """After the rerun failed too: Halted for a CLI that wrote no report, else Blocked, with a `ticket-blocked`
    question when the agent stopped or was rejected."""
    if failed:
        raise Halted(failed)
    if not same:
        _ask_blocked(run, call, report, error)
    raise Blocked(error)


def _files(folder: Path, role: str, fix_round: int, name: str) -> tuple[Path, Path, Path]:
    """The report, log and prompt of an agent: `<name>.*`, by default `<role>-<fix_round>.*`."""
    name = name or f"{role}-{fix_round}"
    return folder / f"{name}.json", folder / f"{name}.log", folder / f"{name}.prompt.md"


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


def _attempts(run: Run, call: Call) -> dict:
    """Run one agent in its slot, with at most one rerun, and return the accepted report. An agent that stops or is
    rejected reruns on the next entry of its list; a timeout or a moved branch reruns on the same entry. An agent whose
    rerun stops or is rejected too asks `ticket-blocked`."""
    role, number, slot, values = call.role, call.number, call.slot, call.values
    folder, entry = OUT / number, call.entry or run.models[role][0]
    folder.mkdir(parents=True, exist_ok=True)
    report, log, prompt = _files(folder, role, call.fix_round, call.name)
    report_in_slot = os.path.relpath(report, slot)  # the agent works in its slot
    context = {"repo": slot, "branch": values["branch"], "start": values["start_sha"], "log": str(log),
               "template": call.template or role, **call.check}
    text = _first_prompt(context["template"], values, call.notes, report_in_slot)
    rerun_on = _rerun_entry(run, role, entry, call.entries)
    minutes, error, failed, same = run.args.timeout[role], "", "", True
    for _ in range(2):
        argv = _command(run, role, entry, os.path.relpath(prompt, slot), report_in_slot)
        prompt.write_text(text)
        report.unlink(missing_ok=True)
        Path(driver_agents.last_message(report)).unlink(missing_ok=True)
        start = time.monotonic()
        code = driver_agents.launch(argv, prompt, log, minutes * 60, slot)
        seconds = time.monotonic() - start
        driver_agents.settle_report(report)
        context["locked"] = run.locked(call)
        error, rerun_lines, same = _rejection(call, code, minutes, report, context)
        _record_attempt(run, role, number, entry, seconds, log, error)
        if not error:
            return json.loads(report.read_text())
        failed = _cli_failure(role, entry, code, report, log)
        # The rerun prompt is the first prompt, the rejection, then the same ending again.
        text += rerun_lines + driver_agents.tail(text, report_in_slot)
        if code is None:
            minutes *= 2
        entry = entry if same else rerun_on
    _give_up(run, call, report, error, failed, same)
