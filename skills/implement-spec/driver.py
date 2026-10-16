#!/usr/bin/env python3
"""implement-spec driver: validate the ticket set and the environment, then walk the ticket graph.

    sh run.sh --gate COMMAND --spec FILE [--tickets DIR] [--models FILE] [--parallel N] [--prepare COMMAND]
              [--timeout ROLE=MINUTES] [--answer QUESTION=OPTION] [--unattended [--stage-json FILE]] [--push]
              [--dry-run]

Run it in the PR worktree. Exit 0 = every ticket merged (and pushed, when unattended), 1 = a ticket or the end of
run is BLOCKED, 2 = parked on a question, 3 = usage or validation error, 4 = stopped by a CLI failure. README.md has the method and the files of
a run; ARCHITECTURE.md has the map of the modules and the rules the code keeps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import driver_agents
import driver_answers
import driver_decisions
import driver_final
import driver_fixloop
import driver_graph
import driver_legacy
import driver_merge
import driver_review
import driver_review_page
import driver_slots
import driver_state
import driver_summary
import driver_tickets
from driver_names import Names
from driver_run import FINAL, OUT, Blocked, Halted, Run, during, ledger
from driver_slots import git
from driver_tickets import Ticket

STATE = OUT / "state.json"
DECISIONS = OUT / "decisions.json"
PAGE = Path(".agent/implement-review.html")
ONLY = ("test-writer", "implementer", "reviewer", "correctness")  # `--only` is for tests
# The phase of a ticket while a role runs, and the phase it reaches when the role's report is accepted.
DURING = {"test-writer": "test-writer", "implementer": "implement"}
AFTER = {"test-writer": "implement", "implementer": "gate"}


# ---------------------------------------------------------------- one ticket: the roles

def _roles_left(ticket: Ticket, phase: str, only: str | None) -> list[str]:
    if only:
        return [only]
    if phase == "implement":
        return ["implementer"]
    if phase not in ("pending", "test-writer"):
        return []
    return ["implementer"] if ticket.refactor else ["test-writer", "implementer"]


def _check_out_ticket(ticket: Ticket, slot_number: int, entry: dict, run: Run) -> str:
    """Put the ticket branch in the slot and return the commit its next role starts from."""
    slot, branch = driver_slots.path(slot_number), run.names.ticket(ticket.number)
    if run.kept.get(ticket.number) == slot_number:
        # The slot holds the uncommitted work of an interrupted role: the branch and the tree stay as they are.
        return entry["tip"] or git(slot, "merge-base", "HEAD", run.tip()).stdout.strip()
    made = git(slot, "checkout", "-q", "-B", branch, entry["tip"] or run.tip())
    if made.returncode:
        raise Blocked(f"cannot check out {branch} in slot {slot_number}: {made.stderr.strip()}")
    return driver_slots.head(slot)


def _blocked_report(number: str, run: Run) -> str:
    """The report of the last `locked-test` stop of the ticket: an implementer's or a fixer's."""
    asked = [q["evidence"][0] for q in run.state.read()["run"]["questions"]
             if (q["ticket"], q["kind"]) == (number, "locked-test")]
    return asked[-1] if asked else str(OUT / number / "implementer-0.json")


def _prompt_values(ticket: Ticket, slot: str, run: Run) -> dict[str, str]:
    """The pointers every prompt of the ticket gets. An agent works in its slot, so paths are relative to it."""
    folder = OUT / ticket.number
    return {"spec": os.path.relpath(run.args.spec, slot), "seam": ticket.seam, "pr_branch": run.pr,
            "branch": run.names.ticket(ticket.number),
            "ticket": os.path.relpath(Path(run.args.tickets) / ticket.file, slot),
            "correctness": str(driver_review.CORRECTNESS), "gate": run.args.gate,
            "blocked_report": os.path.relpath(_blocked_report(ticket.number, run), slot),
            "writer_report": os.path.relpath(folder / "test-writer-0.json", slot)}


def _run_roles(ticket: Ticket, slot_number: int, run: Run) -> None:
    """Run the test-writer and the implementer, as far as the phase of the ticket still needs them."""
    number, slot = ticket.number, driver_slots.path(slot_number)
    entry = run.state.ticket(number, slot=slot_number)
    roles = _roles_left(ticket, entry["phase"], run.args.only)
    with during("the start"):
        start = _check_out_ticket(ticket, slot_number, entry, run)
    # The first role gets one more template: the ruling that sent the ticket back, or that its work was interrupted.
    if entry["note"]:
        notes = [entry["note"]]
    elif run.kept.get(number) == slot_number:
        notes = ["interrupted"]
        ledger(f"Ticket {number}: resumed in slot {slot_number} with the work the interrupted {roles[0]} left.")
    else:
        notes = []
    values = {**_prompt_values(ticket, slot, run), "locked": " ".join(entry["locked"])}
    for role in roles:
        run.state.ticket(number, phase=DURING.get(role, "review"))
        values["start_sha"] = start
        # A test-writer on a ticket that has locked tests is the correction after a ruling.
        check = {"correction": role == "test-writer" and bool(entry["test_writer_sha"])}
        if check["correction"] and (restored := run.restore_locked_files(number, slot)):
            values.update(start_sha=driver_slots.head(slot), restored=" ".join(restored))
            notes, check["restored"] = [*notes, "restored"], restored
        with during(f"the {role}"):
            report = run.agent(role, number, slot, values, notes=notes, check=check)
        facts = {"tip": driver_slots.head(slot)}
        if role == "test-writer":
            facts.update(driver_review.written(slot, entry, report["locked"], facts["tip"]))
            values["locked"] = " ".join(facts["locked"])
        run.state.ticket(number, phase=AFTER.get(role, "review"), note="", **facts)
        start, notes = facts["tip"], []


# ---------------------------------------------------------------- one ticket: the landing on the PR branch

@dataclass
class _Landing:
    ticket_tip: str
    pr_tip: str  # the PR tip that the landing check read
    commit: str  # what the PR branch moves to: `pr_tip` (merged already), `ticket_tip` (fast-forward) or a merge


def _resolve_conflict(ticket: Ticket, slot: str, run: Run, tip: str, files: list[str]) -> str:
    """Run the conflict agent on the merge that is open in the slot. Returns the new ticket tip."""
    number = ticket.number
    values = {**_prompt_values(ticket, slot, run), "start_sha": tip, "pr_sha": run.tip(), "files": " ".join(files)}
    try:
        with during("the conflict agent"):
            run.agent("conflict", number, slot, values)
            # The resolution may not change a locked test of the ticket. Those of the merged tickets are checked
            # when the resolved tip lands.
            changed = driver_review.locked_changes(slot, tip, run.state.read()["tickets"][number]["locked"])
            if changed:
                raise Blocked("; ".join(finding["title"] for finding in changed))
    except (Blocked, Halted):
        driver_merge.abort(slot)  # closes the merge when the agent did not commit it
        raise
    resolved = driver_slots.head(slot)
    run.state.ticket(number, tip=resolved)
    ledger(f"Ticket {number}: the conflict agent resolved the merge conflict in {', '.join(files)} at {resolved[:7]}.")
    return resolved


def _merge_commit(ticket: Ticket, slot: str, run: Run, tip: str, may_resolve: bool) -> _Landing:
    """Merge the ticket tip with the PR tip into a commit that no ref holds yet."""
    message = f"Merge {run.names.ticket(ticket.number)} into {run.pr}"
    pr_tip = run.tip()
    commit, error = driver_merge.commit(pr_tip, tip, message)
    if not error:
        return _Landing(tip, pr_tip, commit)
    conflicts = driver_merge.begin(slot, pr_tip) if may_resolve else []
    if not conflicts:
        raise Blocked(error)
    tip = _resolve_conflict(ticket, slot, run, tip, conflicts)
    pr_tip = run.tip()
    commit, error = driver_merge.commit(pr_tip, tip, message)
    if error:
        raise Blocked(f"after the conflict agent, {error}")
    return _Landing(tip, pr_tip, commit)


def _checked_landing(ticket: Ticket, slot: str, run: Run, may_resolve: bool) -> _Landing:
    """The commit to land, with the locked-file check passed and the gate green. Call it under the merge lock."""
    number = ticket.number
    # An earlier try left the slot on its merge commit.
    git(slot, "checkout", "-q", run.names.ticket(number))
    tip = run.state.ticket(number, phase="merge")["tip"]
    landing = _merge_commit(ticket, slot, run, tip, may_resolve)
    refusal = driver_merge.refused(run.state.read()["tickets"], number, landing.pr_tip, landing.commit)
    if refusal:
        raise Blocked(refusal)
    if landing.commit != landing.pr_tip:
        # No gate has seen a merge commit. A fast-forward to a gated tip finds its green record.
        # When the checkout fails, the slot is on another commit and the gate refuses to run.
        git(slot, "checkout", "-q", "--detach", landing.commit)
        run.gate(number, slot, landing.commit)
    return landing


def _land(ticket: Ticket, slot: str, run: Run) -> _Landing:
    """Move the PR branch to the checked commit. Call it under the merge lock."""
    landing = _checked_landing(ticket, slot, run, may_resolve=True)
    # git moves the ref only from the tip that the check read, so the check and the move are one step.
    error = driver_merge.land(run.pr, landing.commit, landing.pr_tip)
    if error and run.tip() != landing.pr_tip:
        # Another hand moved the PR branch in between: start again from the new tip, one time, with no conflict agent.
        landing = _checked_landing(ticket, slot, run, may_resolve=False)
        error = driver_merge.land(run.pr, landing.commit, landing.pr_tip)
    if error:
        raise Blocked(error)
    return landing


def _record_landing(state: dict, number: str, pr_tip: str) -> None:
    """The ticket is merged and `pr_tip` is the PR tip its merge made. A later merge of the ticket replaces it."""
    state["tickets"][number]["phase"] = "merged"
    earlier = [landed for landed in state["run"]["landed"] if landed[0] != number]
    state["run"]["landed"] = earlier + [[number, pr_tip]]


def _merge(ticket: Ticket, slot: str, run: Run) -> None:
    """Land the reviewed ticket tip on the PR branch, then delete the ticket branch."""
    number, branch = ticket.number, run.names.ticket(ticket.number)
    # The merge gate and the conflict agent run under the lock: the commit the gate saw is the one that lands.
    with run.merging, during("the merge"):
        landing = _land(ticket, slot, run)
        run.state.update(lambda state: _record_landing(state, number, landing.commit))
    driver_slots.release(slot)  # git deletes no branch that a worktree has checked out
    kept = "" if driver_merge.delete(branch, run.pr) else f"; {branch} is kept"
    if landing.commit == landing.pr_tip:
        how = "a merge an earlier run made"
    elif landing.commit == landing.ticket_tip:
        how = "a fast-forward"
    else:
        how = "a merge commit"
    ledger(f"Ticket {number}: merged into {run.pr} as {how} at {landing.commit[:7]}{kept}.")


# ---------------------------------------------------------------- one ticket, and the walk of the graph

def _block(number: str, run: Run, ledger_line: int) -> None:
    """Block the ticket and add the questions it asked for, in one write of state.json."""
    ask = run.asks.pop(number, None)

    def change(state: dict) -> None:
        state["tickets"][number]["phase"] = "blocked"
        if ask:
            state["tickets"][number].update(ask.fields)
            evidence = [ask.report, f"{OUT / 'ledger.md'}:{ledger_line}"]
            driver_decisions.stop(state["run"], ask.kind, number, ask.subjects, evidence)

    run.state.update(change)
    if ask and ask.subjects:
        ledger(f"Ticket {number}: {driver_decisions.decides(ask.kind, len(ask.subjects))} in {DECISIONS}.")


def _halt(run: Run, owner: str, why: str) -> None:
    """Start no other agent: the run ends with exit 4, and `owner` keeps its phase for the next run."""
    run.halted.append(why)
    ledger(f"{owner}: the run stops and the phase stays for the next run: {why}.")


def _run_ticket(ticket: Ticket, slot_number: int, run: Run) -> bool:
    """Take one ticket from its recorded phase to merged or blocked. True: the slot is free again."""
    number, slot = ticket.number, driver_slots.path(slot_number)
    try:
        _run_roles(ticket, slot_number, run)
        if not run.args.only:
            # A ticket that an earlier run left in `merge` has passed its review.
            if run.state.read()["tickets"][number]["phase"] != "merge":
                driver_fixloop.run(ticket, slot, run, _prompt_values(ticket, slot, run))
            _merge(ticket, slot, run)
    except Blocked as blocked:
        _block(number, run, ledger(f"Ticket {number}: BLOCKED at {blocked}."))
        print(f"{ticket.file}: BLOCKED at {blocked}")
    except Halted as halted:
        _halt(run, f"Ticket {number}", str(halted))
    return driver_slots.release(slot)  # False: the slot keeps the uncommitted work of the blocked ticket


def _next_ticket(tickets: list[Ticket], running: dict, told: set, run: Run, kept: dict) -> Ticket | None:
    """The first ticket that may start now. A ticket whose slot is kept for it goes first: it needs no free slot."""
    if run.halted:
        return None
    phases = {number: entry["phase"] for number, entry in run.state.read()["tickets"].items()}
    ready, holds = driver_graph.frontier(tickets, phases, {number for number, _ in running.values()})
    for hold in sorted(set(holds.items()) - told):
        told.add(hold)
        ledger(f"Ticket {hold[0]}: {hold[1]}.")
    if not ready:
        return None
    return next((ticket for ticket in ready if ticket.number in kept), ready[0])


def _take_slot(number: str, kept: dict, free: list[int]) -> int:
    if number in kept:
        return kept.pop(number)
    slot = min(free)
    free.remove(slot)
    return slot


def _walk(tickets: list[Ticket], slots: int, run: Run, pool: ThreadPoolExecutor) -> None:
    """Start each ready ticket in the lowest free slot, and look again each time a ticket ends."""
    kept, told = dict(run.kept), set()  # a kept slot waits for its ticket
    free = [slot for slot in range(1, slots + 1) if slot not in kept.values()]
    running: dict = {}  # future -> (ticket number, slot number)

    def start_ready() -> None:
        while (ticket := _next_ticket(tickets, running, told, run, kept)) and (ticket.number in kept or free):
            slot = _take_slot(ticket.number, kept, free)
            running[pool.submit(_run_ticket, ticket, slot, run)] = (ticket.number, slot)

    while True:
        start_ready()
        if not running:
            return
        for future in wait(running, return_when=FIRST_COMPLETED).done:
            _, slot = running.pop(future)
            if future.result():
                free.append(slot)


def _run_tickets(tickets: list[Ticket], slots: int, run: Run) -> None:
    """One thread per ticket in flight; this thread only schedules."""
    with ThreadPoolExecutor(slots) as pool:
        try:
            _walk(tickets, slots, run, pool)
        except KeyboardInterrupt:
            # A hard exit: the ticket threads would run their agents to the end, and state.json writes are atomic.
            driver_agents.stop()
            ledger("The run was interrupted; the work of the stopped agents stays in their slots for the next run.")
            print("implement-spec: interrupted; run it again to resume from state.json", flush=True)
            os._exit(130)


def _work(tickets: list[Ticket], slots: int, run: Run) -> tuple[bool, list[str]]:
    """The tickets, then the end of run: (the end of run is BLOCKED, the lines it adds to the summary)."""
    only = run.args.only
    # No slot has the PR branch checked out while tickets run: a merge moves its ref and no tree changes under it.
    if 1 not in run.kept.values():
        git(".", "checkout", "-q", "--detach")
    try:
        if only:
            _run_ticket(tickets[0], 1, run)
        else:
            _run_tickets(tickets, slots, run)
    finally:
        if driver_slots.release("."):  # slot 1 is clean, so it goes back to the PR branch
            git(".", "checkout", "-q", run.pr)
    # Every ticket thread has ended, so every slot is idle: only now may the end of run rewrite the PR branch.
    if only:
        return False, []
    try:
        return driver_final.run(run, tickets)
    except Halted as halted:
        _halt(run, "End of run", str(halted))
        return False, []


# ---------------------------------------------------------------- before the first agent: resume and answers

def _resume_error(number: str, state: driver_state.State) -> str:
    """Check the record of one ticket against git. A ticket that git contradicts goes back to its last verified
    phase; "" or the error that stops the run."""
    run, entry = state.read()["run"], state.read()["tickets"][number]
    branch = Names(run["pr_branch"]).ticket(number)
    if entry["phase"] not in driver_state.PHASES:
        return f"{state.path}: ticket {number} has the unknown phase {entry['phase']!r}"
    if entry["phase"] == "pending" and driver_slots.branch_exists(branch):
        return f"branch {branch} exists and {state.path} does not record it: delete or rename the branch"
    fields, why = driver_state.verified(entry, branch, run["pr_branch"])
    if fields:
        state.ticket(number, **fields)
        start = fields["tip"][:7] if fields["tip"] else "the PR tip"
        ledger(f"Ticket {number}: returned to {fields['phase']} from {start}: {why}.")
    return ""


def _resume(tickets: list[Ticket], state: driver_state.State, pr: str, slots: int, args, kept: dict) -> list[str]:
    """Start or resume state.json on the PR branch `pr` and make the slots: the errors that stop the run."""
    state.start(pr, [ticket.number for ticket in tickets])
    driver_final.finish_interrupted_move(state, pr)
    errors = [error for error in (_resume_error(ticket.number, state) for ticket in tickets) if error]
    return errors or driver_slots.setup(slots, args.prepare, set(kept.values()))


def _publish_questions(run: dict) -> str:
    """Write decisions.json and the review page for the questions of `run` of state.json; "" or the error."""
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


def _apply_answers(state: driver_state.State, args) -> list[str]:
    """Turn each answer that counts into a ruling and let its ticket go on; [] or the errors that stop the run."""
    run = state.read()["run"]
    questions, document = run["questions"], driver_decisions.document(run)
    if errors := driver_answers.given(args.answer, document):
        return errors
    answers, ignored = driver_answers.read(document)
    rulings, moved = driver_decisions.apply(questions, answers)
    # The ledger comes first: a run that stops between the two writes gives a ruling twice, and never an answer
    # that no ledger line names.
    for sentence in ignored + rulings:
        ledger(sentence)

    def change(new: dict) -> None:
        new["run"]["questions"] = questions
        for number, fields in moved.items():
            new["tickets"][number].update(fields)

    state.update(change)
    error = _publish_questions(run)
    return [error] if error else []


# ---------------------------------------------------------------- the end of the process

def _write_stage(path: str, code: int, open_questions: int, blocked: list[str]) -> None:
    """stage.json tells the dispatcher of an unattended run how the run ended."""
    if code == 2:
        body = {"status": "awaiting-review", "artifact": str(PAGE), "open_questions": open_questions}
    elif code == 0:
        body = {"status": "done"}
    else:
        body = {"status": "blocked", "blocked": blocked}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"stage": "implement", **body}, indent=1) + "\n")


def _tell(args, code: int, open_questions: list[dict], blocked: list[str], pushed: bool) -> None:
    """Print the open questions for the operator, or write stage.json for the dispatcher of an unattended run."""
    if not args.unattended:
        print(driver_decisions.text(open_questions, str(driver_answers.FILE), str(PAGE)), end="")
    elif args.stage_json and (code or pushed):
        # Exit 0 with nothing pushed is not `done` for the dispatcher: it gets no file.
        _write_stage(args.stage_json, code, len(open_questions), blocked)


def _halted_exit(halted: list[str], final: dict) -> int:
    """Exit 4 after a CLI failure: no ticket is BLOCKED by it, and the same command resumes the run."""
    for why in halted:
        print(f"implement-spec: the run stopped: {why}")
    print("implement-spec: fix the CLI or the model entry (--models FILE), then run the same command again to "
          "resume; no ticket is BLOCKED by this.")
    if error := _publish_questions(final["run"]):
        print(error)
    return 4


def _exit_code(final: dict, args, end_blocked: bool) -> int:
    """Publish the open questions, tell the operator or the dispatcher, and return the exit code."""
    if error := _publish_questions(final["run"]):
        print(error)
        return 3
    blocked = [number for number, entry in final["tickets"].items() if entry["phase"] == "blocked"]
    if end_blocked:
        blocked.append(FINAL)
    open_questions = driver_decisions.document(final["run"])["questions"]
    code = 2 if driver_decisions.parked(final["run"]["questions"]) else int(bool(blocked))
    _tell(args, code, open_questions, blocked, bool(final["run"]["final"]["pushed"]))
    return code


def _run(tickets: list[Ticket], models: dict[str, list[str]], args, kept: dict[str, int], pr: str) -> int:
    state = driver_state.State(STATE)
    slots = 1 if args.only else min(args.parallel, len(tickets))
    errors = _resume(tickets, state, pr, slots, args, kept)
    errors = errors or _apply_answers(state, args)
    for error in errors:
        print(error)
    if errors:
        return 3
    run = Run(args, models, state, pr, kept)
    if args.unattended and args.stage_json:
        Path(args.stage_json).unlink(missing_ok=True)  # the file tells how this run ends, never an earlier one
    end_blocked, lines = _work(tickets, slots, run)
    final = state.read()
    summary = driver_summary.render(final)
    (OUT / "summary.md").write_text(summary)
    print(summary + "".join(line + "\n" for line in lines), end="")
    if run.halted:
        return _halted_exit(run.halted, final)
    return _exit_code(final, args, end_blocked)


# ---------------------------------------------------------------- the command line

class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        print(f"implement-spec: {message}")
        raise SystemExit(3)


def _timeout(text: str) -> tuple[str, float]:
    role, _, minutes = text.partition("=")
    if role not in driver_agents.TIMEOUTS:
        raise ValueError(role)
    return role, float(minutes)


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
    parser.add_argument("--push", action="store_true", help="local mode: when the final review has passed, push "
                        "the PR branch to origin and open the PR; without it nothing is pushed")
    parser.add_argument("--only", choices=ONLY, help="test only: run this one role on the first ticket")
    args = parser.parse_args(argv)
    args.argv = list(sys.argv[1:] if argv is None else argv)  # for the rerun command the driver prints
    args.timeout = {**driver_agents.TIMEOUTS, **dict(args.timeout)}
    return args


def _worktree_errors(slot_1_is_kept: bool) -> list[str]:
    errors = []
    # The trailing slash lets a `.agent/` pattern match before the directory exists.
    if git(".", "check-ignore", "-q", ".agent/").returncode != 0:
        errors.append(".agent is not gitignored")
    status = git(".", "status", "--porcelain")
    if status.returncode != 0:
        errors.append(f"git status failed: {status.stderr.strip()}")
    if not slot_1_is_kept:  # a kept slot 1 holds the uncommitted work of a ticket that runs there again
        errors += [f"uncommitted change: {line[3:]}" for line in status.stdout.splitlines()]
    return errors


def _validated(args: argparse.Namespace) -> tuple[list[Ticket], dict[str, list[str]], dict[str, int]] | None:
    """(the tickets, the models, the kept slots) of a valid call, or None with each error printed."""
    errors = [] if args.gate else ["--gate is required: the command the driver runs as the gate"]
    if not (args.spec or args.dry_run):
        errors.append("--spec is required: the spec file the prompts point at")
    if args.parallel < 1:
        errors.append("--parallel must be 1 or more")
    kept = driver_state.kept(driver_state.State(STATE).read()) if STATE.exists() else {}
    errors += _worktree_errors(1 in kept.values())
    models, model_errors = driver_agents.load_models(args.models)
    tickets, ticket_errors = driver_tickets.parse(args.tickets)
    errors += model_errors + ticket_errors + driver_review.precondition_errors(tickets)
    errors += driver_final.precondition_errors(args.push or args.unattended)
    for error in errors:
        print(error)
    return None if errors else (tickets, models, kept)


def _print_width(tickets: list[Ticket]) -> None:
    print(f"max frontier width: {driver_tickets.max_frontier_width(tickets)}")


def _run_branch() -> str:
    """The PR branch the run in this worktree is on: the one state.json records, else the one checked out."""
    # HEAD first: a driver writes state.json before it moves HEAD, so a HEAD that a run moved has a state.json.
    here = driver_slots.on_branch(".")
    return driver_state.State(STATE).read()["run"]["pr_branch"] if STATE.exists() else here


def _start_errors(pr: str) -> list[str]:
    """Under the lock of `pr`, before any check reads a ref: the errors that stop the run, or [].

    The run of this worktree must still be on `pr`, a run that an older driver started gets its new names first,
    and slot 1 must be on `pr` or where an interrupted run left it: on one of its ticket branches, or detached."""
    if _run_branch() != pr:
        return [f"the run in this worktree left {pr} while the driver took its lock: run the driver again"]
    if errors := driver_legacy.resume(driver_state.State(STATE)):
        return errors
    here = driver_slots.on_branch(".")
    # An older driver named its ticket branches `ticket/NN`, and a branch that the resume does not rename stays so.
    if here in ("", pr) or here.startswith(Names(pr).ticket("")) or re.fullmatch(r"ticket/\d+", here):
        return []
    return [f"{STATE} records a run on {pr}, not on {here}: check out {pr}, or remove {OUT}"]


def _common_dir_and_branch(args: argparse.Namespace) -> tuple[Path, str] | None:
    """(the git common directory, which every worktree shares and `git status` never reports, and the PR branch of
    the run): what names the lock. None, with each error printed, when the call is not valid."""
    common = git(".", "rev-parse", "--git-common-dir")
    # A first call is validated before the lock, so a wrong call makes no file. After an earlier run the lock comes
    # first: the driver that holds it changes what the validation reads.
    first_call = common.returncode or not OUT.exists()
    if first_call and not _validated(args):
        return None
    if not (pr := _run_branch()):
        print("the PR worktree is on a detached HEAD: check out the PR branch")
        return None
    if pr.startswith("ticket/"):  # a slot of another run: its driver owns the worktree and the branch
        print(f"the PR worktree is on the ticket branch {pr} of a run: check out the PR branch")
        return None
    return Path(common.stdout.strip()).resolve(), pr


def _hold(common: Path, pr: str) -> tuple[ExitStack | None, str]:
    """Take the locks of a run on `pr`: (the stack that holds them until it closes, "") or (None, why another
    driver stops this one)."""
    with ExitStack() as stack:
        lock_file = Names(pr).lock_file(common)
        if not (held := driver_state.lock(lock_file)):
            return None, (f"another driver runs on {pr}: it holds the lock {lock_file}. Wait until it ends, then "
                          "run the driver again.")
        stack.enter_context(held)
        older, error = driver_legacy.lock(common)
        if not older:
            return None, error
        stack.enter_context(older)
        return stack.pop_all(), ""


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.dry_run:  # it changes nothing, so it needs no lock
        if valid := _validated(args):
            _print_width(valid[0])
        return 0 if valid else 3
    if not (locking := _common_dir_and_branch(args)):
        return 3
    common, pr = locking
    held, error = _hold(common, pr)
    if not held:
        print(f"implement-spec: {error}")
        return 3
    with held:
        # Only what is read under the lock is trusted: no other driver changes state.json, the slots, the PR branch
        # or its ticket branches. Drivers on other PR branches touch none of them.
        if errors := _start_errors(pr):
            print("".join(f"implement-spec: {error}\n" for error in errors), end="")
            return 3
        if not (valid := _validated(args)):
            return 3
        tickets, models, kept = valid
        # The CLIs answer before the first agent, so a model that the account cannot use stops no ticket.
        if not args.runner and (errors := driver_agents.preflight(models)):
            print("".join(error + "\n" for error in errors), end="")
            return 3
        _print_width(tickets)
        return _run(tickets, models, args, kept, pr)


if __name__ == "__main__":
    sys.exit(main())
