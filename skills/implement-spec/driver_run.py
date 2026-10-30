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
from typing import Callable, Iterator, NamedTuple, NoReturn, TypeVar

import driver_agents
import driver_decisions
import driver_gate
import driver_merge
import driver_report
import driver_review
import driver_slots
import driver_state
from driver_names import Names
from driver_slots import git, names

OUT = Path(".agent/implement")
FINAL = "final"  # the end of run has its reports and gate records under this name, as a ticket has under its number
_LEDGER = threading.Lock()
STOPS = ("BLOCKED", "NEEDS_CONTEXT")
_COMMITS = ("test-writer", "implementer", "fixer")  # the ticket agents whose commits are the ticket's work
# The phase that runs a ticket agent again: the fixer's round runs again after a review of the tip it left.
_RETRY = {"test-writer": "test-writer", "implementer": "implement", "reviewer": "review", "fixer": "review",
          "conflict": "merge"}
T = TypeVar("T")


class Blocked(Exception):
    """A ticket, or the end of run, cannot go on. The message says why."""


class Stopped(Blocked):
    """An agent's accepted report says BLOCKED or NEEDS_CONTEXT. `report` is that report."""

    def __init__(self, why: str, report: dict):
        super().__init__(why)
        self.report = report


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
    resume: str  # the phase a `retry` takes the ticket back to
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

    @property
    def names(self) -> Names:
        return Names(self.pr)

    def tip(self) -> str:
        return git(".", "rev-parse", f"refs/heads/{self.pr}").stdout.strip()

    def ask(self, number: str, kind: str, subjects: list[str], report: str, resume: str = "", **fields) -> None:
        self.asks[number] = Ask(kind, subjects, report, resume, fields)

    def agent(self, role: str, number: str, slot: str, values: dict[str, str], **options) -> dict:
        """Run one role and return its accepted report. `options` are those of `_attempts`."""
        if role == "test-writer":
            # Its report may change a locked file of a merged ticket only when it lists the file.
            merged = driver_merge.protected(self.state.read()["tickets"], number)
            options["check"] = {**(options.get("check") or {}), "merged": merged, "pr": self.tip()}
            values = {**values, "protected": " ".join(merged) or "none"}
            if options["check"].get("correction"):
                options["notes"] = [*options.get("notes", ()), "corrected"]
        report = _attempts(self, role, number, slot, values, **options)
        if role == "test-writer":
            report["locked"] = driver_report.locked_files(report["locked"])  # as validate read it
        if report.get("status") in STOPS:
            concerns = "; ".join(report["concerns"]) or "no concern given"
            why = f"the {role} reported {report['status']}: {concerns}"
            if number != FINAL:
                self._ask_stop(role, number, slot, report, options.get("fix_round", 0))
            raise Stopped(why, report)
        return report

    def _ask_stop(self, role: str, number: str, slot: str, report: dict, fix_round: int) -> None:
        """The `locked-test` question of an implementer or a fixer that says a locked test contradicts the spec: the
        one stop of a ticket agent that `_attempts` returns. Its commits stay: after the ruling the ticket goes on
        from them."""
        if subject := driver_decisions.contradicted(role, report):
            self.ask(number, "locked-test", [subject], str(OUT / number / f"{role}-{fix_round}.json"),
                     tip=driver_slots.head(slot))

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

    def red_gate(self, number: str, slot: str, commit: str, tier: str, restored: list[str] | tuple = ()) -> str:
        """`gate`, for a caller that acts on a red gate: returns its record, or "" when the gate is green. The fast gate
        checks the ticket's locked files and the `restored` ones, as the state holds them now."""
        state = self.state.read()
        if commit in _gate_record(state, number)["green_gates"][tier]:
            return ""
        at = driver_slots.head(slot)
        if at != commit:
            raise Blocked(f"the slot is at {at[:7]}, not at {commit[:7]}: the {tier} gate did not run")
        command = self.args.fast_gate if tier == driver_gate.FAST else self.args.gate
        locked = list(dict.fromkeys([*state["tickets"][number]["locked"], *restored])) if tier == driver_gate.FAST else []
        green, record, killed = driver_gate.run(command, tier, slot, OUT / number, self.pr, locked)
        for sentence in killed:
            ledger(f"{_owner(number)}: {sentence}.")

        def count(state: dict) -> None:
            state["run"]["gates"][tier] += 1
            if green:
                _gate_record(state, number)["green_gates"][tier].append(commit)

        self.state.update(count)
        if not green:
            return str(record)
        ledger(f"{_owner(number)}: the {tier} gate is green on {commit[:7]}, record {record}.")
        return ""

    def restore_locked_files(self, number: str, slot: str) -> list[str]:
        """Before a test-writer correction: put each locked file the implementer changed back to its locked
        version in one commit of the driver, so the correction starts from the locked versions on a clean index and
        its commit carries no change of the implementer. Other work in the tree stays. Returns the paths."""
        tickets = self.state.read()["tickets"]
        paths = (driver_review.restore_locked(slot, tickets[number])
                 + driver_merge.restore(slot, self.tip(), driver_merge.protected(tickets, number)))
        self._commit_restored(number, slot, paths, "implement-spec: restore the locked files before the correction")
        for path in paths:
            ledger(f"Ticket {number}: {path} was not the locked version at the correction: the driver restored it in "
                   "a commit, so the correction starts from the locked version.")
        return paths

    def restore_merged_files(self, number: str, slot: str, paths: list[str]) -> list[str]:
        """Before a landing, by a `restore` ruling: put each of `paths` that is a locked file of a merged ticket and
        that the ticket changed back to the version of the PR branch, in one commit of the driver. Returns those."""
        protected = driver_merge.protected(self.state.read()["tickets"], number)
        restored = driver_merge.restore(slot, self.tip(), [path for path in paths if path in protected])
        self._commit_restored(number, slot, restored,
                              "implement-spec: restore the locked files of merged tickets before the landing")
        for path in restored:
            ledger(f"Ticket {number}: by the ruling (restore), the driver put back {path}, a locked file of a merged "
                   "ticket, at the version of the PR branch in a commit before the landing.")
        return restored

    def _commit_restored(self, number: str, slot: str, paths: list[str], words: str) -> None:
        """Commit the restored `paths` in the slot as a commit of the driver, and make it the ticket tip."""
        # A path whose change was never committed is back at HEAD already, and `git commit -- <path>` refuses it.
        changed = names(git(slot, "diff", "--name-only", "-z", "HEAD", "--", *paths).stdout) if paths else []
        if not changed:
            return
        made = subprocess.run(["git", "-C", slot, "commit", "-q", "--no-verify", "-m", words, "--", *changed],
                              capture_output=True, text=True, env={**os.environ, **driver_merge.identity("HEAD", slot)})
        if made.returncode:
            raise Blocked(f"the driver cannot commit the restored locked files: {made.stderr.strip()}")
        self.state.ticket(number, tip=driver_slots.head(slot))


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


