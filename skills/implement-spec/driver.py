#!/usr/bin/env python3
"""implement-spec driver: validate the ticket set and the environment, then walk the ticket graph.

    sh run.sh --gate COMMAND --spec FILE [--tickets DIR] [--models FILE] [--parallel N] [--prepare COMMAND]
              [--timeout ROLE=MINUTES] [--answer QUESTION=OPTION] [--unattended [--stage-json FILE]] [--dry-run]

Run it in the PR worktree. Each ready ticket runs in a slot: test-writer, implementer, the gate, the review with
its fix rounds, the merge into the PR branch. `.agent/implement/` gets state.json (the resume contract), ledger.md, the gate records and summary.md.
A choice that only the operator can make becomes a question: `.agent/implement/decisions.json` and the page
`.agent/implement-review.html` hold the open questions, `.agent/implement-answers.json` or `--answer` gives the answers.
Messages go to stdout. Exit 0 = every ticket merged, 1 = a ticket is BLOCKED, 2 = parked on a question,
3 = usage or validation error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

import driver_agents
import driver_answers
import driver_decisions
import driver_fixloop
import driver_gate
import driver_graph
import driver_merge
import driver_report
import driver_review
import driver_review_page
import driver_slots
import driver_state
import driver_summary
import driver_tickets
from driver_slots import git

OUT = Path(".agent/implement")
STATE = OUT / "state.json"
DECISIONS = OUT / "decisions.json"
PAGE = Path(".agent/implement-review.html")
# `--only` is test only: one role on the first ticket, with no gate, review or merge.
ONLY = ("test-writer", "implementer", "reviewer", "correctness")
# The phase a ticket is in while a role runs, and the one it reaches when the role's report is accepted.
DURING = {"test-writer": "test-writer", "implementer": "implement"}
AFTER = {"test-writer": "implement", "implementer": "gate"}
_LEDGER = threading.Lock()


@dataclass
class Run:
    """What every ticket thread shares. `state` and `merging` are the two locks of the run."""
    args: argparse.Namespace
    models: dict[str, list[str]]
    state: driver_state.State
    pr: str  # the PR branch
    kept: dict[str, int]  # ticket number -> the slot that holds its work from an interrupted run
    merging: threading.Lock = field(default_factory=threading.Lock)
    out: Path = OUT
    # ticket number -> (question kind, one subject per question, the report they come from, the fields the ticket
    # record gets): the stop that the ticket's thread asked for, until that thread blocks the ticket.
    asks: dict[str, tuple[str, list[str], str, dict]] = field(default_factory=dict)

    def tip(self) -> str:
        return git(".", "rev-parse", f"refs/heads/{self.pr}").stdout.strip()

    def agent(self, role: str, number: str, slot: str, values: dict[str, str], **how) -> tuple[str, dict]:
        """One role with its one rerun: ("", report) or (why the ticket is blocked, {}). A report that says BLOCKED
        or NEEDS_CONTEXT blocks too, and a contradicted locked test asks the operator (requirement 17)."""
        error, report = _agent(role, number, slot, values, self, **how)
        if subject := driver_decisions.contradicted(role, report):
            # The commits of the implementer stay: after the ruling the ticket goes on from them, not from a reset.
            self.ask(number, "locked-test", [subject], str(OUT / number / f"{role}-{how.get('fix_round', 0)}.json"),
                     tip=driver_slots.head(slot))
        return error or _stopped(role, report), report

    def ask(self, number: str, kind: str, subjects: list[str], report: str, **fields) -> None:
        """The ticket is about to block on a choice of the operator: one question of `kind` per subject. `fields`
        go to the ticket record with the stop."""
        self.asks[number] = (kind, subjects, report, fields)

    def gated(self, number: str, slot: str, commit: str) -> str:
        return _gated(number, slot, self, commit)

    def ledger(self, sentence: str) -> None:
        _ledger(sentence)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        print(f"implement-spec: {message}")
        raise SystemExit(3)


def _worktree_errors(kept: bool) -> list[str]:
    """`kept`: the uncommitted work is that of an interrupted ticket, which runs in the PR worktree again."""
    errors = []
    # The trailing slash lets a `.agent/` pattern match before the directory exists.
    if git(".", "check-ignore", "-q", ".agent/").returncode != 0:
        errors.append(".agent is not gitignored")
    status = git(".", "status", "--porcelain")
    if status.returncode != 0:
        errors.append(f"git status failed: {status.stderr.strip()}")
    return errors + [f"uncommitted change: {line[3:]}" for line in status.stdout.splitlines() if not kept]


def _timeout(text: str) -> tuple[str, float]:
    role, _, minutes = text.partition("=")
    if role not in driver_agents.TIMEOUTS:
        raise ValueError(role)
    return role, float(minutes)


def _ledger(sentence: str) -> int:
    """One plain sentence per event; returns its line number, for a pointer. Only the driver writes the ledger, and
    it takes no fact from it (requirement 16): it only counts the lines."""
    with _LEDGER:
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "ledger.md").open("a+") as ledger:
            ledger.write(" ".join(sentence.split()) + "\n")
            ledger.seek(0)
            return sum(1 for _ in ledger)


def _hygiene(slot: str, branch: str, tip: str) -> str:
    """After an agent: the ticket branch exists, is checked out in the slot and still holds `tip`. If not, reset."""
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


def _outcome(role: str, code: int | None, minutes: float, report: Path, context: dict) -> tuple[str, str]:
    """(why this attempt is rejected, the template lines the rerun prompt gets); ("", "") when accepted."""
    branch, tip = context["branch"], context["start"]
    # Two read-only agents share a slot: the driver resets nothing under the other one. The review checks the slot
    # when both are done.
    if role not in driver_agents.READ_ONLY and (moved := _hygiene(context["repo"], branch, tip)):
        return moved, driver_agents.render("moved", {"branch": branch, "tip": tip})
    if code is None:
        return f"timed out after {minutes:g} minutes", _timed_out(role, minutes, context)
    error = "; ".join(driver_report.validate(role, str(report), context))
    return error, driver_agents.render("rerun", {"error": error})


def _timed_out(role: str, minutes: float, context: dict) -> str:
    """The template lines for the rerun after a timeout. A timeout is not an agent fault: the tree stays as it is.
    A read-only role has no work to keep, so its rerun starts from the clean tip, unless another agent still
    works in the slot (`alone`): the review restores the slot when both are done."""
    values = {"minutes": f"{minutes:g}", "tip": context["start"]}
    if role not in driver_agents.READ_ONLY:
        return driver_agents.render("timeout", values)
    if context.get("alone", lambda: True)():
        driver_review.restore(context["repo"], context["branch"], context["start"])
        return driver_agents.render("timeout-restored", values)
    return driver_agents.render("timeout-shared", values)


def _agent(role: str, number: str, slot: str, values: dict[str, str], run: Run, notes: tuple | list = (),
           entry: str = "", fix_round: int = 0, check: dict | None = None) -> tuple[str, dict]:
    """Run one role in its slot, with at most one rerun. Returns ("", report) when accepted, else (the last error, {}).
    `notes` names templates whose lines the first prompt gets, as a rerun prompt gets its rejection. `entry` is the
    model entry when it is not the first of the role's list, `fix_round` numbers the files, and `check` adds to what
    the report validation knows."""
    folder, entry = OUT / number, entry or run.models[role][0]
    folder.mkdir(parents=True, exist_ok=True)
    report, log, prompt = (folder / f"{role}-{fix_round}{suffix}" for suffix in (".json", ".log", ".prompt.md"))
    # The agent works in its slot, so the paths it gets are relative to the slot.
    seen = os.path.relpath(report, slot)
    context = {"repo": slot, "branch": values["branch"], "start": values["start_sha"], "log": str(log), "round": 0,
               **(check or {})}
    argv = ([run.args.runner, role, os.path.relpath(prompt, slot), seen] if run.args.runner
            else driver_agents.command(role, entry, seen))
    text = driver_agents.render(role, {**values, "report": seen})
    if notes:
        text += "".join(driver_agents.render(note, values) for note in notes) + driver_agents.tail(text, seen)
    minutes, error = run.args.timeout[role], ""
    for _ in range(2):
        prompt.write_text(text)
        report.unlink(missing_ok=True)
        start = time.monotonic()
        code = driver_agents.launch(argv, prompt, log, minutes * 60, slot)
        seconds = time.monotonic() - start
        error, lines = _outcome(role, code, minutes, report, context)
        cost = driver_agents.cost(log)
        used = {"ticket": number, "role": role, "entry": entry, "seconds": round(seconds, 1), "cost": float(cost or 0)}
        run.state.update(lambda state: state["run"]["agents"].append(used))
        _ledger(f"Ticket {number}: {role} ran on {entry} for {seconds:.1f}s{f', cost {cost} USD' if cost else ''}: "
                f"{error or 'report accepted'}.")
        if not error:
            return "", json.loads(report.read_text())
        # The rerun prompt is the first prompt, the rejection, then the same ending again (requirement 14).
        text += lines + driver_agents.tail(text, seen)
        minutes *= 2 if code is None else 1
    return error, {}


def _stopped(role: str, report: dict) -> str:
    """A valid report whose agent did not finish blocks the ticket; it is not a completion."""
    if report.get("status") in ("BLOCKED", "NEEDS_CONTEXT"):
        return f"the {role} reported {report['status']}: {'; '.join(report['concerns']) or 'no concern given'}"
    return ""


def _todo(ticket: driver_tickets.Ticket, phase: str, only: str | None) -> list[str]:
    """The roles a ticket in `phase` still needs. A refactor ticket has no test-writer."""
    if only:
        return [only]
    if phase in ("pending", "test-writer"):
        return ["test-writer", "implementer"][ticket.refactor:]
    return ["implementer"] if phase == "implement" else []


def _placed(ticket: driver_tickets.Ticket, number: int, entry: dict, run: Run) -> tuple[str, str]:
    """Put the ticket branch in slot `number`. Returns (the commit its next role starts from, "") or ("", the error)."""
    slot, branch = driver_slots.path(number), f"ticket/{ticket.number}"
    if run.kept.get(ticket.number) == number:
        # An interrupted role left its work here: the branch and the tree stay as they are, as after a timeout.
        return entry["tip"] or git(slot, "merge-base", "HEAD", run.tip()).stdout.strip(), ""
    # `tip` is the last accepted commit of the ticket: none before the test-writer, so the branch starts at the PR tip.
    made = git(slot, "checkout", "-q", "-B", branch, entry["tip"] or run.tip())
    if made.returncode:
        return "", f"cannot check out {branch} in slot {number}: {made.stderr.strip()}"
    return driver_slots.head(slot), ""


def _values(ticket: driver_tickets.Ticket, slot: str, run: Run) -> dict[str, str]:
    """The pointers every prompt of the ticket gets. An agent works in its slot, so the paths are relative to it."""
    return {"spec": os.path.relpath(run.args.spec, slot), "seam": ticket.seam, "pr_branch": run.pr,
            "branch": f"ticket/{ticket.number}", "ticket": os.path.relpath(Path(run.args.tickets) / ticket.file, slot),
            "correctness": str(driver_review.CORRECTNESS),
            "blocked_report": os.path.relpath(OUT / ticket.number / "implementer-0.json", slot),
            "writer_report": os.path.relpath(OUT / ticket.number / "test-writer-0.json", slot)}


def _restored(number: str, slot: str, entry: dict) -> list[str]:
    """Before a correction: each locked file that the implementer changed, in a commit or in the tree, goes back to
    its reference version in the tree (requirement 7). The correction then starts from the locked version, and its
    commit carries that version plus its own change: no change of the implementer becomes the reference."""
    paths = driver_review.restore_locked(slot, entry)
    for path in paths:
        _ledger(f"Ticket {number}: {path} was not the locked version at the correction: the driver restored it in "
                "the tree, so the correction starts from the locked version.")
    return paths


def _roles(ticket: driver_tickets.Ticket, number: int, run: Run) -> str:
    """Put the ticket branch in slot `number` at the commit its phase starts from, then run the roles it still needs.
    Returns "" or where and why the ticket is blocked."""
    slot = driver_slots.path(number)
    entry = run.state.ticket(ticket.number, slot=number)
    roles = _todo(ticket, entry["phase"], run.args.only)
    start, error = _placed(ticket, number, entry, run)
    if error:
        return f"the start: {error}"
    # A ruling sent the ticket back to this role: its template says what is on the branch and in the tree.
    notes = [entry["note"]] if entry["note"] else ["interrupted"] * (run.kept.get(ticket.number) == number)
    if notes == ["interrupted"]:
        _ledger(f"Ticket {ticket.number}: resumed in slot {number} with the work the interrupted {roles[0]} left.")
    values = {**_values(ticket, slot, run), "locked": " ".join(entry["locked"])}
    for role in roles:
        run.state.ticket(ticket.number, phase=DURING.get(role, "review"))
        values["start_sha"] = start
        # A test-writer on a ticket that has locked tests is the correction after a ruling.
        check = {"correction": role == "test-writer" and bool(entry["test_writer_sha"])}
        if check["correction"]:
            check["restored"] = _restored(ticket.number, slot, entry)
        error, report = run.agent(role, ticket.number, slot, values, notes=notes, check=check)
        if error:
            return f"the {role}: {error}"
        facts = {"tip": driver_slots.head(slot)}
        if role == "test-writer":  # only its schema types `locked`
            # A correction lists only the files it corrected: the earlier ones stay locked.
            facts.update(driver_review.written(slot, entry, report["locked"], facts["tip"]))
            values["locked"] = " ".join(facts["locked"])
        run.state.ticket(ticket.number, phase=AFTER.get(role, "review"), note="", **facts)
        start, notes = facts["tip"], []
    return ""


def _gated(number: str, slot: str, run: Run, commit: str) -> str:
    """Gate `commit` in the slot, unless a green record settles it (requirement 8). Returns "" or why the ticket
    is blocked. The gate runs only on the commit the driver is about to trust: a slot that is not there is red."""
    if commit in run.state.read()["tickets"][number]["green_gates"]:
        return ""
    if (at := driver_slots.head(slot)) != commit:
        return f"the slot is at {at[:7]}, not at {commit[:7]}: the gate did not run"
    green, record = driver_gate.run(run.args.gate, slot, OUT / number, run.pr)

    def settle(state: dict) -> None:
        state["run"]["gates"] += 1
        state["tickets"][number]["green_gates"] += [commit] * green

    run.state.update(settle)
    if not green:
        return f"the gate is red on {commit[:7]}, record {record}"
    _ledger(f"Ticket {number}: the gate is green on {commit[:7]}, record {record}.")
    return ""


def _resolved(ticket: driver_tickets.Ticket, slot: str, run: Run, tip: str, files: list[str]) -> tuple[str, str]:
    """The conflict agent, once (requirement 11): the merge of the PR tip into the ticket branch is open in the slot
    with conflicts in `files`, and the agent commits it. Returns (the new ticket tip, "") or ("", why the ticket
    is blocked)."""
    number = ticket.number
    values = {**_values(ticket, slot, run), "start_sha": tip, "pr_sha": run.tip(), "files": " ".join(files)}
    error, _ = run.agent("conflict", number, slot, values)
    # The resolution may not touch a locked test: each locked file is as the reviewed tip has it.
    locked = driver_review.locked_changes(slot, tip, run.state.read()["tickets"][number]["locked"])
    if error := error or "; ".join(finding["title"] for finding in locked):
        driver_merge.abort(slot)  # the driver's own open merge; nothing to close when the agent committed it
        return "", f"the conflict agent: {error}"
    new = driver_slots.head(slot)
    run.state.ticket(number, tip=new)
    _ledger(f"Ticket {number}: the conflict agent resolved the merge conflict in {', '.join(files)} at {new[:7]}.")
    return new, ""


def _merged(ticket: driver_tickets.Ticket, slot: str, run: Run) -> str:
    """Merge the reviewed tip into the PR branch, then delete the ticket branch. Returns "" or why the ticket
    is blocked. The slot is on the ticket branch at that tip."""
    number, branch = ticket.number, f"ticket/{ticket.number}"
    # Merges are serial, the merge gate and the conflict agent included: the commit the gate saw is the commit that lands.
    with run.merging:
        tip = run.state.ticket(number, phase="merge")["tip"]
        old = run.tip()
        new, error = driver_merge.commit(old, tip, f"Merge {branch} into {run.pr}")
        if error and (files := driver_merge.begin(slot, old)):
            tip, error = _resolved(ticket, slot, run, tip, files)
            if not error:
                # Only another hand moves the PR branch now. A merge that conflicts again blocks the ticket.
                old = run.tip()
                new, error = driver_merge.commit(old, tip, f"Merge {branch} into {run.pr}")
                error = error and f"after the conflict agent, {error}"
        if not error and new != old:
            # A merge commit, or the commit of the conflict agent, is a tree no gate has seen. It is gated in the
            # ticket's own slot before the PR branch moves; a fast-forward of a gated tip has its green record.
            # A checkout that fails leaves the slot on another commit, and then the gate does not run.
            git(slot, "checkout", "-q", "--detach", new)
            error = _gated(number, slot, run, new)
        if error := error or driver_merge.land(run.pr, new, old):
            return f"the merge: {error}"
        run.state.ticket(number, phase="merged")
    driver_slots.release(slot)  # off the branch, so that no slot has it checked out when it is deleted
    kept = "" if driver_merge.delete(branch, run.pr) else f"; {branch} is kept"
    how = {tip: "a fast-forward", old: "a merge an earlier run made"}.get(new, "a merge commit")
    _ledger(f"Ticket {number}: merged into {run.pr} as {how} at {new[:7]}{kept}.")
    return ""


def _blocked(number: str, run: Run, line: int) -> None:
    """Block the ticket and, when it asked for a stop, add its questions (requirement 17), in one write of
    state.json: a stop is never half there. `line` is the ledger line that says why the ticket is blocked."""
    kind, subjects, report, fields = run.asks.pop(number, ("", [], "", {}))

    def change(state: dict) -> None:
        state["tickets"][number].update(fields, phase="blocked")
        asked = state["run"]["questions"]
        state["run"]["stops"] += bool(subjects)
        for subject in subjects:
            asked.append(driver_decisions.question(kind, number, subject, [report, f"{OUT / 'ledger.md'}:{line}"],
                                                   asked, state["run"]["stops"]))

    run.state.update(change)
    if subjects:
        _ledger(f"Ticket {number}: the operator decides on {len(subjects)} question{'s' * (len(subjects) > 1)} of "
                f"kind {kind} in {DECISIONS}.")


def _ticket(ticket: driver_tickets.Ticket, number: int, run: Run) -> bool:
    """One ticket in slot `number`, from the phase state.json records to merged or blocked. True: the slot is free.
    A blocked ticket keeps a slot that holds uncommitted work, for the operator to look at."""
    slot = driver_slots.path(number)
    error = _roles(ticket, number, run)
    if not (error or run.args.only):
        # A ticket that an earlier run left in `merge` has passed its review.
        reviewed = run.state.read()["tickets"][ticket.number]["phase"] == "merge"
        error = "" if reviewed else driver_fixloop.run(ticket, slot, run, _values(ticket, slot, run))
        error = error or _merged(ticket, slot, run)
    if error:
        _blocked(ticket.number, run, _ledger(f"Ticket {ticket.number}: BLOCKED at {error}."))
        print(f"{ticket.file}: BLOCKED at {error}")
    return driver_slots.release(slot)


def _next(tickets: list[driver_tickets.Ticket], running: dict, told: set, run: Run, kept: dict) -> driver_tickets.Ticket | None:
    """The first ticket that may start now; a ticket whose slot is kept for it goes first, because it needs no free
    slot. Each new hold goes to the ledger once."""
    phases = {number: entry["phase"] for number, entry in run.state.read()["tickets"].items()}
    ready, holds = driver_graph.frontier(tickets, phases, {number for number, _ in running.values()})
    for hold in sorted(set(holds.items()) - told):
        told.add(hold)
        _ledger(f"Ticket {hold[0]}: {hold[1]}.")
    return next((ticket for ticket in ready if ticket.number in kept), ready[0]) if ready else None


def _walk(tickets: list[driver_tickets.Ticket], slots: int, run: Run, pool: ThreadPoolExecutor) -> None:
    """Run every ticket that can run: each ready ticket in the lowest free slot, a new look at each finish."""
    kept, told = dict(run.kept), set()  # a kept slot waits for its ticket: no other ticket gets it before
    free = [slot for slot in range(1, slots + 1) if slot not in kept.values()]
    running: dict = {}  # future -> (ticket number, slot number)

    def start() -> None:
        while (ticket := _next(tickets, running, told, run, kept)) and (ticket.number in kept or free):
            slot = kept.pop(ticket.number, None) or free.pop(free.index(min(free)))
            running[pool.submit(_ticket, ticket, slot, run)] = (ticket.number, slot)

    while start() or running:
        for future in wait(running, return_when=FIRST_COMPLETED).done:
            _, slot = running.pop(future)
            if future.result():
                free.append(slot)


def _tickets(tickets: list[driver_tickets.Ticket], slots: int, run: Run) -> None:
    """One thread per ticket in flight; this thread only schedules."""
    with ThreadPoolExecutor(slots) as pool:
        try:
            _walk(tickets, slots, run, pool)
        except KeyboardInterrupt:
            # The ticket threads would run their agents to the end. Every state.json write is atomic, so a hard
            # stop loses nothing that a rerun needs.
            driver_agents.stop()
            _ledger("The run was interrupted; the work of the stopped agents stays in their slots for the next run.")
            print("implement-spec: interrupted; run it again to resume from state.json", flush=True)
            os._exit(130)


def _pr_branch(state: driver_state.State) -> tuple[str, str]:
    """(the PR branch, "") or ("", why the run cannot start)."""
    here = git(".", "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    # An interrupted run leaves slot 1 on a ticket branch or detached, so the record names the PR branch.
    pr = state.read()["run"]["pr_branch"] if state.path.exists() else here
    if not pr:
        return "", "the PR worktree is on a detached HEAD: check out the PR branch"
    if here not in ("", pr) and not here.startswith("ticket/"):
        return "", f"{state.path} records a run on {pr}, not on {here}: check out {pr}, or remove {OUT}"
    return pr, ""


def _resumed(number: str, state: driver_state.State) -> str:
    """Check one ticket's record against git; "" or the error that stops the run. A ticket whose record git
    contradicts goes back to its last verified phase, with the reason in the ledger (requirement 15)."""
    entry, branch = state.read()["tickets"][number], f"ticket/{number}"
    if entry["phase"] not in driver_state.PHASES:
        return f"{state.path}: ticket {number} has the unknown phase {entry['phase']!r}"
    if entry["phase"] == "pending" and git(".", "rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode == 0:
        return f"branch {branch} exists and {state.path} does not record it: delete or rename the branch"
    fields, why = driver_state.verified(entry, branch, state.read()["run"]["pr_branch"])
    if fields:
        state.ticket(number, **fields)
        _ledger(f"Ticket {number}: returned to {fields['phase']} from "
                f"{fields['tip'][:7] if fields['tip'] else 'the PR tip'}: {why}.")
    return ""


def _checked(tickets: list[driver_tickets.Ticket], state: driver_state.State, slots: int, args, kept: dict) -> tuple[str, list[str]]:
    """(the PR branch, the errors that stop the run before any agent). state.json is the only resume input."""
    pr, error = _pr_branch(state)
    if error:
        return "", [error]
    state.start(pr, [ticket.number for ticket in tickets])
    errors = [error for error in (_resumed(ticket.number, state) for ticket in tickets) if error]
    return pr, errors or driver_slots.setup(slots, args.prepare, set(kept.values()))


def _published(run: dict) -> str:
    """Write decisions.json and the review page for the questions of `run` of state.json (requirement 17). Returns
    "" or why the page is not there. A run that never stopped writes neither, and no open question needs no page."""
    if not run["questions"]:
        return ""
    document = driver_decisions.document(run)
    DECISIONS.write_text(json.dumps(document, indent=1) + "\n")
    PAGE.unlink(missing_ok=True)
    try:
        if document["questions"]:
            PAGE.write_text(driver_review_page.render(document))
    except (OSError, ValueError) as exc:
        return (f"implement-spec: cannot write {PAGE} for the open questions of {DECISIONS}: {exc}; install the "
                "review-page skill next to implement-spec")
    return ""


def _answers(state: driver_state.State, args) -> list[str]:
    """Before any agent: `--answer` writes the answers file, then each answer of the file that counts becomes a
    ruling in the ledger and lets its ticket go on (requirement 17). Returns [] or the errors that stop the run.
    The ledger comes first: a run that is stopped between the two writes gives a ruling twice, never an answer
    that no ledger line names."""
    run = state.read()["run"]
    questions, document = run["questions"], driver_decisions.document(run)
    if errors := driver_answers.given(args.answer, document):
        return errors
    answers, ignored = driver_answers.read(document)
    rulings, moved = driver_decisions.apply(questions, answers)
    for sentence in ignored + rulings:
        _ledger(sentence)

    def change(new: dict) -> None:
        new["run"]["questions"] = questions
        for number, fields in moved.items():
            new["tickets"][number].update(fields)

    state.update(change)
    error = _published(run)
    return [error] if error else []


def _stage(path: str | None, code: int, questions: int, blocked: list[str]) -> None:
    """stage.json, for the dispatcher of an unattended run that did not end with exit 0 (requirement 18)."""
    if not (path and code):
        return
    body = ({"status": "awaiting-review", "artifact": str(PAGE), "open_questions": questions} if code == 2
            else {"status": "blocked", "blocked": blocked})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"stage": "implement", **body}, indent=1) + "\n")


def _ended(final: dict, args) -> int:
    """The exit code (requirement 20), after the questions are published and the operator, or the dispatcher of
    an unattended run, is told (requirement 18). Only a question whose kind parks gives exit 2."""
    if error := _published(final["run"]):
        print(error)
        return 3
    blocked = [number for number, entry in final["tickets"].items() if entry["phase"] == "blocked"]
    still = driver_decisions.document(final["run"])["questions"]
    code = 2 if driver_decisions.parked(final["run"]["questions"]) else int(bool(blocked))
    if args.unattended:
        _stage(args.stage_json, code, len(still), blocked)
    else:
        print(driver_decisions.text(still, str(driver_answers.FILE), str(PAGE)), end="")
    return code


def _run(tickets: list[driver_tickets.Ticket], models: dict[str, list[str]], args, kept: dict[str, int]) -> int:
    state = driver_state.State(STATE)
    slots = 1 if args.only else min(args.parallel, len(tickets))
    pr, errors = _checked(tickets, state, slots, args, kept)
    errors = errors or _answers(state, args)
    for error in errors:
        print(error)
    if errors:
        return 3
    run = Run(args, models, state, pr, kept)
    if args.unattended and args.stage_json:
        Path(args.stage_json).unlink(missing_ok=True)  # the file tells how this run ends, never an earlier one
    # The PR branch is checked out in no slot while tickets run: a merge moves its ref and no tree changes under it.
    # A kept slot 1 is on its ticket branch already.
    if 1 not in kept.values():
        git(".", "checkout", "-q", "--detach")
    try:
        _ticket(tickets[0], 1, run) if args.only else _tickets(tickets, slots, run)
    finally:
        if driver_slots.release("."):  # slot 1 keeps the uncommitted work of a blocked ticket; else it goes back
            git(".", "checkout", "-q", pr)
    final = state.read()
    summary = driver_summary.render(final)
    (OUT / "summary.md").write_text(summary)
    print(summary, end="")
    return _ended(final, args)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    defaults = ", ".join(f"{role} {minutes}" for role, minutes in driver_agents.TIMEOUTS.items())
    parser = _Parser(prog="implement-spec", description="Validate tickets and preconditions, then walk the graph.")
    parser.add_argument("--tickets", default=".agent/tickets", help="directory of NN-name.md ticket files")
    parser.add_argument("--gate", help="the repo's check command (required); the driver runs it on each commit it "
                        "is about to trust, with IMPLEMENT_SPEC_BASE set to the PR branch")
    parser.add_argument("--spec", help="the spec file the prompts point at (required without --dry-run)")
    parser.add_argument("--models", help="JSON file: one ordered list of provider/model@effort per role, "
                        "on top of the skill's models.json")
    parser.add_argument("--timeout", action="append", type=_timeout, default=[], metavar="ROLE=MINUTES",
                        help=f"timeout of one role, repeatable; doubled on the one rerun (defaults: {defaults})")
    parser.add_argument("--dry-run", action="store_true", help="validate, print the frontier width, change nothing")
    parser.add_argument("--runner", help="test only: a script called as `<script> <role> <prompt> <report>` "
                        "in the slot, in place of the CLI")
    parser.add_argument("--parallel", type=int, default=2, help="tickets in flight at once (default 2)")
    parser.add_argument("--prepare", help="shell command run once in each new slot 2..N, in "
                        ".agent/worktrees/slot-K/ (for example: link the installed dependencies)")
    parser.add_argument("--answer", action="append", type=driver_answers.pair, default=[],
                        metavar="QUESTION=OPTION", help="the answer to one open question of "
                        f"{DECISIONS}, repeatable; it goes to {driver_answers.FILE}")
    parser.add_argument("--unattended", action="store_true", help="at a question, print no question: write "
                        "--stage-json for the dispatcher, which gives the answers file to the next run")
    parser.add_argument("--stage-json", metavar="FILE", help="with --unattended: where the driver writes how a "
                        "run ended that is parked (exit 2) or has a BLOCKED ticket (exit 1)")
    parser.add_argument("--only", choices=ONLY, help="test only: run this one role on the first ticket")
    args = parser.parse_args(argv)
    args.timeout = {**driver_agents.TIMEOUTS, **dict(args.timeout)}
    return args


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    errors = [] if args.gate else ["--gate is required: the command the driver runs as the gate"]
    if not (args.spec or args.dry_run):
        errors.append("--spec is required: the spec file the prompts point at")
    if args.parallel < 1:
        errors.append("--parallel must be 1 or more")
    # After an interrupt, a slot may hold the work of the role that was stopped, and after a stop the work of the
    # role that asks the operator: that is not an error (requirements 15 and 18).
    kept = driver_state.kept(json.loads(STATE.read_text())) if STATE.exists() else {}
    errors += _worktree_errors(1 in kept.values())
    models, model_errors = driver_agents.load_models(args.models)
    tickets, ticket_errors = driver_tickets.parse(args.tickets)
    errors += model_errors + ticket_errors + driver_review.missing(tickets)
    for error in errors:
        print(error)
    if errors:
        return 3
    print(f"max frontier width: {driver_tickets.max_frontier_width(tickets)}")
    return 0 if args.dry_run else _run(tickets, models, args, kept)


if __name__ == "__main__":
    sys.exit(main())
