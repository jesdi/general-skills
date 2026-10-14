"""A gated ticket tip in, a reviewed tip out: the review, the fixture corrections and the fix rounds.

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
from driver_run import OUT, Blocked, Run, during, ledger
from driver_tickets import Ticket

ROUNDS = 4


@dataclass
class Job:
    """One ticket between its gate and its merge."""
    ticket: Ticket
    slot: str
    run: Run
    values: dict[str, str]  # the pointers every prompt of the ticket gets
    round: int  # fix rounds started
    tip: str
    previous: str = ""  # the tip before the last fix; "" makes the next review a full one
    proofs: set[tuple[str, int]] = field(default_factory=set)  # mutation proofs so far, at `tip`
    again: list[str] = field(default_factory=list)  # `file:line` of each proof the last fix made due again
    corrected: bool = False  # the last step was a fixture correction

    def pointers(self, review: int) -> dict[str, str]:
        """The values of a prompt; `review` is the round whose findings the agent reads."""
        entry = self.run.state.read()["tickets"][self.ticket.number]
        findings = os.path.relpath(OUT / self.ticket.number / f"findings-{review}.json", self.slot)
        return {**self.values, "start_sha": self.tip, "from_sha": self.previous, "round": str(self.round),
                "locked": " ".join(entry["locked"]), "test_writer_sha": str(entry["test_writer_sha"] or ""),
                "findings": findings, "proofs": " ".join(self.again)}


def entry(models: dict[str, list[str]], round: int) -> str:
    """The implementer entry of a fix round: the second one from round 3, when the list has one."""
    entries = models["implementer"]
    return entries[1] if round > 2 and len(entries) > 1 else entries[0]


def _log_findings(job: Job, findings: list[dict]) -> list[dict]:
    for finding in findings:
        ledger(f"Ticket {job.ticket.number}: {finding['severity']} finding of kind {finding['kind']} from the "
               f"{finding['source']} at {finding['file']}:{finding['line']}: {finding['title']}.")
    return findings


def _run_reviewers(job: Job, cast: list[tuple[str, str]], locked: list[str]) -> list[dict]:
    """Run the (role, model entry) pairs of `cast` at the same time in the one slot. Returns their reports."""
    values, running = job.pointers(job.round - 1), [role for role, _ in cast]
    errors: dict[str, str] = {}

    def call(role: str, entry: str) -> dict:
        # The first review of a tip must give a mutation proof for each locked test.
        first_review = not job.previous and role == "reviewer"
        try:
            return job.run.agent(role, job.ticket.number, job.slot, values, entry=entry, fix_round=job.round,
                                 notes=["rereview"] if job.previous else [],
                                 check={"round": 1, "locked": locked} if first_review else {},
                                 alone=lambda: len(running) == 1)
        except Blocked as blocked:
            errors[role] = f"the {role}: {blocked}"
            return {}
        finally:
            running.remove(role)

    with ThreadPoolExecutor(len(cast)) as pool:
        reports = list(pool.map(lambda agent: call(*agent), cast))
    if errors:
        raise Blocked("; ".join(errors[role] for role, _ in cast if role in errors))
    return reports


def _review(job: Job) -> list[dict]:
    """Review `job.tip`. Returns the findings of the driver and of each reviewer."""
    run, number = job.run, job.ticket.number
    state = run.state.ticket(number, phase="review")
    found = _log_findings(job, driver_review.locked_findings(job.slot, state))
    cast = [("reviewer", "")]
    if job.ticket.risk:
        cast.append(("correctness", driver_agents.correctness_entry(run.models)))
    reports = run.read_only(number, job.slot, job.values["branch"], job.tip,
                            lambda: _run_reviewers(job, cast, state["locked"]))
    for (role, _), report in zip(cast, reports):
        found += _log_findings(job, [{**finding, "source": role} for finding in report["findings"]])
        job.proofs |= {(proof["file"], proof["line"]) for proof in report.get("mutations", [])}
    (OUT / number / f"findings-{job.round}.json").write_text(json.dumps(found, indent=1) + "\n")
    return found


def _correct_fixture(job: Job, fixture: list[dict]) -> None:
    """One test-writer correction for the fixture findings. It is outside the fix rounds."""
    run, number = job.run, job.ticket.number
    if job.corrected:
        titles = "; ".join(finding["title"] for finding in fixture)
        raise Blocked(f"the review: a fixture finding is open after the test-writer correction: {titles}")
    run.restore_locked_files(number, job.slot)
    with during("the fixture correction"):
        report = run.agent("test-writer", number, job.slot, job.pointers(job.round), notes=["correction"],
                           fix_round=job.round + 1)
    job.tip, job.previous, job.corrected = driver_slots.head(job.slot), "", True
    entry = run.state.read()["tickets"][number]
    run.state.ticket(number, tip=job.tip, **driver_review.written(job.slot, entry, report["locked"], job.tip))
    for finding in fixture:
        ledger(f"Ticket {number}: fixture correction in {finding['file']}, outside the fix rounds: "
               f"{finding['title']}: {finding['evidence']}. The test-writer corrected it at {job.tip[:7]}.")


def _fix(job: Job) -> None:
    """Run the next fix round: a fixer on the implementer's list."""
    run, number = job.run, job.ticket.number
    job.round += 1
    on = entry(run.models, job.round)
    run.state.ticket(number, phase="fix", round=job.round)
    if job.round == 3 and on != run.models["implementer"][0]:
        ledger(f"Ticket {number} round 3: escalated to {on}.")
    elif job.round == 3:
        ledger(f"Ticket {number} round 3: the implementer has no next entry, so the round stays on {on}.")
    with during(f"fix round {job.round}"):
        run.agent("fixer", number, job.slot, job.pointers(job.round - 1), entry=on, fix_round=job.round)
    before, job.tip = job.tip, driver_slots.head(job.slot)
    # No review has seen a fixture correction, so the review after a fix on a corrected tip is a full one.
    job.previous, job.corrected = "" if job.corrected else before, False
    run.state.ticket(number, tip=job.tip)
    followed = driver_review.follow(job.slot, before, job.tip, job.proofs)
    job.proofs = set(followed)
    job.again = [f"{file}:{line}" for (file, line), changed in sorted(followed.items()) if changed]


def _act_on(job: Job, found: list[dict]) -> bool:
    """Do what the findings of a review ask for: a fixture correction, a fix round, or a stop. True: none is open."""
    fixture, to_fix = driver_review.route(found)
    # A changed locked file is fixed first: a correction on top of it would make the changed file the reference.
    locked_file_changed = any(finding["source"] == "driver" for finding in found)
    if fixture and not locked_file_changed:
        _correct_fixture(job, fixture)
        if not to_fix:
            return False
    if not to_fix:
        return True
    if job.round == ROUNDS:
        titles = [f"{finding['title']} ({finding['file']}:{finding['line']})" for finding in to_fix]
        evidence = OUT / job.ticket.number / f"findings-{ROUNDS}.json"
        job.run.ask(job.ticket.number, "finding", titles, str(evidence))
        raise Blocked(f"the review: after round {ROUNDS} these findings are open: {'; '.join(titles)}")
    _fix(job)
    return False


def run(ticket: Ticket, slot: str, run: Run, values: dict[str, str]) -> None:
    """Gate, review and fix the ticket tip until a review leaves no finding open."""
    state = run.state.read()["tickets"][ticket.number]
    job = Job(ticket, slot, run, values, state["round"], state["tip"])
    while True:
        with during("the gate"):
            run.gate(ticket.number, slot, job.tip)
        with during("the review"):
            found = _review(job)
        if _act_on(job, found):
            return