def _stop(role: str, number: str, report: Path) -> str:
    """Why the valid report of a ticket agent is a stop: BLOCKED or NEEDS_CONTEXT and no dispute; else ""."""
    accepted = json.loads(report.read_text())
    if number == FINAL or accepted["status"] not in STOPS or driver_decisions.contradicted(role, accepted):
        return ""
    return f"it reported {accepted['status']}: {'; '.join(accepted['concerns']) or 'no concern given'}"


def _rejection(role: str, number: str, code: int | None, minutes: float, report: Path, context: dict,
               alone: Callable[[], bool], detached: bool) -> tuple[str, str, bool]:
    """(why the attempt is rejected, the lines the rerun prompt gets, whether the rerun stays on the same entry);
    ("", ...) when the report is accepted. A stop of a ticket agent is rejected too."""
    branch, tip = context["branch"], context["start"]
    # Read-only agents share a slot, and a rebase is on a detached HEAD: there the driver has no branch to reset.
    owns_branch = role not in driver_agents.READ_ONLY and not detached
    if owns_branch and (moved := _reset_if_moved(context["repo"], branch, tip)):
        return moved, driver_agents.render("moved", {"branch": branch, "tip": tip}), True
    if code is None:
        return f"timed out after {minutes:g} minutes", _timeout_lines(role, minutes, context, alone), True
    error = "; ".join(driver_report.validate(context["template"], str(report), context)) or _stop(role, number, report)
    return error, driver_agents.render("rerun", {"error": error}), False


