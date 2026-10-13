"""A gated ticket tip in, a reviewed tip out: the review, the fixture corrections and the fix rounds (requirements
5, 7, 9, 10 and 13). Stdlib only.

`run` is the driver's Run: its `agent`, `gated` and `ledger` do the work, so this module only decides what is next.
Reports of a ticket are `<role>-<K>.json`: K is the fix round the agent follows (reviewer, correctness) or is
(fixer). A fixture correction after the review of round K is `test-writer-<K+1>.json`.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import driver_agents
import driver_review
import driver_slots
from driver_tickets import Ticket

ROUNDS = 4


@dataclass
class Job:
    """One ticket between its gate and its merge."""
    ticket: Ticket
    slot: str
    run: object  # driver.Run
    values: dict[str, str]  # the pointers every prompt of the ticket gets
    round: int  # fix rounds started
    tip: str
    previous: str = ""  # the tip before the last fix; "" makes the next review a full one
    proofs: set[tuple[str, int]] = field(default_factory=set)  # mutation proofs so far, at `tip`
    again: list[str] = field(default_factory=list)  # `file:line` of each proof the last fix made due again
    corrected: bool = False  # the last step was a fixture correction

    def findings(self, round: int) -> str:
        """The merged findings of the review after `round`, as the slot sees the file."""
        return os.path.relpath(self.run.out / self.ticket.number / f"findings-{round}.json", self.slot)

    def pointers(self, review: int) -> dict[str, str]:
        """The values of a prompt; `review` is the round whose findings the agent reads."""
        entry = self.run.state.read()["tickets"][self.ticket.number]
        return {**self.values, "start_sha": self.tip, "from_sha": self.previous, "round": str(self.round),
                "locked": " ".join(entry["locked"]), "test_writer_sha": str(entry["test_writer_sha"] or ""),
                "findings": self.findings(review), "proofs": " ".join(self.again)}


def entry(models: dict[str, list[str]], round: int) -> str:
    """The implementer entry a fix round runs on: the first in rounds 1 and 2; in rounds 3 and 4 the next one
    whose CLI is on PATH (requirement 2 checks only the first), and the first again when there is none."""
    first, *later = models["implementer"]
    return next(filter(driver_agents.installed, later * (round > 2)), first)


def _cast(job: Job) -> tuple[list[tuple[str, str]], list[str]]:
    """((role, entry) per agent of this review, the notes the reviewer's prompt gets). A ticket with a Risk line
    gets the correctness pass: a second agent on another provider, or the reviewer itself when there is none."""
    cast, notes = [("reviewer", "")], ["rereview"] * bool(job.previous)
    if job.ticket.risk:
        other, why = driver_review.correctness_entry(job.run.models)
        if other:
            cast.append(("correctness", other))
        else:
            notes.insert(0, "folded")
            job.run.ledger(f"Ticket {job.ticket.number}: correctness pass folded into reviewer: {why}.")
    return cast, notes


def _told(job: Job, findings: list[dict]) -> list[dict]:
    """Each finding goes to the ledger with its severity: a minor one waits there for the final review."""
    for finding in findings:
        job.run.ledger(f"Ticket {job.ticket.number}: {finding['severity']} finding of kind {finding['kind']} from the "
                       f"{finding['source']} at {finding['file']}:{finding['line']}: {finding['title']}.")
    return findings


def _attempt(job: Job, cast: list[tuple[str, str]], notes: list[str], locked: list[str]) -> tuple[str, str, list[dict]]:
    """Run the agents of one review at once in the one slot: (why the ticket is blocked, what they left in the
    slot, the reports). The driver writes nothing to the tree of the slot while two agents run. When all are
    done, a slot they changed goes back to the tip: a read-only role has no work to keep."""
    run, number, values, running = job.run, job.ticket.number, job.pointers(job.round - 1), [role for role, _ in cast]

    def call(role: str, entry: str) -> tuple[str, dict]:
        mine = notes if role == "reviewer" else notes[-1:] * bool(job.previous)
        # The first review of a tip is the "round 1" of the report contract: a proof for each locked test.
        check = {} if job.previous or role != "reviewer" else {"round": 1, "locked": locked}
        check["alone"] = lambda: len(running) == 1
        try:
            return run.agent(role, number, job.slot, values, notes=mine, entry=entry, fix_round=job.round, check=check)
        finally:
            running.remove(role)

    with ThreadPoolExecutor(len(cast)) as pool:
        done = list(pool.map(lambda agent: call(*agent), cast))
    left = driver_review.disturbed(job.slot, job.values["branch"], job.tip)
    if left:
        driver_review.restore(job.slot, job.values["branch"], job.tip)
    errors = [f"the {role}: {error}" for (role, _), (error, _) in zip(cast, done) if error]
    return "; ".join(errors), left, [report for _, report in done]


def _clean(job: Job, cast: list[tuple[str, str]], notes: list[str], locked: list[str]) -> tuple[str, list[dict]]:
    """The agents of one review, until they leave the slot as it was: ("", the reports) or (why the ticket is
    blocked, []). A review that changed the slot runs one more time on the restored tip (requirements 5 and 6)."""
    for last in (False, True):
        error, left, reports = _attempt(job, cast, notes, locked)
        if left:
            job.run.ledger(f"Ticket {job.ticket.number}: a read-only role broke its rule: {left}; the driver restored "
                           f"the slot to {job.tip[:7]}{'' if error or last else ' and runs the review again'}.")
        if error or not left:
            break
    return error or left, reports


def _review(job: Job) -> tuple[str, list[dict]]:
    """Review `job.tip`: ("", the merged findings) or (why the ticket is blocked, [])."""
    run, number = job.run, job.ticket.number
    state = run.state.ticket(number, phase="review")
    found = _told(job, driver_review.locked_findings(job.slot, state))
    cast, notes = _cast(job)
    error, reports = _clean(job, cast, notes, state["locked"])
    if error:
        return error, []
    for (role, _), report in zip(cast, reports):
        found += _told(job, [{**finding, "source": role} for finding in report["findings"]])
        job.proofs |= {(proof["file"], proof["line"]) for proof in report.get("mutations", [])}
    (run.out / number / f"findings-{job.round}.json").write_text(json.dumps(found, indent=1) + "\n")
    return "", found


def _correct(job: Job, fixture: list[dict]) -> str:
    """Requirement 10: one test-writer correction for the fixture findings, outside the rounds. Returns "" or why
    the ticket is blocked. The locked-file check starts again from the corrected commit."""
    run, number = job.run, job.ticket.number
    titles = "; ".join(finding["title"] for finding in fixture)
    if job.corrected:
        return f"the review: a fixture finding is open after the test-writer correction: {titles}"
    error, report = run.agent("test-writer", number, job.slot, job.pointers(job.round), notes=["correction"], fix_round=job.round + 1)
    if error:
        return f"the fixture correction: {error}"
    job.tip, job.previous, job.corrected = driver_slots.head(job.slot), "", True
    entry = run.state.read()["tickets"][number]
    run.state.ticket(number, tip=job.tip, **driver_review.written(job.slot, entry, report["locked"], job.tip))
    for finding in fixture:
        run.ledger(f"Ticket {number}: fixture correction in {finding['file']}, outside the fix rounds: "
                   f"{finding['title']}: {finding['evidence']}. The test-writer corrected it at {job.tip[:7]}.")
    return ""


def _fix(job: Job) -> str:
    """Start the next fix round: a fixer on the implementer's list. Returns "" or why the ticket is blocked."""
    run, number = job.run, job.ticket.number
    job.round += 1
    on = entry(run.models, job.round)
    run.state.ticket(number, phase="fix", round=job.round)
    if job.round == 3:
        run.ledger(f"Ticket {number} round 3: escalated to {on}." if on != run.models["implementer"][0] else
                   f"Ticket {number} round 3: no next implementer entry has its CLI on PATH, so the round stays on {on}.")
    error, _ = run.agent("fixer", number, job.slot, job.pointers(job.round - 1), entry=on, fix_round=job.round)
    if error:
        return f"fix round {job.round}: {error}"
    # A round that starts on a corrected tip is followed by a full review: no review has seen the correction.
    before, job.tip = job.tip, driver_slots.head(job.slot)
    job.previous, job.corrected = "" if job.corrected else before, False
    run.state.ticket(number, tip=job.tip)
    followed = driver_review.follow(job.slot, before, job.tip, job.proofs)
    job.proofs = set(followed)
    job.again = [f"{file}:{line}" for (file, line), changed in sorted(followed.items()) if changed]
    return ""


def _after(job: Job, found: list[dict]) -> str | None:
    """What the findings of a review ask for. None: the review has passed. "": a correction or a fix round ran, so
    the new tip gets its gate and its review. Else where and why the ticket is blocked."""
    fixture, todo = driver_review.route(found)
    # A changed locked file is fixed first: a correction would make the changed file the new reference.
    if fixture and not any(finding["source"] == "driver" for finding in found):
        # The other open findings of this review stay open: their fix round starts on the corrected tip.
        if (error := _correct(job, fixture)) or not todo:
            return error
    if not todo:
        return None
    if job.round == ROUNDS:
        titles = [f"{finding['title']} ({finding['file']}:{finding['line']})" for finding in todo]
        # Requirement 17: the ticket is BLOCKED, and the operator rules on each open finding.
        job.run.ask(job.ticket.number, "finding", titles, str(job.run.out / job.ticket.number / f"findings-{ROUNDS}.json"))
        return f"the review: after round {ROUNDS} these findings are open: {'; '.join(titles)}"
    return _fix(job)


def run(ticket: Ticket, slot: str, run, values: dict[str, str]) -> str:
    """Gate, review, and fix until the review has no open finding. Returns "" when the tip the state records has
    passed, or where and why the ticket is blocked. Each round ends with one gate record on its tip."""
    state = run.state.read()["tickets"][ticket.number]
    job = Job(ticket, slot, run, values, state["round"], state["tip"])
    while True:
        if red := run.gated(ticket.number, slot, job.tip):
            return f"the gate: {red}"
        error, found = _review(job)
        if error:
            return f"the review: {error}"
        if (error := _after(job, found)) != "":
            return error or ""
