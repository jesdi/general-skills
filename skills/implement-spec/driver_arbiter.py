"""The arbiter: when the driver's own retries are spent, one read-only agent rules on each open question that
driver_decisions.arbitrated names (a final finding, or a ticket's spec-ambiguous question), on another provider than
the implementer, with a reason and the cost if it is wrong. An arbiter that cannot rule leaves the questions to the
operator.

The arbiter runs in a checkout of its own, SCRATCH, on a detached HEAD at the PR tip: slot 1 may hold the uncommitted
work of the ticket it rules on, which the restore after a read-only role would wipe."""
from __future__ import annotations

import os

import driver_agents
import driver_decisions
from driver_run import FINAL, OUT, Blocked, Call, Run, ledger
from driver_slots import git

SCRATCH = ".agent/worktrees/arbiter"


def _here(path: str) -> str:
    """`path`, which the PR worktree reads, as the arbiter reads it from SCRATCH. Keeps a `:line` suffix."""
    return os.path.relpath(path, SCRATCH)


def _values(run: Run, asked: list[dict]) -> dict[str, str]:
    return {"spec": _here(run.args.spec), "tickets": _here(run.args.tickets), "pr_branch": run.pr, "branch": "",
            "start_sha": run.tip(), "ledger": _here(str(OUT / "ledger.md")),
            "questions": "".join(_line(run, q) for q in asked)}


def _line(run: Run, q: dict) -> str:
    """One question for the arbiter: its options, its evidence, and for a ticket the branch its work is on."""
    branch = f" Ticket branch: {run.names.ticket(q['ticket'])}." if q["ticket"] != FINAL else ""
    return (f"- {q['id']}: {q['title']}. Options: {', '.join(driver_decisions.KINDS[q['kind']].arbiter)}.{branch} "
            f"Evidence: {', '.join(map(_here, q['evidence']))}\n")


def _why(ruling: dict) -> str:
    """The end of the ruling sentence: the reason of the arbiter and the cost if it is wrong."""
    return f": {ruling['reason'].rstrip('.')}. Cost if wrong: {ruling['cost'].rstrip('.')}"


def rule(run: Run) -> tuple[dict[str, str], dict[str, str]]:
    """(question id -> the arbiter's option, question id -> the end of its ruling sentence); both {} when no question
    is the arbiter's or the arbiter could not rule."""
    asked = driver_decisions.arbitrated(run.state.read()["run"]["questions"], run.args.retries)
    if not asked:
        return {}, {}
    entries = driver_agents.preferred_entries(run.models, "arbiter")
    call = Call("arbiter", FINAL, SCRATCH, _values(run, asked), entry=entries[0], entries=entries, asks=False,
                name=f"arbiter-{asked[0]['stop']}",
                check={"questions": {q["id"]: driver_decisions.KINDS[q["kind"]].arbiter for q in asked}})
    try:
        report = _in_scratch(run, call)
    except Blocked as blocked:
        ledger(f"{driver_decisions.owner(asked[0]['ticket'])}: the arbiter could not rule, so the operator decides: "
               f"{blocked}.")
        return {}, {}
    return _answers(report["rulings"], call.check["questions"])


def _in_scratch(run: Run, call: Call) -> dict:
    """Run the arbiter in SCRATCH, made for it at its start commit and removed after it."""
    tip = call.values["start_sha"]
    if error := _remove_scratch():  # one that an interrupted run left
        raise Blocked(error)
    added = git(".", "worktree", "add", "-q", "--force", "--detach", SCRATCH, tip)
    if added.returncode:
        raise Blocked(f"cannot create {SCRATCH}: {added.stderr.strip()}")
    try:
        return run.read_only(FINAL, SCRATCH, "", tip, lambda: run.agent(call))
    finally:
        if error := _remove_scratch():
            ledger(f"End of run: {error}.")


def _remove_scratch() -> str:
    """Remove SCRATCH through Git alone, so a locked worktree or a directory that is no worktree there loses nothing.
    "" or why it stays."""
    if not os.path.lexists(SCRATCH):
        return ""
    removed = git(".", "worktree", "remove", "--force", SCRATCH)
    return f"cannot remove {SCRATCH}: {removed.stderr.strip()}" if removed.returncode else ""


def _answers(rulings: list[dict], asked: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """The rulings on the questions in `asked`, as `rule` returns them."""
    rulings = [one for one in rulings if one["question"] in asked]
    return {one["question"]: one["ruling"] for one in rulings}, {one["question"]: _why(one) for one in rulings}