def _rerun_entry(run: Run, role: str, number: str, entry: str, entries: list[str] | None) -> str:
    """The entry of the rerun after a stop or a rejection: the next one of the list, wrapping to the first. The end of
    run reruns on the same entry."""
    if number == FINAL:
        return entry
    entries = entries or run.models[role]
    return entries[(entries.index(entry) + 1) % len(entries)]


def _cli_failure(role: str, entry: str, code: int | None, report: Path, log: Path) -> str:
    """Why the run setup is wrong, when the CLI exited with an error and no report: it did not do the role; else ""."""
    if code in (None, 0) or report.exists():
        return ""
    return f"the {role} on {entry} exited {code} with no report: {driver_agents.last_words(log)}"


def _ask_blocked(run: Run, role: str, number: str, slot: str, report: Path, error: str, fix_round: int) -> None:
    """The `ticket-blocked` question of a ticket agent whose rerun stopped or was rejected too. `retry` takes the
    ticket back to the phase that runs the role again, from the tip it left; the round of a fixer does not count."""
    fields = {"tip": driver_slots.head(slot)} if role in _COMMITS else {}
    if role == "fixer":
        fields["round"] = fix_round - 1
    run.ask(number, "ticket-blocked", [f"the {role}: {error}"], str(report), _RETRY[role], **fields)


def _give_up(run: Run, role: str, number: str, slot: str, report: Path, error: str, failed: str, same: bool,
             fix_round: int) -> NoReturn:
    """After the rerun failed too: Halted for a CLI that wrote no report, else Blocked, with a `ticket-blocked`
    question when a ticket agent stopped or was rejected."""
    if failed:
        raise Halted(failed)
    if not (same or number == FINAL):
        _ask_blocked(run, role, number, slot, report, error, fix_round)
    raise Blocked(error)


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
              alone: Callable[[], bool] = lambda: True, detached: bool = False, entries: list[str] | None = None) -> dict:
    """Run one role in its slot, with at most one rerun, and return the accepted report. A ticket agent that stops or
    is rejected reruns on the next entry of its list; a timeout or a moved branch reruns on the same entry. A ticket
    agent whose rerun stops or is rejected too asks `ticket-blocked`.

    notes      templates whose lines the first prompt gets after the role's own
    entry      the model entry, when it is not the first of the role's list
    entries    the list `entry` is from, when it is not the role's
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
    context = {"repo": slot, "branch": values["branch"], "start": values["start_sha"], "log": str(log),
               "template": template or role, **(check or {})}
    text = _first_prompt(context["template"], values, notes, report_in_slot)
    rerun_on = _rerun_entry(run, role, number, entry, entries)
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
        error, rerun_lines, same = _rejection(role, number, code, minutes, report, context, alone, detached)
        _record_attempt(run, role, number, entry, seconds, log, error)
        if not error:
            return json.loads(report.read_text())
        failed = _cli_failure(role, entry, code, report, log)
        # The rerun prompt is the first prompt, the rejection, then the same ending again.
        text += rerun_lines + driver_agents.tail(text, report_in_slot)
        if code is None:
            minutes *= 2
        entry = entry if same else rerun_on
    _give_up(run, role, number, slot, report, error, failed, same, fix_round)
